"""Render sanitized study-guide SVG for the authoring model's visual self-check."""

from __future__ import annotations

import io
import math
import re
import shutil
import subprocess

from PIL import Image

from app.services.safe_svg import sanitize_svg

MAX_PREVIEW_WIDTH = 1280
MAX_PREVIEW_HEIGHT = 960
PHONE_CARD_PREVIEW_WIDTH = 360
PHONE_CARD_PREVIEW_HEIGHT = 420
MAX_PREVIEW_PNG_BYTES = 8 * 1024 * 1024
PREVIEW_TIMEOUT_SECONDS = 12


class SvgPreviewRenderError(RuntimeError):
    """The sanitized SVG could not be rendered into a bounded preview image."""


def render_svg_preview_png(
    svg: str,
    *,
    max_width: int = MAX_PREVIEW_WIDTH,
    max_height: int = MAX_PREVIEW_HEIGHT,
) -> bytes:
    """Render one sanitized SVG at its display size, bounded by the requested box."""
    if max_width < 1 or max_height < 1:
        raise SvgPreviewRenderError("The SVG preview dimensions are invalid.")
    executable = shutil.which("rsvg-convert")
    if executable is None:
        raise SvgPreviewRenderError("The SVG preview renderer is unavailable.")

    try:
        safe_markup = sanitize_svg(svg)
        safe_svg = safe_markup.encode("utf-8")
        view_box = re.search(r'\bviewBox="([^"]+)"', safe_markup)
        if not view_box:
            raise SvgPreviewRenderError("The sanitized SVG has no usable viewBox.")
        view_values = [float(value) for value in re.split(r"[\s,]+", view_box[1].strip())]
        if len(view_values) != 4 or view_values[2] <= 0 or view_values[3] <= 0:
            raise SvgPreviewRenderError("The sanitized SVG has an invalid viewBox.")
        scale = min(max_width / view_values[2], max_height / view_values[3])
        render_width = max(1, math.floor(view_values[2] * scale + 0.5))
        render_height = max(1, math.floor(view_values[3] * scale + 0.5))
        result = subprocess.run(
            [
                executable,
                "--format=png",
                f"--width={render_width}",
                f"--height={render_height}",
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
            if preview.format != "PNG" or preview.width > max_width:
                raise SvgPreviewRenderError("The SVG preview has invalid image dimensions.")
            if preview.height > max_height or preview.width * preview.height > max_width * max_height:
                raise SvgPreviewRenderError("The SVG preview exceeds the image-size limit.")
            preview.verify()
    except (OSError, ValueError) as exc:
        raise SvgPreviewRenderError("The SVG preview is not a valid PNG image.") from exc

    return result.stdout
