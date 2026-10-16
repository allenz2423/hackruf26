"""Extract citation-scoped raster evidence from private course originals."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_VISUAL_EVIDENCE_IMAGES = 12
MAX_VISUAL_EVIDENCE_BYTES = 12 * 1024 * 1024
MAX_VISUAL_IMAGE_BYTES = 2 * 1024 * 1024
MAX_VISUAL_IMAGE_DIMENSION = 1800
MAX_VISUAL_SOURCE_PIXELS = 60_000_000
MAX_RENDERED_PAGE_BYTES = 32 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 45


@dataclass(frozen=True)
class SourceVisualEvidence:
    """One private source image, ready to send as an inline multimodal input."""

    label: str
    mime_type: str
    data: bytes


class VisualEvidenceLimitError(ValueError):
    """Cited visual evidence exceeded the bounded inline-image budget."""


class VisualEvidenceUnavailableError(RuntimeError):
    """A cited page/slide could not be rendered into multimodal evidence."""

    def __init__(
        self,
        message: str,
        *,
        source_format: str | None = None,
        locator_kind: str | None = None,
        locator_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.source_format = source_format
        self.locator_kind = locator_kind
        self.locator_index = locator_index


def extract_cited_source_visuals(
    sources: list[dict], excerpts: list[dict]
) -> list[SourceVisualEvidence]:
    """Read only images on cited PDF pages/slides from the supplied originals."""
    source_map = {str(source.get("id")): source for source in sources if source.get("id")}
    visuals: list[SourceVisualEvidence] = []
    seen_images: set[str] = set()
    source_cache: dict[str, object] = {}
    total_bytes = 0

    for excerpt in excerpts:
        upload_id = str(excerpt.get("upload_id") or "")
        source = source_map.get(upload_id)
        locator = excerpt.get("locator") or {}
        slide = locator.get("slide")
        page = locator.get("page")
        path_value = source.get("original_path") if source else None
        if not isinstance(path_value, (Path, str)):
            source_format = _source_format(source or {})
            locator_kind, locator_index = _required_visual_locator(
                source_format, slide=slide, page=page
            )
            if locator_kind:
                raise VisualEvidenceUnavailableError(
                    "the cited course source file is unavailable",
                    source_format=source_format,
                    locator_kind=locator_kind,
                    locator_index=locator_index,
                )
            continue
        path = Path(path_value)
        if not path.is_file():
            source_format = _source_format(source or {}, path)
            locator_kind, locator_index = _required_visual_locator(
                source_format, slide=slide, page=page
            )
            if locator_kind:
                raise VisualEvidenceUnavailableError(
                    "the cited course source file is unavailable",
                    source_format=source_format,
                    locator_kind=locator_kind,
                    locator_index=locator_index,
                )
            continue
        title = str(excerpt.get("title") or source.get("title") or "Course source")

        try:
            candidates = _images_for_locator(
                path,
                source=source,
                slide=slide,
                page=page,
                source_cache=source_cache,
            )
        except VisualEvidenceUnavailableError as exc:
            locator_kind = (
                "slide" if type(slide) is int else "page" if type(page) is int else None
            )
            locator_index = slide if type(slide) is int else page if type(page) is int else None
            raise VisualEvidenceUnavailableError(
                str(exc),
                source_format=path.suffix.casefold()[:12],
                locator_kind=locator_kind,
                locator_index=locator_index,
            ) from exc
        except VisualEvidenceLimitError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile):
            source_format = _source_format(source, path)
            locator_kind, locator_index = _required_visual_locator(
                source_format, slide=slide, page=page
            )
            if locator_kind:
                raise VisualEvidenceUnavailableError(
                    "the cited course source visual could not be extracted",
                    source_format=source_format,
                    locator_kind=locator_kind,
                    locator_index=locator_index,
                ) from None
            continue

        for image_number, (data, label) in enumerate(candidates, start=1):
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_images:
                continue
            seen_images.add(digest)
            normalized = _normalize_image(data)
            if normalized is None:
                continue
            mime_type, normalized_data = normalized
            if (
                len(visuals) >= MAX_VISUAL_EVIDENCE_IMAGES
                or total_bytes + len(normalized_data) > MAX_VISUAL_EVIDENCE_BYTES
            ):
                raise VisualEvidenceLimitError("cited visual evidence exceeds the review limit")
            locator_label = (
                f"slide {slide}" if type(slide) is int else f"page {page}"
                if type(page) is int
                else "uploaded image"
            )
            visuals.append(
                SourceVisualEvidence(
                    label=(
                        f"Visual evidence {len(visuals) + 1}: {title}, {locator_label}, "
                        f"image {image_number} ({label})"
                    ),
                    mime_type=mime_type,
                    data=normalized_data,
                )
            )
            total_bytes += len(normalized_data)

    return visuals


def _images_for_locator(
    path: Path,
    *,
    source: dict,
    slide: object,
    page: object,
    source_cache: dict[str, object],
) -> list[tuple[bytes, str]]:
    suffix = path.suffix.casefold()
    if suffix == ".pptx" and type(slide) is int and slide > 0:
        rendered = _render_cited_page(
            path, source=source, page_number=slide, is_presentation=True
        )
        return [(rendered, "rendered PowerPoint slide")]
    if suffix == ".pdf" and type(page) is int and page > 0:
        rendered = _render_cited_page(path, source=source, page_number=page)
        return [(rendered, "rendered PDF page")]
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        return [(path.read_bytes(), "uploaded image")]
    if suffix == ".docx" and page is None and slide is None:
        key = str(path)
        cached = source_cache.get(key)
        if cached is None:
            images = []
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.startswith("word/media/") and not name.endswith("/"):
                        images.append((archive.read(name), Path(name).name))
            source_cache[key] = images
            cached = images
        return cached
    return []


def _render_cited_page(
    path: Path,
    *,
    source: dict,
    page_number: int,
    is_presentation: bool = False,
) -> bytes | None:
    """Render the whole cited page so OLE equations and vector marks are visible."""
    poppler = shutil.which("pdftoppm")
    soffice = (shutil.which("soffice") or shutil.which("libreoffice")) if is_presentation else None
    if poppler is None or (is_presentation and soffice is None):
        rendered_format = "PowerPoint slide" if is_presentation else "PDF page"
        raise VisualEvidenceUnavailableError(
            f"the cited {rendered_format} renderer is unavailable"
        )

    state = source.setdefault("_visual_render_state", {})
    page_key = ("slide" if is_presentation else "page", page_number)
    cached = state.setdefault("pages", {}).get(page_key)
    if isinstance(cached, bytes):
        return cached

    temporary_directory = state.get("temporary_directory")
    if temporary_directory is None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="study-guide-visual-")
        state["temporary_directory"] = temporary_directory
    render_directory = Path(temporary_directory.name)
    pdf_path = path

    if is_presentation:
        cached_pdf = state.get("pdf_path")
        if isinstance(cached_pdf, Path) and cached_pdf.is_file():
            pdf_path = cached_pdf
        else:
            profile = render_directory / "office-profile"
            profile.mkdir(mode=0o700)
            command = [
                str(soffice),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf:impress_pdf_Export",
                "--outdir",
                str(render_directory),
                str(path),
            ]
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=RENDER_TIMEOUT_SECONDS,
                    cwd=render_directory,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise VisualEvidenceUnavailableError(
                    "the cited PowerPoint slide could not be rendered"
                ) from None
            converted = render_directory / f"{path.stem}.pdf"
            if result.returncode != 0 or not converted.is_file():
                raise VisualEvidenceUnavailableError(
                    "the cited PowerPoint slide could not be rendered"
                )
            if converted.stat().st_size > MAX_RENDERED_PAGE_BYTES * 8:
                raise VisualEvidenceLimitError("rendered PowerPoint exceeds the page-render limit")
            state["pdf_path"] = converted
            pdf_path = converted

    output_prefix = render_directory / f"source-page-{page_number}"
    command = [
        str(poppler),
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-singlefile",
        "-r",
        "144",
        "-png",
        str(pdf_path),
        str(output_prefix),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=RENDER_TIMEOUT_SECONDS,
            cwd=render_directory,
        )
    except (OSError, subprocess.TimeoutExpired):
        message = "the cited source page could not be rendered"
        raise VisualEvidenceUnavailableError(message) from None
    rendered_path = output_prefix.with_suffix(".png")
    if result.returncode != 0 or not rendered_path.is_file():
        raise VisualEvidenceUnavailableError("the cited source page could not be rendered")
    if rendered_path.stat().st_size > MAX_RENDERED_PAGE_BYTES:
        raise VisualEvidenceLimitError("the cited source page exceeds the render limit")
    rendered = rendered_path.read_bytes()
    state.setdefault("pages", {})[page_key] = rendered
    return rendered


def _source_format(source: dict, path: Path | None = None) -> str | None:
    """Infer only a safe format label from private source metadata."""
    for candidate in (path, source.get("original_filename")):
        if isinstance(candidate, (Path, str)):
            suffix = Path(candidate).suffix.casefold()
            if suffix in {".pdf", ".pptx"}:
                return suffix
    mime_type = source.get("mime_type")
    if mime_type == "application/pdf":
        return ".pdf"
    if mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return ".pptx"
    return None


def _required_visual_locator(
    source_format: str | None, *, slide: object, page: object
) -> tuple[str | None, int | None]:
    if source_format == ".pdf" and type(page) is int and page > 0:
        return "page", page
    if source_format == ".pptx" and type(slide) is int and slide > 0:
        return "slide", slide
    return None, None


def _normalize_image(data: bytes) -> tuple[str, bytes] | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width <= 0 or image.height <= 0:
                return None
            if image.width * image.height > MAX_VISUAL_SOURCE_PIXELS:
                raise VisualEvidenceLimitError("source image dimensions exceed the review limit")
            image_format = str(image.format or "").upper()
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (MAX_VISUAL_IMAGE_DIMENSION, MAX_VISUAL_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            elif image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")

            output = io.BytesIO()
            if image_format in {"JPEG", "JPG"}:
                image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
                mime_type = "image/jpeg"
            else:
                image.save(output, format="PNG", optimize=True)
                mime_type = "image/png"

            if output.tell() > MAX_VISUAL_IMAGE_BYTES:
                output = io.BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
                mime_type = "image/jpeg"
            if output.tell() > MAX_VISUAL_IMAGE_BYTES:
                return None
            return mime_type, output.getvalue()
    except VisualEvidenceLimitError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
