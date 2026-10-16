"""Render sanitized study-guide SVG for the authoring model's visual self-check."""

from __future__ import annotations

import io
import shutil
import subprocess

from PIL import Image

from app.services.safe_svg import sanitize_svg

MAX_PREVIEW_WIDTH = 1280
MAX_PREVIEW_HEIGHT = 960
MAX_PREVIEW_PNG_BYTES = 8 * 1024 * 1024
PREVIEW_TIMEOUT_SECONDS = 12


class SvgPreviewRenderError(RuntimeError):
    """The sanitized SVG could not be rendered into a bounded preview image."""


def render_svg_preview_png(svg: str) -> bytes:
    """Render one sanitized SVG to a bounded white-background PNG using librsvg."""
    executable = shutil.which("rsvg-convert")
    if executable is None:
        raise SvgPreviewRenderError("The SVG preview renderer is unavailable.")

    try:
        safe_svg = sanitize_svg(svg).encode("utf-8")
        result = subprocess.run(
            [
                executable,
                "--format=png",
                f"--width={MAX_PREVIEW_WIDTH}",
                f"--height={MAX_PREVIEW_HEIGHT}",
                "--background-color=white",
            ],
            input=safe_svg,
            capture_output=True,
            timeout=PREVIEW_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SvgPreviewRenderError("The SVG preview could not be rendered.") from exc

    if result.returncode != 0 or not result.stdout or len(result.stdout) > MAX_PREVIEW_PNG_BYTES:
        raise SvgPreviewRenderError("The SVG preview renderer rejected the diagram.")

    try:
        with Image.open(io.BytesIO(result.stdout)) as preview:
            if preview.format != "PNG" or preview.width > MAX_PREVIEW_WIDTH:
                raise SvgPreviewRenderError("The SVG preview has invalid image dimensions.")
            if preview.height > MAX_PREVIEW_HEIGHT or preview.width * preview.height > 1_228_800:
                raise SvgPreviewRenderError("The SVG preview exceeds the image-size limit.")
            preview.verify()
    except (OSError, ValueError) as exc:
        raise SvgPreviewRenderError("The SVG preview is not a valid PNG image.") from exc

    return result.stdout
