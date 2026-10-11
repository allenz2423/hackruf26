"""Install a pinned local Tectonic binary and cache the guide template dependencies.

Run from the repo root: backend/.venv/bin/python backend/scripts/install-study-pdf.py
"""

from __future__ import annotations

import hashlib
import io
import platform
import sys
import tarfile
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

VERSION = "0.17.0"
RELEASES = {
    ("Linux", "x86_64"): (
        "x86_64-unknown-linux-musl",
        "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7",
    ),
    ("Linux", "aarch64"): (
        "aarch64-unknown-linux-musl",
        "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8",
    ),
    ("Darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80",
    ),
    ("Darwin", "arm64"): (
        "aarch64-apple-darwin",
        "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667",
    ),
}


def main() -> None:
    from app.config import Settings
    from app.services.study_files import compile_guide_pdf, render_guide_latex

    release = RELEASES.get((platform.system(), platform.machine()))
    if release is None:
        raise SystemExit(
            "Install Tectonic from "
            "https://tectonic-typesetting.github.io/en-US/install.html and set "
            "STUDY_PDF_EXECUTABLE."
        )
    target, digest = release
    binary = ROOT / "backend/.local/bin/tectonic"
    if not binary.is_file():
        url = f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-{target}.tar.gz"
        with urllib.request.urlopen(url, timeout=60) as response:
            archive_bytes = response.read(100 * 1024 * 1024)
        if hashlib.sha256(archive_bytes).hexdigest() != digest:
            raise SystemExit("Tectonic download checksum mismatch.")
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            entry = next(
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "tectonic"
            )
            data = archive.extractfile(entry).read()
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(data)
        binary.chmod(0o755)
    settings = Settings(study_pdf_executable=str(binary), study_pdf_timeout_seconds=120)
    upload_id = uuid4()
    latex = render_guide_latex(
        title="Study guide",
        class_name="Template setup",
        material_id=uuid4(),
        content={
            "format": "study_guide",
            "sections": [
                {
                    "heading": "Template",
                    "body": "Unicode β. Formula: $x^2 + \\frac{1}{2}$.\n\n- One\n- Two",
                    "key_terms": ["term"],
                    "sources": [{"upload_id": str(upload_id), "page": 1}],
                }
            ],
        },
        sources=[{"id": upload_id, "title": "Example source"}],
    )
    print("Caching LaTeX packages and fonts for offline guide generation…", flush=True)
    compile_guide_pdf(latex, settings, only_cached=False)
    compile_guide_pdf(latex, settings)
    print(f"Study-guide PDF generation ready: {binary}")


if __name__ == "__main__":
    main()
