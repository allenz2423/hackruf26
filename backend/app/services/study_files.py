"""Typeset saved guides from validated content, never model-supplied TeX programs."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.api.schemas import StudyGuideContent, UploadDocumentContent
from app.config import REPOSITORY_ROOT, Settings

_COMPILATION_LOCK = threading.Semaphore(1)
_MAX_FILE_BYTES = 20 * 1024 * 1024
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_MATH_COMMANDS = set(
    """
frac dfrac tfrac sqrt sum prod int iint lim sin cos tan log ln exp min max
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
lambda mu nu xi pi varpi rho sigma tau upsilon phi varphi chi psi omega
 circ
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
times cdot div pm mp le leq ge geq ne neq approx equiv sim simeq propto
in notin subset subseteq supset supseteq cup cap emptyset infty partial nabla
to rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow
forall exists neg land lor lnot
left right big Big langle rangle lvert rvert vert Vert
mathrm mathbf mathit mathcal mathbb text operatorname overline underline
hat bar vec dot ddot quad qquad
""".split()
)
_MATH_PATTERN = re.compile(
    r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$([^$\n]+?)\$"
)


class StudyFileFailure(RuntimeError):
    """Guide typesetting failed; callers expose a bounded, sanitized error."""


def escape_latex(value: str) -> str:
    return "".join(
        _ESCAPES.get(char, char)
        for char in str(value)
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )


def _safe_math(value: str) -> bool:
    """Allow only notation and a fixed set of math macros, without TeX IO or definitions."""
    if not value.strip() or len(value) > 4000 or "^^" in value:
        return False
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            match = re.match(r"\\([A-Za-z]+|[ ,;!{}|])", value[index:])
            if not match or (match[1].isalpha() and match[1] not in _MATH_COMMANDS):
                return False
            index += len(match[0])
            continue
        if depth == 0 and char.isascii() and char.isalpha():
            word = re.match(r"[A-Za-z]+", value[index:])
            assert word is not None
            if len(word[0]) >= 4:
                return False
            index += len(word[0])
            continue
        if char in "$%#&~" or (unicodedata.category(char).startswith("C") and char not in "\n\t"):
            return False
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if not 0 <= depth <= 40:
            return False
        index += 1
    return depth == 0


def _inline(value: str, render_math: bool = True) -> str:
    if not render_math:
        return escape_latex(value)
    parts = []
    cursor = 0
    while match := _MATH_PATTERN.search(value, cursor):
        parts.append(escape_latex(value[cursor : match.start()]))
        math = next(group for group in match.groups() if group is not None)
        if _safe_math(math):
            display = match[1] is not None or match[2] is not None
            parts.append((r"\[" if display else r"\(") + math + (r"\]" if display else r"\)"))
            cursor = match.end()
        elif match[0].startswith("$") and not match[0].startswith("$$"):
            # Leave this opening marker as text and rescan from the next dollar.
            # That next marker may open a valid equation after a truncated one.
            parts.append(escape_latex("$"))
            cursor = match.start() + 1
        else:
            parts.append(escape_latex(match[0]))
            cursor = match.end()
    parts.append(escape_latex(value[cursor:]))
    return "".join(parts)


def _body(value: str, render_math: bool = True) -> str:
    lines = []
    prose = []
    in_list = False

    def flush_prose() -> None:
        if prose:
            lines.append(_inline("\n".join(prose), render_math))
            prose.clear()

    for line in value.splitlines():
        item = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.+)$", line)
        if item:
            flush_prose()
            if not in_list:
                lines.append(r"\begin{itemize}")
                in_list = True
            lines.append(r"\item " + _inline(item[1], render_math))
        else:
            if in_list:
                lines.append(r"\end{itemize}")
                in_list = False
            heading = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading:
                flush_prose()
                lines.append(r"\paragraph{" + escape_latex(heading[1]) + "}")
            else:
                prose.append(line)
    if in_list:
        lines.append(r"\end{itemize}")
    flush_prose()
    return "\n".join(lines)


def render_guide_latex(
    *,
    title: str,
    class_name: str,
    material_id: UUID,
    content: dict,
    sources: list[dict],
    original_text: str | None = None,
    render_math: bool = True,
) -> str:
    guide = (
        UploadDocumentContent if content.get("format") == "upload_document" else StudyGuideContent
    ).model_validate(content)
    source_map = {
        str(source["id"]): (index, source["title"]) for index, source in enumerate(sources, 1)
    }
    document = [
        r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,fontspec,unicode-math}
\setmainfont{DejaVuSerif.ttf}[BoldFont=DejaVuSerif-Bold.ttf,ItalicFont=DejaVuSerif-Italic.ttf,BoldItalicFont=DejaVuSerif-BoldItalic.ttf]
\usepackage[hidelinks]{hyperref}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.6em}
\emergencystretch=3em
\begin{document}
""",
        r"\title{" + escape_latex(title) + "}",
        r"\author{" + escape_latex(class_name) + "}",
        r"\date{" + datetime.now(UTC).strftime("%Y-%m-%d") + "}",
        r"\maketitle",
        r"\noindent AI-generated study document. Check the cited course sources when reviewing.",
        r"\par Reference ID: \texttt{" + escape_latex(str(material_id)) + "}",
        r"\tableofcontents",
        r"\newpage",
    ]
    for section in guide.sections:
        document.extend(
            [r"\section{" + escape_latex(section.heading) + "}", _body(section.body, render_math)]
        )
        if section.key_terms:
            document.append(
                r"\textbf{Key terms:} "
                + "; ".join(_inline(term, render_math) for term in section.key_terms)
            )
        citations = []
        for citation in section.sources:
            entry = source_map.get(str(citation.upload_id))
            if entry is None:
                raise StudyFileFailure("Invalid source reference.")
            number, _ = entry
            locator = []
            if citation.page is not None:
                locator.append(f"p. {citation.page}")
            if citation.slide is not None:
                locator.append(f"slide {citation.slide}")
            if citation.timestamp_seconds is not None:
                seconds = int(citation.timestamp_seconds)
                locator.append(f"{seconds // 60}:{seconds % 60:02d}")
            label = f"[{number}]" + (", " + ", ".join(locator) if locator else "")
            citations.append(r"\hyperref[source:" + str(number) + "]{" + escape_latex(label) + "}")
        document.append(r"\par\textbf{Sources:} " + "; ".join(citations))
    document.extend([r"\newpage", r"\section*{Source references}", r"\begin{description}"])
    for source_id, (number, name) in source_map.items():
        document.extend(
            [
                r"\item[{[" + str(number) + r"]}]\phantomsection\label{source:" + str(number) + "}",
                escape_latex(name),
                r"\par\small Upload: \texttt{" + escape_latex(source_id) + "}",
            ]
        )
    document.append(r"\end{description}")
    if original_text is not None:
        document.extend(
            [
                r"\newpage",
                r"\appendix",
                r"\section{Original extracted source text}",
                "The following text is reproduced from the upload extraction, without AI "
                "rewriting. "
                "Extraction can miss image details or misread scans; the original "
                "uploaded file remains available.",
                r"\begingroup\small\raggedright\setlength{\parskip}{0pt}",
            ]
        )
        document.extend(escape_latex(line) + r"\par" for line in original_text.splitlines())
        document.append(r"\endgroup")
    document.append(r"\end{document}")
    return "\n".join(document) + "\n"


