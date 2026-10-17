"""Typeset saved guides from validated content, never model-supplied TeX programs."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.api.schemas import (
    StudyGuideBlock,
    StudyGuideContent,
    StudyGuideSectionV2,
    UploadDocumentContent,
)
from app.config import REPOSITORY_ROOT, Settings
from app.services.guide_templates import (
    ANIMAL_CELL_LIMITATION,
    ANIMAL_CELL_ORGANELLES,
    MOLECULE_3D_CATALOG,
    MOLECULES,
    animal_cell_map_alt_text,
    binary_search_trace,
    _graph_traversal,
    _stress_state,
    guide_block_alt_text,
    guide_block_fallback,
    _python_algorithm_trace,
    _simcir_truth_table,
    sampling_demo_result,
)
from app.services.study_guide_visual_preview import (
    MAX_PREVIEW_PNG_BYTES,
    SvgPreviewRenderError,
    render_svg_preview_png,
)

_COMPILATION_LOCK = threading.Semaphore(1)
_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_GUIDE_DIAGRAMS = 32
_MAX_GUIDE_DIAGRAM_BYTES = 16 * 1024 * 1024
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
_UNSAFE_MATH_COMMANDS = {
    # File access, output, shell, document structure, and macro definitions are not
    # mathematical notation. Tectonic also runs in --untrusted mode at the boundary.
    "input", "include", "includeonly", "endinput", "openin", "closein", "read",
    "openout", "closeout", "write", "immediate", "special", "catcode", "csname",
    "endcsname", "def", "edef", "xdef", "gdef", "let", "futurelet", "newcommand",
    "renewcommand", "providecommand", "DeclareMathOperator", "usepackage", "RequirePackage",
    "documentclass", "LoadClass", "directlua", "luaexec", "scantokens", "loop", "repeat",
    "newread", "newwrite", "newcount", "newdimen", "newskip", "newmuskip", "newtoks",
    "newif", "newenvironment", "renewenvironment", "shipout", "output", "message",
    "errmessage", "aftergroup", "afterassignment", "everyjob", "everypar", "everymath",
    "everydisplay", "global", "long", "outer", "verb", "href", "url", "includegraphics",
    "makeatletter", "makeatother",
}
_MATH_PATTERN = re.compile(
    r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$([^$\n]+?)\$"
)
_UNICODE_MATH_TEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ϵ": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta",
    "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi",
    "ϖ": r"\varpi", "ρ": r"\rho", "σ": r"\sigma", "ς": r"\varsigma",
    "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi", "ϕ": r"\varphi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma",
    "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi",
    "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega", "∂": r"\partial", "∇": r"\nabla",
    "∞": r"\infty", "∝": r"\propto", "≤": r"\leq", "≥": r"\geq",
    "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv", "×": r"\times",
    "÷": r"\div", "·": r"\cdot", "±": r"\pm", "∓": r"\mp", "−": "-",
    "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "∫": r"\int", "∑": r"\sum", "√": r"\sqrt", "∈": r"\in", "∉": r"\notin",
    "⊥": r"\perp", "∥": r"\parallel", "∠": r"\angle", "∅": r"\emptyset",
    "∩": r"\cap", "∪": r"\cup", "⊂": r"\subset", "⊆": r"\subseteq",
    "∧": r"\land", "∨": r"\lor", "¬": r"\lnot", "∀": r"\forall",
    "∃": r"\exists", "∴": r"\therefore", "∵": r"\because",
    "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "°": r"^{\circ}",
}
_UNICODE_SUBSCRIPT = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
    "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
    "₌": "=", "₍": "(", "₎": ")", "ₐ": "a", "ₑ": "e", "ₕ": "h",
    "ᵢ": "i", "ₖ": "k", "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o",
    "ₚ": "p", "ᵣ": "r", "ₛ": "s", "ₜ": "t", "ᵤ": "u", "ᵥ": "v",
    "ₓ": "x", "ᵧ": "y",
}
_UNICODE_SUPERSCRIPT = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
    "⁼": "=", "⁽": "(", "⁾": ")", "ᵃ": "a", "ᵇ": "b", "ᶜ": "c",
    "ᵈ": "d", "ᵉ": "e", "ᶠ": "f", "ᵍ": "g", "ʰ": "h", "ⁱ": "i",
    "ʲ": "j", "ᵏ": "k", "ˡ": "l", "ᵐ": "m", "ⁿ": "n", "ᵒ": "o",
    "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u", "ᵛ": "v",
    "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z", "ᵧ": "y",
}
_MATH_ENTITIES = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Sigma": "Σ", "Omega": "Ω", "infin": "∞", "plusmn": "±",
    "minus": "−", "times": "×", "divide": "÷", "middot": "·", "le": "≤",
    "ge": "≥", "ne": "≠", "equiv": "≡", "approx": "≈", "radic": "√", "sum": "∑",
    "int": "∫", "part": "∂", "nabla": "∇", "rarr": "→", "larr": "←", "perp": "⊥",
    "parallel": "∥",
}
_MATH_WORDS = {"sin", "cos", "tan", "cot", "log", "ln", "exp", "lim", "max", "min"}
_PROSE_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could",
    "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "how",
    "if", "in", "into", "is", "it", "its", "may", "might", "must", "no", "nor", "not",
    "of", "on", "or", "our", "she", "should", "so", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "would", "you", "your",
}
_MATH_ENVIRONMENTS = {
    "matrix",
    "pmatrix",
    "bmatrix",
    "Bmatrix",
    "vmatrix",
    "Vmatrix",
    "smallmatrix",
    "cases",
    "aligned",
    "alignedat",
    "gathered",
    "split",
    "array",
    "subarray",
}
_BARE_MATH_ENVIRONMENTS = {
    "math", "equation", "equation*", "displaymath", "align", "align*", "gather", "gather*",
    "multline", "multline*", "flalign", "flalign*", "alignat", "alignat*", *_MATH_ENVIRONMENTS,
}
_MATH_ALIGNMENT_ENVIRONMENTS = _MATH_ENVIRONMENTS - {"gathered"}
_MATH_DELIMITER_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "|": "|",
    ".": ".",
    "langle": "rangle",
    "lvert": "rvert",
    "vert": "vert",
    "Vert": "Vert",
}
_MATH_DELIMITER_PATTERN = re.compile(r"(?<!\\)\$|\\(?:\[|\]|\(|\))")
_MATH_FENCE_PATTERN = re.compile(
    r"(?m)^```([A-Za-z]*)[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*(?=\r?\n|$)"
)
_INLINE_CODE_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")


class StudyFileFailure(RuntimeError):
    """Guide typesetting failed; callers expose a bounded, sanitized error."""


def escape_latex(value: str) -> str:
    return "".join(
        _ESCAPES.get(char, char)
        for char in str(value)
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )


def _safe_math(value: str) -> bool:
    """Accept bounded math syntax while rejecting TeX I/O and document-control commands."""
    if not value.strip() or len(value) > 4000 or "^^" in value:
        return False
    depth = 0
    environments: list[tuple[str, int]] = []
    delimiters: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            environment = re.match(r"\\(begin|end)\{([A-Za-z]+)\}", value[index:])
            if environment:
                action, name = environment.groups()
                if name not in _MATH_ENVIRONMENTS:
                    return False
                if action == "begin":
                    if len(environments) >= 8:
                        return False
                    environments.append((name, depth))
                    index += len(environment[0])
                    if name == "array":
                        columns = re.match(r"\s*\{([clr|:]{1,20})\}", value[index:])
                        if not columns:
                            return False
                        index += len(columns[0])
                    continue
                if not environments or environments[-1][0] != name:
                    return False
                environments.pop()
                index += len(environment[0])
                continue
            if value.startswith("\\\\", index):
                if (
                    depth != (environments[-1][1] if environments else -1)
                    or not environments
                    or environments[-1][0] not in _MATH_ENVIRONMENTS
                ):
                    return False
                index += 2
                continue
            match = re.match(r"\\([A-Za-z]+|[\\ ,;!{}|])", value[index:])
            if not match:
                return False
            command = match[1]
            if command in {"left", "right"}:
                delimiter_index = index + len(match[0])
                delimiter_match = re.match(r"\\([A-Za-z]+|[{}|])", value[delimiter_index:])
                if delimiter_match:
                    delimiter = delimiter_match[1]
                    delimiter_index += len(delimiter_match[0])
                elif delimiter_index < len(value) and value[delimiter_index] in "()[]|.":
                    delimiter = value[delimiter_index]
                    delimiter_index += 1
                else:
                    return False
                if delimiter not in _MATH_DELIMITER_PAIRS:
                    return False
                if command == "left":
                    delimiters.append(_MATH_DELIMITER_PAIRS[delimiter])
                    if len(delimiters) > 20:
                        return False
                else:
                    if not delimiters or delimiters.pop() != delimiter:
                        return False
                index = delimiter_index
                continue
            if command.isalpha() and command in _UNSAFE_MATH_COMMANDS:
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
        if char in "$%#~" or (unicodedata.category(char).startswith("C") and char not in "\n\t"):
            return False
        if char == "&" and (
            not environments
            or environments[-1][0] not in _MATH_ALIGNMENT_ENVIRONMENTS
            or depth != environments[-1][1]
        ):
            return False
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if not 0 <= depth <= 40:
            return False
        index += 1
    return depth == 0 and not environments and not delimiters


def _balanced_group_end(value: str, start: int) -> int:
    if start >= len(value) or value[start] != "{":
        return -1
    depth = 0
    index = start
    while index < len(value):
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _bare_command_end(value: str, start: int, nesting: int = 0) -> int:
    if nesting > 8:
        return -1
    command = re.match(r"\\[A-Za-z]+", value[start:])
    if not command:
        return -1
    index = start + len(command[0])
    while index < len(value):
        if value[index] in "_^":
            argument_start = index + 1
            if argument_start < len(value) and value[argument_start] == "{":
                group_end = _balanced_group_end(value, argument_start)
                if group_end == -1:
                    break
                index = group_end
            elif argument_start < len(value) and value[argument_start] == "\\":
                command_end = _bare_command_end(value, argument_start, nesting + 1)
                if command_end == -1:
                    break
                index = command_end
            else:
                argument = re.match(r"[A-Za-z0-9]+", value[argument_start:])
                if not argument:
                    break
                index = argument_start + len(argument[0])
        elif value[index] == "{":
            group_end = _balanced_group_end(value, index)
            if group_end == -1:
                break
            index = group_end
        else:
            break
    return index


def _normalize_text_argument(command: str, inner: str) -> str:
    if not any(char in _UNICODE_SUBSCRIPT or char in _UNICODE_SUPERSCRIPT for char in inner):
        return command + "{" + inner + "}"

    pieces: list[str] = []
    text_start = 0
    cursor = 0
    while cursor < len(inner):
        char = inner[cursor]
        script_map = (
            _UNICODE_SUBSCRIPT
            if char in _UNICODE_SUBSCRIPT
            else _UNICODE_SUPERSCRIPT
            if char in _UNICODE_SUPERSCRIPT
            else None
        )
        if script_map is None:
            cursor += 1
            continue
        if cursor > text_start:
            pieces.append(command + "{" + inner[text_start:cursor] + "}")
        script = ""
        while cursor < len(inner) and inner[cursor] in script_map:
            script += script_map[inner[cursor]]
            cursor += 1
        pieces.append(("_" if script_map is _UNICODE_SUBSCRIPT else "^") + "{" + script + "}")
        text_start = cursor
    if text_start < len(inner):
        pieces.append(command + "{" + inner[text_start:] + "}")
    return "".join(pieces)


def _normalize_bare_math(value: str) -> str:
    text_commands = {"text", "textrm", "textsf", "texttt", "textbf", "textit", "mbox"}

    def normalize_segment(segment: str) -> str:
        normalized = "".join(_UNICODE_MATH_TEX.get(char, char) for char in segment)
        normalized = re.sub(
            f"[{re.escape(''.join(_UNICODE_SUBSCRIPT))}]+",
            lambda match: "_{" + "".join(_UNICODE_SUBSCRIPT[char] for char in match[0]) + "}",
            normalized,
        )
        return re.sub(
            f"[{re.escape(''.join(_UNICODE_SUPERSCRIPT))}]+",
            lambda match: "^{" + "".join(_UNICODE_SUPERSCRIPT[char] for char in match[0]) + "}",
            normalized,
        )

    pieces: list[str] = []
    cursor = 0
    segment_start = 0
    while cursor < len(value):
        command = re.match(r"\\([A-Za-z]+)", value[cursor:]) if value[cursor] == "\\" else None
        if command and command[1] in text_commands:
            group_start = cursor + len(command[0])
            group_end = _balanced_group_end(value, group_start)
            if group_end != -1:
                inner = value[group_start + 1 : group_end - 1]
                pieces.append(normalize_segment(value[segment_start:cursor]))
                pieces.append(_normalize_text_argument(command[0], inner))
                cursor = group_end
                segment_start = cursor
                continue
        cursor += 1
    pieces.append(normalize_segment(value[segment_start:]))
    return "".join(pieces)


def _decode_math_entities(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        entity = match[1]
        symbol = _MATH_ENTITIES.get(entity)
        if not symbol and entity.startswith("#"):
            try:
                codepoint = int(entity[2:], 16) if entity[1:2].lower() == "x" else int(entity[1:])
                if 0 <= codepoint <= 0x10FFFF:
                    symbol = chr(codepoint)
            except ValueError:
                return match[0]
        if (
            symbol in _UNICODE_MATH_TEX
            or symbol in _UNICODE_SUBSCRIPT
            or symbol in _UNICODE_SUPERSCRIPT
        ):
            return symbol
        return match[0]

    return re.sub(
        r"&(#(?:x[\da-f]+|\d+)|[A-Za-z][A-Za-z\d]+);",
        replace,
        value,
        flags=re.IGNORECASE,
    )


def _bare_script_end(value: str, start: int) -> int:
    base = re.match(r"[A-Za-z0-9\u0370-\u03ff]+", value[start:])
    if not base:
        return -1
    index = start + len(base[0])
    has_script = False
    while index < len(value):
        if value[index] in "_^":
            argument_start = index + 1
            if argument_start < len(value) and value[argument_start] == "{":
                group_end = _balanced_group_end(value, argument_start)
                if group_end == -1:
                    break
                index = group_end
            elif argument_start < len(value) and value[argument_start] == "\\":
                command_end = _bare_command_end(value, argument_start)
                if command_end == -1:
                    break
                index = command_end
            else:
                argument = re.match(r"[A-Za-z0-9\u0370-\u03ff]+", value[argument_start:])
                if not argument:
                    break
                index = argument_start + len(argument[0])
            has_script = True
        elif value[index] in _UNICODE_SUBSCRIPT or value[index] in _UNICODE_SUPERSCRIPT:
            while index < len(value) and (
                value[index] in _UNICODE_SUBSCRIPT or value[index] in _UNICODE_SUPERSCRIPT
            ):
                index += 1
            has_script = True
        elif value[index] in "'′°":
            index += 1
            has_script = True
        else:
            break
    return index if has_script else -1


def _bare_equation_end(value: str, start: int) -> int:
    if start >= len(value) or not re.match(r"[A-Za-z0-9\u0370-\u03ff\\∂∇∫∑√]", value[start]):
        return -1
    if start and re.match(r"[A-Za-z0-9_]", value[start - 1]):
        return -1
    index = start
    has_operator = False
    has_value = False
    while index < len(value):
        char = value[index]
        if char == "\n":
            break
        if char == "\\":
            command_end = _bare_command_end(value, index)
            if command_end == -1:
                break
            index = command_end
            has_value = True
            continue
        token = re.match(r"[A-Za-z0-9\u0370-\u03ff]+", value[index:])
        if token:
            word = token[0].casefold()
            if word in _PROSE_WORDS or (len(token[0]) > 3 and word not in _MATH_WORDS):
                break
            index += len(token[0])
            has_value = True
            continue
        if char in _UNICODE_SUBSCRIPT or char in _UNICODE_SUPERSCRIPT:
            index += 1
            has_value = True
            continue
        if (
            char.isspace()
            or char in "{}_^.,'′+-−=*/×÷<>≤≥≠≈≡∝()[]|:∂∇∫∑√∞"
            or char in _UNICODE_MATH_TEX
        ):
            if char in "=+-−=*/×÷<>≤≥≠≈≡∝":
                has_operator = True
            index += 1
            continue
        break
    candidate = value[start:index].strip().rstrip(".,;:")
    if not has_operator or not has_value or len(candidate) < 3:
        return -1
    return start + len(candidate)


def _normalize_math_environment(value: str) -> tuple[str, bool]:
    match = re.fullmatch(r"\s*\\begin\{([A-Za-z]+\*?)\}([\s\S]*?)\\end\{\1\}\s*", value)
    if not match:
        return value, False
    name, body = match.groups()
    replacements = {
        "math": None, "equation": None, "equation*": None, "displaymath": None,
        "align": "aligned", "align*": "aligned", "flalign": "aligned", "flalign*": "aligned",
        "alignat": "alignedat", "alignat*": "alignedat",
        "gather": "gathered", "gather*": "gathered",
        "multline": "gathered", "multline*": "gathered",
    }
    replacement = replacements.get(name, name)
    if replacement is None:
        return body, name != "math"
    return rf"\begin{{{replacement}}}{body}\end{{{replacement}}}", True


def _bare_environment(value: str, start: int) -> tuple[int, str, bool] | None:
    opening = re.match(r"\\begin\{([A-Za-z]+\*?)\}", value[start:])
    if not opening or opening[1] not in _BARE_MATH_ENVIRONMENTS:
        return None
    name = opening[1]
    close = rf"\end{{{name}}}"
    content_start = start + len(opening[0])
    close_index = value.find(close, content_start)
    if close_index == -1:
        return None
    end = close_index + len(close)
    tex, display = _normalize_math_environment(value[start:end])
    return end, tex, display or name != "math"


def _strip_math_delimiters(value: str) -> str:
    trimmed = str(value or "").strip()
    for pattern in (
        r"\$\$([\s\S]*?)\$\$",
        r"\$([\s\S]*?)\$",
        r"\\\[([\s\S]*?)\\\]",
        r"\\\(([\s\S]*?)\\\)",
    ):
        match = re.fullmatch(pattern, trimmed)
        if match:
            return match[1].strip()
    return trimmed


def _looks_like_math_source(value: str) -> bool:
    return bool(
        re.search(
            r"\\[A-Za-z]+|[_^]\s*(?:\{|[A-Za-z0-9\\])|[=+*/<>≤≥≠≈±∓×÷∑∫√∞∂∇]|[₀-₉⁰-⁹]",
            value,
        )
    )


def _normalize_math_code(value: str) -> str:
    def fence(match: re.Match[str]) -> str:
        language, body = match.groups()
        language = language.lower()
        if language and language not in {"math", "latex", "tex", "equation"}:
            return match[0]
        if not language and not _looks_like_math_source(body):
            return match[0]
        return "$$" + _strip_math_delimiters(body) + "$$"

    value = _MATH_FENCE_PATTERN.sub(fence, value)

    def inline_code(match: re.Match[str]) -> str:
        body = match[1]
        if not _looks_like_math_source(body):
            return match[0]
        return r"\(" + _strip_math_delimiters(body) + r"\)"

    return _INLINE_CODE_PATTERN.sub(inline_code, value)


def _display_math(value: str, render_math: bool = True, strict_math: bool = False) -> str:
    if not render_math:
        return escape_latex(value)
    expression = _strip_math_delimiters(value)
    fence = _MATH_FENCE_PATTERN.fullmatch(expression)
    if fence:
        language, body = fence.groups()
        if language.lower() in {"", "math", "latex", "tex", "equation"}:
            expression = _strip_math_delimiters(body)
    _, environment_display = _normalize_math_environment(expression)
    if environment_display:
        return _inline(expression, strict_math=strict_math)
    return _inline("$$" + expression + "$$", strict_math=strict_math)


def _inline_bare(value: str, strict_math: bool = False) -> str:
    parts: list[str] = []
    cursor = 0
    text_start = 0
    while cursor < len(value):
        display = False
        if value.startswith(r"\begin{", cursor):
            environment = _bare_environment(value, cursor)
            if environment:
                end, tex, display = environment
            else:
                end, tex = -1, ""
        elif (
            value.startswith("\\\\", cursor)
            and cursor + 2 < len(value)
            and value[cursor + 2].isalpha()
        ):
            end = _bare_equation_end(value, cursor + 1)
            if end == -1:
                end = _bare_command_end(value, cursor + 1)
            tex = value[cursor + 1 : end] if end != -1 else ""
        elif value[cursor] == "\\":
            end = _bare_equation_end(value, cursor)
            if end == -1:
                end = _bare_command_end(value, cursor)
            tex = value[cursor:end] if end != -1 else ""
        else:
            end = _bare_equation_end(value, cursor)
            if end == -1:
                end = _bare_script_end(value, cursor)
            if end != -1:
                tex = value[cursor:end]
            elif value[cursor] in _UNICODE_MATH_TEX:
                end = cursor + 1
                tex = value[cursor:end]
            else:
                end, tex = -1, ""

        if end == -1:
            cursor += 1
            continue
        normalized = _normalize_bare_math(tex)
        if not _safe_math(normalized):
            if strict_math:
                raise StudyFileFailure("Study guide contains unsupported math.")
            cursor = end
            continue
        parts.append(escape_latex(value[text_start:cursor]))
        opener, closer = (r"\[", r"\]") if display else (r"\(", r"\)")
        parts.append(opener + normalized + closer)
        cursor = end
        text_start = end
    parts.append(escape_latex(value[text_start:]))
    return "".join(parts)


def _inline(value: str, render_math: bool = True, strict_math: bool = False) -> str:
    if not render_math:
        return escape_latex(value)
    value = _normalize_math_code(_decode_math_entities(value))
    parts = []
    cursor = 0
    while match := _MATH_PATTERN.search(value, cursor):
        if strict_math and _MATH_DELIMITER_PATTERN.search(value, cursor, match.start()):
            raise StudyFileFailure("Study-guide math delimiters are unbalanced.")
        parts.append(_inline_bare(value[cursor : match.start()], strict_math))
        math = next(group for group in match.groups() if group is not None)
        math, environment_display = _normalize_math_environment(math)
        math = _normalize_bare_math(math)
        if _safe_math(math):
            display = match[1] is not None or match[2] is not None or environment_display
            parts.append((r"\[" if display else r"\(") + math + (r"\]" if display else r"\)"))
            cursor = match.end()
        elif match[0].startswith("$") and not match[0].startswith("$$"):
            if strict_math:
                raise StudyFileFailure("Study guide contains unsupported math.")
            # Leave this opening marker as text and rescan from the next dollar.
            # That next marker may open a valid equation after a truncated one.
            parts.append(escape_latex("$"))
            cursor = match.start() + 1
        else:
            if strict_math:
                raise StudyFileFailure("Study guide contains unsupported math.")
            parts.append(escape_latex(match[0]))
            cursor = match.end()
    if strict_math and _MATH_DELIMITER_PATTERN.search(value, cursor):
        raise StudyFileFailure("Study-guide math delimiters are unbalanced.")
    parts.append(_inline_bare(value[cursor:], strict_math))
    return "".join(parts)


def _body(value: str, render_math: bool = True, strict_math: bool = False) -> str:
    lines = []
    prose = []
    in_list = False

    def flush_prose() -> None:
        if prose:
            lines.append(_inline("\n".join(prose), render_math, strict_math))
            prose.clear()

    for line in value.splitlines():
        item = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.+)$", line)
        if item:
            flush_prose()
            if not in_list:
                lines.append(r"\begin{itemize}")
                in_list = True
            lines.append(r"\item " + _inline(item[1], render_math, strict_math))
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


def _picture_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _picture_line(start: tuple[float, float], end: tuple[float, float]) -> str:
    middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    points = (start, middle, end)
    return r"\qbezier" + "".join(f"({_picture_number(x)},{_picture_number(y)})" for x, y in points)


def _picture_arrow(start: tuple[float, float], end: tuple[float, float]) -> str:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 0.01:
        return ""
    ux, uy = dx / length, dy / length
    back_x, back_y = end[0] - 3.0 * ux, end[1] - 3.0 * uy
    wings = [
        (back_x + 1.3 * uy, back_y - 1.3 * ux),
        (back_x - 1.3 * uy, back_y + 1.3 * ux),
    ]
    return "\n".join([_picture_line(start, end), *(_picture_line(end, wing) for wing in wings)])


def _force_components_picture(block: StudyGuideBlock) -> str:
    parameters = block.parameters
    force_n = float(parameters.force_n)
    angle_deg = float(parameters.angle_deg)
    radians = math.radians(angle_deg)
    horizontal = force_n * math.cos(radians)
    vertical = force_n * math.sin(radians)
    scale = 50 / max(force_n, 1)
    origin = (12.0, 10.0)
    component_tip = (origin[0] + horizontal * scale, origin[1])
    tip = (component_tip[0], origin[1] + vertical * scale)
    x_axis = (93.0, origin[1])
    y_axis = (origin[0], 70.0)
    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    pieces.extend([_picture_arrow(origin, x_axis), _picture_arrow(origin, y_axis)])
    if force_n > 0:
        pieces.extend(
            [
                _picture_arrow(origin, component_tip),
                _picture_arrow(component_tip, tip),
                _picture_arrow(origin, tip),
            ]
        )
    pieces.extend(
        [
            r"\put(94,8){$x$}",
            r"\put(10,72){$y$}",
            r"\put(35,2){$F_x=" + _picture_number(horizontal) + r"\,\mathrm{N}$}",
            r"\put(14,43){$F_y=" + _picture_number(vertical) + r"\,\mathrm{N}$}",
            r"\put(49,66){$F=" + _picture_number(force_n) + r"\,\mathrm{N}$}",
            r"\put(60,56){$\theta=" + _picture_number(angle_deg) + r"^\circ$}",
            r"\end{picture}",
        ]
    )
    return "\n".join(piece for piece in pieces if piece)


def _linear_graph_picture(block: StudyGuideBlock) -> str:
    parameters = block.parameters
    slope, intercept = float(parameters.slope), float(parameters.intercept)
    bound = max(10.0, abs(slope) * 10 + abs(intercept))

    def map_point(x: float, y: float) -> tuple[float, float]:
        return (8 + (x + 10) * 4.2, 8 + (y + bound) * 58 / (2 * bound))

    first = map_point(-10, slope * -10 + intercept)
    last = map_point(10, slope * 10 + intercept)
    origin = map_point(0, 0)
    intercept_text = (
        r"-" + _picture_number(abs(intercept))
        if intercept < 0
        else r"+" + _picture_number(intercept)
    )
    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    pieces.extend(
        [
            _picture_line((8, 8), (92, 8)),
            _picture_line((8, 8), (8, 66)),
            _picture_line(origin, (92, origin[1])),
            _picture_line(origin, (origin[0], 66)),
            _picture_line(first, last),
            r"\put(89,2){$x$}",
            r"\put(51,68){$y$}",
            r"\put(11,71){$y=" + _picture_number(slope) + r"x" + intercept_text + r"$}",
            r"\end{picture}",
        ]
    )
    return "\n".join(pieces)


def _first_order_picture(block: StudyGuideBlock) -> str:
    parameters = block.parameters
    initial = float(parameters.initial_value)
    final = float(parameters.final_value)
    tau = float(parameters.time_constant_s)
    low, high = min(initial, final), max(initial, final)
    padding = max(1.0, (high - low) * 0.15)
    y_min, y_max = low - padding, high + padding

    def map_point(time: float, value: float) -> tuple[float, float]:
        return (10 + time / (5 * tau) * 80, 8 + (value - y_min) / (y_max - y_min) * 58)

    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    pieces.extend([_picture_line((10, 8), (90, 8)), _picture_line((10, 8), (10, 66))])
    previous = map_point(0, initial)
    for index in range(1, 41):
        time = 5 * tau * index / 40
        value = final + (initial - final) * math.exp(-time / tau)
        current = map_point(time, value)
        pieces.append(_picture_line(previous, current))
        previous = current
    at_tau = map_point(tau, final + (initial - final) * math.exp(-1))
    pieces.extend(
        [
            _picture_line((at_tau[0], 8), (at_tau[0], 66)),
            r"\put(84,2){$t$}",
            r"\put(12,68){$y(t)$}",
            r"\put(11,71){$y_0="
            + _picture_number(initial)
            + r",\ y_f="
            + _picture_number(final)
            + r"$}",
            r"\end{picture}",
        ]
    )
    return "\n".join(pieces)


def _resistor_divider_picture(block: StudyGuideBlock) -> str:
    parameters = block.parameters
    voltage = float(parameters.voltage_v)
    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    pieces.extend(
        [
            _picture_line((20, 66), (80, 66)),
            _picture_line((80, 66), (80, 52)),
            _picture_line((80, 37), (80, 22)),
            _picture_line((80, 8), (20, 8)),
            _picture_line((20, 8), (20, 31)),
            _picture_line((20, 43), (20, 66)),
        ]
    )
    # Fixed zig-zag resistor symbols keep this export independent of generated drawing code.
    pieces.extend(
        [
            _picture_line((80, 52), (76, 50)),
            _picture_line((76, 50), (84, 47)),
            _picture_line((84, 47), (76, 44)),
            _picture_line((76, 44), (84, 41)),
            _picture_line((84, 41), (80, 37)),
            _picture_line((80, 22), (76, 20)),
            _picture_line((76, 20), (84, 17)),
            _picture_line((84, 17), (76, 14)),
            _picture_line((76, 14), (84, 11)),
            _picture_line((84, 11), (80, 8)),
            _picture_line((16, 43), (24, 43)),
            _picture_line((18, 39), (22, 39)),
            r"\put(84,45){$R_1$}",
            r"\put(84,13){$R_2$}",
            r"\put(2,48){$V_{in}=" + _picture_number(voltage) + r"\,\mathrm{V}$}",
            r"\put(82,30){$V_{out}$}",
            r"\end{picture}",
        ]
    )
    return "\n".join(pieces)


def _molecule_picture(block: StudyGuideBlock) -> str:
    molecule = MOLECULES[block.parameters.molecule_id]
    atoms = {atom["id"]: atom for atom in molecule["atoms"]}

    def point(atom: dict) -> tuple[float, float]:
        return (10 + atom["x"] * 0.8, 64 - atom["y"] * 0.6)

    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    for bond in molecule["bonds"]:
        start, end = point(atoms[bond["from"]]), point(atoms[bond["to"]])
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy) or 1.0
        offset = 1.4 if bond["order"] == 2 else 0.0
        ox, oy = -dy / length * offset, dx / length * offset
        for shift in [-1, 1] if bond["order"] == 2 else [0]:
            pieces.append(
                _picture_line(
                    (start[0] + ox * shift, start[1] + oy * shift),
                    (end[0] + ox * shift, end[1] + oy * shift),
                )
            )
    # Polygon outlines avoid TeX's optional LCIRCLE font in the production cache.
    radius = 4.0
    diagonal = radius / math.sqrt(2)
    offsets = [
        (0.0, -radius),
        (diagonal, -diagonal),
        (radius, 0.0),
        (diagonal, diagonal),
        (0.0, radius),
        (-diagonal, diagonal),
        (-radius, 0.0),
        (-diagonal, -diagonal),
    ]
    for atom in molecule["atoms"]:
        x, y = point(atom)
        vertices = [(x + dx, y + dy) for dx, dy in offsets]
        for index, start in enumerate(vertices):
            pieces.append(_picture_line(start, vertices[(index + 1) % len(vertices)]))
        pieces.append(
            r"\put("
            + _picture_number(x - 1.5)
            + ","
            + _picture_number(y - 1)
            + r"){\texttt{"
            + atom["element"]
            + "}}"
        )
    pieces.extend(
        [
            r"\put(35,2){\textbf{" + molecule["name"] + " (" + molecule["formula"] + ")}}",
            r"\end{picture}",
        ]
    )
    return "\n".join(pieces)


def _animal_cell_picture(block: StudyGuideBlock) -> str:
    """Draw an app-owned schematic; all geometry is fixed and model-independent."""
    focus_ids = block.parameters.focus_ids

    def point(organelle_id: str) -> tuple[float, float]:
        item = ANIMAL_CELL_ORGANELLES[organelle_id]
        return (8 + int(item["x"]) * 0.84, 69 - int(item["y"]) * 0.52)

    def ellipse(center: tuple[float, float], rx: float, ry: float) -> list[str]:
        cx, cy = center
        coordinates = [
            (
                cx + rx * math.cos(2 * math.pi * index / 32),
                cy + ry * math.sin(2 * math.pi * index / 32),
            )
            for index in range(33)
        ]
        return [
            _picture_line(coordinates[index], coordinates[index + 1])
            for index in range(len(coordinates) - 1)
        ]

    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(100,76)"]
    # The cell boundary is a framing silhouette. Numbered anchors are schematic only.
    pieces.extend(ellipse((50, 38), 43, 29))
    for organelle_id in focus_ids:
        if organelle_id == "nucleus":
            pieces.extend(ellipse(point(organelle_id), 10, 7))
        elif organelle_id == "mitochondria":
            pieces.extend(ellipse(point(organelle_id), 7, 4))
            x, y = point(organelle_id)
            pieces.extend(
                [
                    _picture_line((x - 4, y), (x - 2, y + 1)),
                    _picture_line((x - 2, y + 1), (x, y - 1)),
                    _picture_line((x, y - 1), (x + 2, y + 1)),
                    _picture_line((x + 2, y + 1), (x + 4, y)),
                ]
            )
        elif organelle_id == "cytoskeleton":
            pieces.extend(
                [
                    _picture_line((17, 22), (76, 54)),
                    _picture_line((18, 53), (80, 25)),
                    _picture_line((29, 12), (70, 62)),
                ]
            )
        elif organelle_id == "endoplasmic_reticulum":
            x, y = point(organelle_id)
            pieces.extend(
                [
                    _picture_line((x - 9, y + 2), (x - 4, y + 4)),
                    _picture_line((x - 4, y + 4), (x + 1, y + 2)),
                    _picture_line((x + 1, y + 2), (x + 7, y + 4)),
                    _picture_line((x - 8, y - 2), (x - 2, y)),
                    _picture_line((x - 2, y), (x + 4, y - 2)),
                    _picture_line((x + 4, y - 2), (x + 8, y)),
                ]
            )
        elif organelle_id == "golgi_apparatus":
            x, y = point(organelle_id)
            pieces.extend(
                _picture_line((x - 6, y + offset), (x + 6, y + offset + 1))
                for offset in (-2, 0, 2)
            )
        elif organelle_id == "lysosomes_peroxisomes":
            x, y = point(organelle_id)
            pieces.extend(ellipse((x, y), 3, 3))
            pieces.extend(ellipse((x + 5, y - 4), 2, 2))

    # Number squares map the schematic anchors to the complete readable legend below.
    for index, organelle_id in enumerate(focus_ids, 1):
        x, y = point(organelle_id)
        pieces.append(
            r"\put(" + _picture_number(x - 2.5) + "," + _picture_number(y - 2.5)
            + r"){\framebox(5,5){\scriptsize " + str(index) + "}}"
        )
    pieces.extend(
        [
            r"\put(34,2){\textbf{Animal cell schematic}}",
            r"\end{picture}",
        ]
    )
    return "\n".join(pieces)


def _stress_cube_picture(block: StudyGuideBlock) -> str:
    """Draw a static axonometric cube, its shear cut, and distinct n/τ arrows."""
    state = _stress_state(block.parameters.stress_mpa)

    def project(point: list[float]) -> tuple[float, float]:
        x, y, z = point
        return 52 + 17 * (y - x), 44 + 8.5 * (x + y) - 17 * z

    vertices = [[x, y, z] for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
    vertex_indices = {tuple(point): index for index, point in enumerate(vertices)}
    edges: list[tuple[int, int]] = []
    for index, point in enumerate(vertices):
        for axis in range(3):
            other = list(point)
            other[axis] *= -1
            other_index = vertex_indices[tuple(other)]
            if other_index > index:
                edges.append((index, other_index))

    pieces = [r"\setlength{\unitlength}{1mm}", r"\begin{picture}(104,88)"]
    pieces.extend(_picture_line(project(vertices[first]), project(vertices[second])) for first, second in edges)
    plane = state["plane_vertices"]
    if len(plane) >= 3:
        pieces.append(r"\linethickness{0.6mm}")
        pieces.extend(
            _picture_line(project(plane[index]), project(plane[(index + 1) % len(plane)]))
            for index in range(len(plane))
        )
        pieces.append(r"\linethickness{0.2mm}")
    center = [sum(point[axis] for point in plane) / len(plane) for axis in range(3)] if plane else [0, 0, 0]
    normal = state["normal"]
    shear = state["shear"]
    shear_length = math.sqrt(sum(value * value for value in shear))
    shear_direction = [value / shear_length for value in shear] if shear_length > 1e-12 else [0, 0, 0]
    second_direction = state["second_principal_direction"]
    shear_start = [center[index] + 0.3 * second_direction[index] for index in range(3)]
    normal_end = [center[index] + 1.15 * normal[index] for index in range(3)]
    shear_end = [shear_start[index] + 1.05 * shear_direction[index] for index in range(3)]
    pieces.extend(
        [
            _picture_arrow(project(center), project(normal_end)),
            _picture_arrow(project(shear_start), project(shear_end)),
            r"\put(76,75){$n$}",
            r"\put(80,62){$\tau$}",
            r"\put(9,6){x-y-z axes; tension positive}",
            r"\end{picture}",
        ]
    )
    return "\n".join(piece for piece in pieces if piece)


def _render_guide_block(
    block: StudyGuideBlock,
    render_math: bool,
    strict_math: bool = False,
    *,
    svg_image: str | None = None,
) -> list[str]:
    def inline(value: str) -> str:
        return _inline(value, render_math, strict_math)

    def body(value: str) -> str:
        return _body(value, render_math, strict_math)

    if block.type == "paragraph":
        return [body(block.text or "")]
    if block.type == "formula":
        content = [_display_math(block.expression, render_math, strict_math)]
        if block.definitions:
            content.append(r"\begin{itemize}")
            for definition in block.definitions:
                item = (
                    inline(definition.symbol)
                    + ": "
                    + inline(definition.meaning)
                )
                if definition.unit:
                    item += " (" + inline(definition.unit) + ")"
                content.append(r"\item " + item)
            content.append(r"\end{itemize}")
        return content
    if block.type == "interactive_model" and block.template_id == "force_components_v1":
        parameters = block.parameters
        radians = math.radians(parameters.angle_deg)
        horizontal = parameters.force_n * math.cos(radians)
        vertical = parameters.force_n * math.sin(radians)
        return [
            r"\paragraph{" + escape_latex(block.title or "Interactive model") + "}",
            r"\begin{center}" + _force_components_picture(block) + r"\end{center}",
            inline(r"$F_x=F\cos\theta,\quad F_y=F\sin\theta$"),
            inline(
                block.fallback_text
                or (
                    f"At {_picture_number(parameters.angle_deg)} degrees, "
                    f"the horizontal component is {_picture_number(horizontal)} N and the "
                    f"vertical component is {_picture_number(vertical)} N."
                ),
            ),
            inline(
                "Text alternative: " + (block.alt_text or "Force vector and components."),
            ),
        ]
    if block.type == "interactive_model" and block.template_id == "linear_graph_v1":
        return [
            r"\paragraph{" + escape_latex(block.title or "Linear graph") + "}",
            r"\begin{center}" + _linear_graph_picture(block) + r"\end{center}",
            inline(guide_block_fallback(block.model_dump(mode="json"))),
            inline(
                "Text alternative: " + guide_block_alt_text(block.model_dump(mode="json")),
            ),
        ]
    if block.type == "interactive_model" and block.template_id == "first_order_response_v1":
        return [
            r"\paragraph{" + escape_latex(block.title or "First-order response") + "}",
            r"\begin{center}" + _first_order_picture(block) + r"\end{center}",
            inline(r"$y(t)=y_f+(y_0-y_f)e^{-t/\tau}$"),
            inline(guide_block_fallback(block.model_dump(mode="json"))),
            inline(
                "Text alternative: " + guide_block_alt_text(block.model_dump(mode="json")),
            ),
        ]
    if block.type == "interactive_model" and block.template_id == "resistor_divider_v1":
        return [
            r"\paragraph{" + escape_latex(block.title or "Resistor divider") + "}",
            r"\begin{center}" + _resistor_divider_picture(block) + r"\end{center}",
            inline(
                r"$V_{out}=V_{in}\frac{R_2}{R_1+R_2},\quad I=\frac{V_{in}}{R_1+R_2}$"
            ),
            inline(
                "Assumptions: ideal source, ideal wires, and no load on the output. "
                + guide_block_fallback(block.model_dump(mode="json")),
            ),
            inline(
                "Text alternative: " + guide_block_alt_text(block.model_dump(mode="json")),
            ),
        ]
    if block.type == "interactive_model" and block.template_id == "binary_search_trace_v1":
        trace, result = binary_search_trace(block.parameters.values, block.parameters.target)
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Binary search trace") + "}",
            r"\textbf{Sorted values:} "
            + ", ".join(str(value) for value in block.parameters.values),
            r"\begin{enumerate}",
        ]
        for item in trace:
            comparison = {
                "equal": "matches",
                "smaller": "is less than",
                "larger": "is greater than",
            }[item["comparison"]]
            lines.append(
                r"\item Search indexes "
                + str(item["low"])
                + " through "
                + str(item["high"])
                + r"; check middle index "
                + str(item["middle"])
                + r" (value "
                + str(item["value"])
                + r"). The target "
                + comparison
                + " "
                + str(item["value"])
                + "."
            )
        lines.append(r"\end{enumerate}")
        lines.append(
            inline(
                f"The target is at index {result}."
                if result is not None
                else "The target is not in the list.",
            )
        )
        return lines
    if block.type == "interactive_model" and block.template_id == "sampling_demo_v1":
        parameters = block.parameters
        result = sampling_demo_result(
            parameters.method,
            parameters.sample_size,
            parameters.seed,
            parameters.random_assignment,
        )
        fallback = guide_block_fallback(block.model_dump(mode="json"))
        synthetic_notice = "App-authored synthetic illustration, not source data: "
        if fallback.startswith(synthetic_notice):
            fallback = fallback.removeprefix(synthetic_notice)
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Sampling demonstration") + "}",
            r"\textbf{"
            + escape_latex("App-authored synthetic illustration, not source data.")
            + "}",
            escape_latex(fallback),
            r"\begin{center}\small\begin{tabular}{llll}",
            r"\textbf{Student} & \textbf{Stratum} & \textbf{Practice problems} & "
            r"\textbf{Assigned treatment} \\",
            r"\hline",
        ]
        for row in result["selected"]:
            treatment = result["assignment"].get(str(row["id"]), "Not assigned")
            lines.append(
                escape_latex(str(row["id"]))
                + " & "
                + escape_latex(str(row["stratum"]))
                + " & "
                + str(row["practice_problems"])
                + " & "
                + escape_latex(treatment)
                + r" \\"
            )
        lines.extend(
            [
                r"\end{tabular}\end{center}",
                escape_latex(guide_block_alt_text(block.model_dump(mode="json"))),
            ]
        )
        return lines
    if block.type == "interactive_model" and block.template_id == "graph_traversal_v1":
        parameters = block.parameters.model_dump(mode="json")
        labels = {node["id"]: node["label"] for node in parameters["nodes"]}
        traversal_ids, unreachable_ids = _graph_traversal(parameters)
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Graph traversal") + "}",
            r"\textbf{Algorithm:} " + parameters["algorithm"].upper()
            + r". \textbf{Start node:} " + escape_latex(labels[parameters["start_node"]]) + ".",
            r"\textbf{Traversal order}",
            r"\begin{enumerate}",
        ]
        lines.extend(r"\item " + escape_latex(labels[node_id]) for node_id in traversal_ids)
        lines.append(r"\end{enumerate}")
        if unreachable_ids:
            lines.extend([r"\textbf{Unreachable from start:} " + ", ".join(
                escape_latex(labels[node_id]) for node_id in unreachable_ids
            ) + "."])
        lines.extend([r"\textbf{Directed edges}", r"\begin{itemize}"])
        lines.extend(
            r"\item " + escape_latex(labels[edge["source"]]) + r" $\rightarrow$ "
            + escape_latex(labels[edge["target"]])
            for edge in parameters["edges"]
        )
        lines.extend(
            [
                r"\end{itemize}",
                inline(guide_block_fallback(block.model_dump(mode="json"))),
                inline("Text alternative: " + guide_block_alt_text(block.model_dump(mode="json"))),
            ]
        )
        return lines
    if block.type == "interactive_model" and block.template_id == "stress_cube_3d_v1":
        parameters = block.parameters
        state = _stress_state(parameters.stress_mpa)
        matrix_lines = [
            r"\begin{center}\begin{tabular}{c|rrr}",
            r"$\sigma$ (MPa) & x & y & z \\",
        ]
        for axis, row in zip("xyz", parameters.stress_mpa, strict=True):
            matrix_lines.append(
                axis + " & " + " & ".join(_picture_number(value) for value in row) + r" \\"
            )
        matrix_lines.append(r"\end{tabular}\end{center}")
        sigma1, sigma2, sigma3 = state["principal_stresses"]
        normal = ", ".join(_picture_number(value) for value in state["normal"])
        shear = ", ".join(_picture_number(value) for value in state["shear"])
        return [
            r"\paragraph{" + escape_latex(block.title or "3D stress cube") + "}",
            *matrix_lines,
            r"\begin{center}" + _stress_cube_picture(block) + r"\end{center}",
            inline(
                f"Principal stresses: σ₁={_picture_number(sigma1)} MPa, "
                f"σ₂={_picture_number(sigma2)} MPa, σ₃={_picture_number(sigma3)} MPa."
            ),
            inline(
                f"The selected maximum-shear plane has n=({normal}), "
                f"normal stress {_picture_number(state['normal_stress'])} MPa, and shear traction "
                f"τ=({shear}) MPa with magnitude {_picture_number(state['shear_magnitude'])} MPa. "
                "The principal construction is 45 degrees from the σ₁ and σ₃ directions; "
                "the plane orientation is not unique if an extreme principal stress is repeated."
            ),
            inline(
                "Tension is positive. The absolute maximum shear is half the difference "
                "between the largest and smallest principal stresses."
            ),
            inline("Text alternative: " + guide_block_alt_text(block.model_dump(mode="json"))),
        ]
    if block.type == "interactive_model" and block.template_id == "python_algorithm_trace_v1":
        parameters = block.parameters.model_dump(mode="json")
        result = _python_algorithm_trace(parameters)
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Python algorithm walkthrough") + "}",
            r"\textbf{Fixed app-authored Python example:} "
            + escape_latex(parameters["algorithm"].replace("_", " ")) + ".",
            r"\textbf{Input values:} ["
            + ", ".join(str(value) for value in parameters["values"]) + "].",
        ]
        if "target" in parameters:
            lines.append(r"\textbf{Search target:} " + str(parameters["target"]) + ".")
        lines.extend([r"\textbf{Trace}", r"\begin{enumerate}"])
        lines.extend(r"\item " + escape_latex(step) for step in result["steps"])
        lines.append(r"\end{enumerate}")
        if "found_index" in result:
            found = result["found_index"]
            lines.append(
                r"\textbf{Result:} "
                + (f"target is at index {found}." if found is not None else "target is absent.")
            )
        else:
            lines.append(
                r"\textbf{Sorted values:} ["
                + ", ".join(str(value) for value in result["sorted_values"]) + "]."
            )
        lines.extend(
            [
                inline("No model-supplied code is executed. " + guide_block_fallback(block.model_dump(mode="json"))),
                inline("Text alternative: " + guide_block_alt_text(block.model_dump(mode="json"))),
            ]
        )
        return lines
    if block.type == "interactive_model" and block.template_id == "simcir_circuit_v1":
        circuit_id = block.parameters.circuit_id
        title, description = {
            "and_gate": ("AND gate", "Output Y is 1 only when both inputs are 1."),
            "or_gate": ("OR gate", "Output Y is 1 when at least one input is 1."),
            "half_adder": ("Half adder", "XOR produces SUM; AND produces CARRY."),
        }[circuit_id]
        outputs = ("Y",) if circuit_id != "half_adder" else ("SUM", "CARRY")
        columns = "l" * (2 + len(outputs))
        headers = " & ".join(["A", "B", *outputs])
        rows = _simcir_truth_table(circuit_id)
        lines = [
            r"\paragraph{" + escape_latex(block.title or title) + "}",
            inline(description),
            r"\begin{center}\begin{tabular}{" + columns + "}",
            r"\textbf{" + headers.replace(" & ", r"} & \textbf{") + r"} \\\hline",
        ]
        lines.extend(
            " & ".join(str(row[name]) for name in ("A", "B", *outputs)) + r" \\"
            for row in rows
        )
        lines.extend(
            [
                r"\end{tabular}\end{center}",
                escape_latex(
                    "App-authored fixed circuit definition. "
                    + guide_block_fallback(block.model_dump(mode="json"))
                ),
                inline("Text alternative: " + guide_block_alt_text(block.model_dump(mode="json"))),
            ]
        )
        return lines
    if block.type == "labeled_diagram" and block.template_id == "process_pathway_v1":
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Process pathway") + "}",
            inline(guide_block_alt_text(block.model_dump(mode="json"))),
            r"\begin{enumerate}",
        ]
        for stage in block.parameters.stages:
            lines.append(
                r"\item \textbf{"
                + escape_latex(stage.title)
                + "} "
                + body(stage.explanation)
            )
        lines.append(r"\end{enumerate}")
        return lines
    if block.type == "labeled_diagram" and block.template_id == "molecule_2d_v1":
        molecule = MOLECULES[block.parameters.molecule_id]
        return [
            r"\paragraph{" + escape_latex(block.title or molecule["name"]) + "}",
            r"\begin{center}" + _molecule_picture(block) + r"\end{center}",
            inline(guide_block_fallback(block.model_dump(mode="json"))),
            inline(
                "Text alternative: " + guide_block_alt_text(block.model_dump(mode="json")),
            ),
        ]
    if block.type == "labeled_diagram" and block.template_id == "molecule_3d_v1":
        molecule = MOLECULE_3D_CATALOG[block.parameters.molecule_id]
        structure = MOLECULES[block.parameters.molecule_id]
        atoms = len(structure["atoms"])
        bonds = len(structure["bonds"])
        return [
            r"\paragraph{" + escape_latex(block.title or molecule["name"]) + "}",
            r"\textbf{Formula:} " + escape_latex(molecule["formula"]) + ".",
            r"\textbf{Idealized geometry:} " + escape_latex(molecule["geometry"]) + ".",
            r"\begin{itemize}",
            r"\item " + escape_latex(molecule["description"]),
            r"\item Connectivity: " + escape_latex(molecule["bond_description"]),
            r"\item The fixed model contains " + str(atoms) + " atoms and " + str(bonds)
            + " bonds. Its coordinates are idealized; this summary is not a measured structure.",
            r"\end{itemize}",
            inline(guide_block_fallback(block.model_dump(mode="json"))),
            inline("Text alternative: " + guide_block_alt_text(block.model_dump(mode="json"))),
        ]
    if block.type == "labeled_diagram" and block.template_id == "animal_cell_map_v1":
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Animal-cell map") + "}",
            r"\begin{center}" + _animal_cell_picture(block) + r"\end{center}",
            inline(ANIMAL_CELL_LIMITATION),
            r"\begin{enumerate}",
        ]
        for organelle_id in block.parameters.focus_ids:
            organelle = ANIMAL_CELL_ORGANELLES[organelle_id]
            lines.append(
                r"\item \textbf{" + escape_latex(str(organelle["name"])) + "} "
                + inline(str(organelle["description"]))
            )
        lines.extend(
            [
                r"\end{enumerate}",
                inline("Text alternative: " + animal_cell_map_alt_text(block.parameters.focus_ids)),
            ]
        )
        return lines
    if block.type == "svg_diagram":
        lines = [r"\paragraph{" + escape_latex(block.title or "Custom diagram") + "}"]
        if svg_image and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}\.png", svg_image):
            lines.extend(
                [
                    r"\begin{center}\includegraphics[width=\linewidth,height=0.46\textheight,keepaspectratio]{"
                    + svg_image
                    + r"}\end{center}",
                    inline("Text alternative: " + (block.alt_text or "Custom course diagram.")),
                ]
            )
        else:
            lines.extend(
                [
                    r"\textit{Interactive SVG diagram is available in the web guide.}",
                    body(block.fallback_text or block.alt_text or "Custom course diagram."),
                    inline("Text alternative: " + (block.alt_text or "Custom course diagram.")),
                ]
            )
        return lines
    if block.type == "example_steps":
        lines = [
            r"\paragraph{" + escape_latex(block.title or "Worked example") + "}",
            r"\begin{enumerate}",
        ]
        for step in block.steps:
            item = (
                r"\item \textbf{"
                + escape_latex(step.title)
                + "} "
                + body(step.explanation)
            )
            if step.expression:
                item += " " + inline("$$" + step.expression + "$$")
            lines.append(item)
        lines.append(r"\end{enumerate}")
        return lines
    if block.type == "takeaway":
        return [r"\paragraph{Key takeaway}", body(block.text or "")]
    return []


def render_guide_latex(
    *,
    title: str,
    class_name: str,
    material_id: UUID,
    content: dict,
    sources: list[dict],
    original_text: str | None = None,
    render_math: bool = True,
    strict_math: bool = False,
    svg_image_map: dict[tuple[int, int], str] | None = None,
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
\usepackage{graphicx}
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
    for section_index, section in enumerate(guide.sections):
        document.append(r"\section{" + escape_latex(section.heading) + "}")
        if isinstance(section, StudyGuideSectionV2) and section.blocks:
            for block_index, block in enumerate(section.blocks):
                document.extend(
                    _render_guide_block(
                        block,
                        render_math,
                        strict_math,
                        svg_image=(svg_image_map or {}).get((section_index, block_index)),
                    )
                )
        else:
            document.append(_body(section.body or "", render_math, strict_math))
        if section.key_terms:
            document.append(
                r"\textbf{Key terms:} "
                + "; ".join(_inline(term, render_math, strict_math) for term in section.key_terms)
            )
        source_citations = list(section.sources)
        if isinstance(section, StudyGuideSectionV2):
            source_citations.extend(
                citation for block in section.blocks for citation in block.sources
            )
        citations = []
        seen_citations: set[tuple[str, int | None, int | None, float | None]] = set()
        for citation in source_citations:
            citation_key = (
                str(citation.upload_id),
                citation.page,
                citation.slide,
                citation.timestamp_seconds,
            )
            if citation_key in seen_citations:
                continue
            seen_citations.add(citation_key)
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
    if source_map:
        document.extend([r"\newpage", r"\section*{Source references}", r"\begin{description}"])
        for source_id, (number, name) in source_map.items():
            document.extend(
                [
                    r"\item[{["
                    + str(number)
                    + r"]}]\phantomsection\label{source:"
                    + str(number)
                    + "}",
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


def compile_guide_pdf(
    latex: str,
    settings: Settings,
    *,
    assets: dict[str, bytes] | None = None,
    only_cached: bool = True,
) -> bytes:
    executable = compiler_path(settings)
    if not executable:
        raise StudyFileFailure("Install Tectonic to create study-guide PDFs.")
    _COMPILATION_LOCK.acquire()
    try:
        with tempfile.TemporaryDirectory(prefix="screen-tutor-guide-") as directory:
            source = Path(directory) / "guide.tex"
            source.write_text(latex, encoding="utf-8")
            total_asset_bytes = 0
            for name, data in (assets or {}).items():
                if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}\.png", name):
                    raise StudyFileFailure("Study-guide PDF contains an invalid diagram asset.")
                if not data or len(data) > MAX_PREVIEW_PNG_BYTES:
                    raise StudyFileFailure("Study-guide PDF diagram asset exceeds its size limit.")
                total_asset_bytes += len(data)
                if total_asset_bytes > _MAX_GUIDE_DIAGRAM_BYTES:
                    raise StudyFileFailure("Study-guide PDF diagram assets exceed their combined size limit.")
                (Path(directory) / name).write_bytes(data)
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


def create_guide_files(
    settings: Settings, *, strict_math: bool = False, **guide: object
) -> dict[str, bytes]:
    svg_image_map: dict[tuple[int, int], str] = {}
    assets: dict[str, bytes] = {}
    try:
        parsed = StudyGuideContent.model_validate(guide.get("content"))
        if parsed.schema_version == 2:
            total_asset_bytes = 0
            for section_index, section in enumerate(parsed.sections):
                if not isinstance(section, StudyGuideSectionV2):
                    continue
                for block_index, block in enumerate(section.blocks):
                    if block.type != "svg_diagram" or len(assets) >= _MAX_GUIDE_DIAGRAMS:
                        continue
                    try:
                        image = render_svg_preview_png(block.svg or "")
                    except (SvgPreviewRenderError, ValueError):
                        continue
                    if total_asset_bytes + len(image) > _MAX_GUIDE_DIAGRAM_BYTES:
                        break
                    name = f"jot-guide-diagram-{len(assets) + 1:03d}.png"
                    assets[name] = image
                    svg_image_map[(section_index, block_index)] = name
                    total_asset_bytes += len(image)
    except Exception:
        # If a document cannot be parsed for optional diagram rendering, the normal
        # validator below still reports the invalid content and text export stays safe.
        assets.clear()
        svg_image_map.clear()

    latex = render_guide_latex(**guide, strict_math=strict_math)
    pdf_latex = render_guide_latex(
        **guide,
        strict_math=strict_math,
        svg_image_map=svg_image_map,
    )
    try:
        pdf = compile_guide_pdf(pdf_latex, settings, assets=assets)
    except StudyFileFailure:
        if strict_math:
            raise
        # A malformed model equation must not discard otherwise valid study content.
        latex = render_guide_latex(**guide, render_math=False)
        pdf_latex = render_guide_latex(
            **guide,
            render_math=False,
            svg_image_map=svg_image_map,
        )
        try:
            pdf = compile_guide_pdf(pdf_latex, settings, assets=assets)
        except StudyFileFailure:
            if not assets:
                raise
            # A broken or unsupported image must not make the entire guide unavailable.
            pdf = compile_guide_pdf(latex, settings)
    return {"latex": latex.encode("utf-8"), "pdf": pdf}