def compiler_path(settings: Settings) -> str | None:
    configured = settings.study_pdf_executable
    executable = shutil.which(configured)
    if executable:
        return executable
    path = Path(configured)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    if path.is_file():
        return str(path.resolve())
    if configured == "tectonic":
        local = REPOSITORY_ROOT / "backend/.local/bin/tectonic"
        if local.is_file():
            return str(local)
    return None


def compile_guide_pdf(latex: str, settings: Settings, *, only_cached: bool = True) -> bytes:
    executable = compiler_path(settings)
    if not executable:
        raise StudyFileFailure("Install Tectonic to create study-guide PDFs.")
    _COMPILATION_LOCK.acquire()
    try:
        with tempfile.TemporaryDirectory(prefix="screen-tutor-guide-") as directory:
            source = Path(directory) / "guide.tex"
            source.write_text(latex, encoding="utf-8")
            command = [executable, "--untrusted", "--chatter", "minimal", "--outdir", directory]
            if only_cached:
                command.append("--only-cached")
            command.append(str(source))
            if shutil.which("prlimit"):
                command = ["prlimit", f"--fsize={_MAX_FILE_BYTES}", "--", *command]
            result = subprocess.run(
                command,
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            pdf = Path(directory) / "guide.pdf"
            if result.returncode != 0 or not pdf.is_file() or pdf.stat().st_size > _MAX_FILE_BYTES:
                raise StudyFileFailure("Study-guide PDF compilation failed.")
            data = pdf.read_bytes()
            if not data.startswith(b"%PDF-"):
                raise StudyFileFailure("Study-guide PDF compilation failed.")
            return data
    except OSError as exc:
        raise StudyFileFailure("Study-guide PDF compilation failed.") from exc
    finally:
        _COMPILATION_LOCK.release()


def create_guide_files(settings: Settings, **guide: object) -> dict[str, bytes]:
    latex = render_guide_latex(**guide)
    try:
        pdf = compile_guide_pdf(latex, settings)
    except StudyFileFailure:
        # A malformed model equation must not discard otherwise valid study content.
        latex = render_guide_latex(**guide, render_math=False)
        pdf = compile_guide_pdf(latex, settings)
    return {"latex": latex.encode("utf-8"), "pdf": pdf}
