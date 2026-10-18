"""Render sanitized study-guide SVG for the authoring model's visual self-check."""

from __future__ import annotations

import io
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass

from PIL import Image

from app.services.safe_svg import SVG_NAMESPACE, sanitize_svg

MAX_PREVIEW_WIDTH = 1280
MAX_PREVIEW_HEIGHT = 960
PHONE_CARD_PREVIEW_WIDTH = 360
PHONE_CARD_PREVIEW_HEIGHT = 420
MAX_PREVIEW_PNG_BYTES = 8 * 1024 * 1024
PREVIEW_TIMEOUT_SECONDS = 12


class SvgPreviewRenderError(RuntimeError):
    """The sanitized SVG could not be rendered into a bounded preview image."""


@dataclass(frozen=True)
class SvgPhoneTextAudit:
    """Approximate visible SVG text size at the reader's 360 px phone-card width."""

    rendered_text_runs: int
    undersized_text_runs: int
    smallest_text_px: float | None
    minimum_text_px: float
    scale: float

    @property
    def has_small_text(self) -> bool:
        return self.undersized_text_runs > 0

    def prompt_diagnostic(self) -> str | None:
        if not self.has_small_text or self.smallest_text_px is None:
            return None
        return (
            f"{self.undersized_text_runs} of {self.rendered_text_runs} visible text runs render "
            f"below {self.minimum_text_px:g} CSS px in the 360×420 phone-card preview; the "
            f"smallest is about {self.smallest_text_px:g} px. Shorten labels or re-layout the "
            "figure and increase their rendered size; do not shrink text to fit more content."
        )


_FONT_SIZE = re.compile(
    r"^\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*(px|pt|pc|mm|cm|in|em|ex|%)?\s*$",
    re.I,
)


def audit_svg_phone_text(
    markup: str,
    *,
    max_width: int = PHONE_CARD_PREVIEW_WIDTH,
    max_height: int = PHONE_CARD_PREVIEW_HEIGHT,
    minimum_text_px: float = 12.0,
) -> SvgPhoneTextAudit:
    """Measure text runs after SVG scaling, using sanitized markup the reader can actually render.

    This is a small-size readability guard, not a text-layout engine. It follows inherited font
    sizes and uniform/nonuniform `scale()` transforms. Exact clipping and overlap still require
    inspection of the rendered preview.
    """
    safe_markup = sanitize_svg(markup)
    root = ET.fromstring(safe_markup)
    raw_view_box = root.get("viewBox", "")
    values = [float(value) for value in re.split(r"[\s,]+", raw_view_box.strip())]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise ValueError("SVG viewBox is invalid.")
    scale = min(max_width / values[2], max_height / values[3])
    rendered_sizes: list[float] = []

    def font_size_in_user_units(raw: str | None, inherited: float) -> float:
        if not raw:
            return inherited
        match = _FONT_SIZE.fullmatch(raw)
        if not match:
            return inherited
        value = float(match.group(1))
        unit = (match.group(2) or "").casefold()
        conversion = {
            "": 1.0,
            "px": 1.0,
            "pt": 96 / 72,
            "pc": 16.0,
            "mm": 96 / 25.4,
            "cm": 96 / 2.54,
            "in": 96.0,
        }
        if unit in conversion:
            return value * conversion[unit]
        if unit == "em":
            return value * inherited
        if unit == "ex":
            return value * inherited * 0.5
        if unit == "%":
            return value * inherited / 100
        return inherited

    def element_scale(raw: str | None) -> float:
        result = 1.0
        for match in re.finditer(r"scale\(\s*([^)]*)\)", raw or "", re.I):
            factors = [
                float(item)
                for item in re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", match[1])
            ]
            if factors:
                result *= (
                    min(abs(factors[0]), abs(factors[1]))
                    if len(factors) > 1
                    else abs(factors[0])
                )
        return result

    def walk(
        element: ET.Element,
        inherited_font_size: float = 16.0,
        inherited_scale: float = 1.0,
        hidden: bool = False,
    ) -> None:
        font_size = font_size_in_user_units(element.get("font-size"), inherited_font_size)
        own_visibility = element.get("visibility", "visible").casefold()
        is_hidden = (
            hidden
            or element.get("display", "").casefold() == "none"
            or own_visibility in {"hidden", "collapse"}
            or element.get("opacity", "1").strip() == "0"
        )
        transform_scale = inherited_scale * element_scale(element.get("transform"))
        tag = element.tag.rsplit("}", 1)[-1]
        if not is_hidden and tag in {"text", "tspan"}:
            if (element.text or "").strip():
                rendered_sizes.append(font_size * transform_scale * scale)
        for child in element:
            walk(child, font_size, transform_scale, is_hidden)
            if (
                not is_hidden
                and tag in {"text", "tspan"}
                and (child.tail or "").strip()
            ):
                rendered_sizes.append(font_size * transform_scale * scale)

    walk(root)
    small_sizes = [size for size in rendered_sizes if size < minimum_text_px]
    return SvgPhoneTextAudit(
        rendered_text_runs=len(rendered_sizes),
        undersized_text_runs=len(small_sizes),
        smallest_text_px=round(min(rendered_sizes), 1) if rendered_sizes else None,
        minimum_text_px=minimum_text_px,
        scale=round(scale, 4),
    )


def phone_text_audit_not_worse(before: SvgPhoneTextAudit, after: SvgPhoneTextAudit) -> bool:
    """Reject an SVG-only edit that makes small-screen text less readable."""
    if after.undersized_text_runs > before.undersized_text_runs:
        return False
    if before.smallest_text_px is not None and after.smallest_text_px is None:
        return False
    if before.smallest_text_px is None:
        return True
    if after.smallest_text_px is None:
        return True
    return after.smallest_text_px >= before.smallest_text_px


@dataclass(frozen=True)
class SvgAnimationPreview:
    """Phone-scale stills sampled from supported native SVG animation instructions."""

    frames: tuple[tuple[str, bytes], ...]
    unsupported_types: tuple[str, ...]


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_ANIMATION_TAGS = frozenset({"animate", "animateTransform", "animateMotion", "set"})
_CLOCK_VALUE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)(ms|s)?\s*$", re.I)


def _clock_seconds(value: str | None) -> float | None:
    if value is None:
        return 0.0
    match = _CLOCK_VALUE.fullmatch(value)
    if not match:
        return None
    amount = float(match.group(1))
    return amount / 1000 if (match.group(2) or "s").casefold() == "ms" else amount


def _animation_preview_limitations(root: ET.Element, animations: list[ET.Element]) -> set[str]:
    limitations: set[str] = set()
    parents = {child: parent for parent in root.iter() for child in parent}
    active = [
        element
        for element in animations
        if element.tag.rsplit("}", 1)[-1] != "set"
    ]
    durations = {_clock_seconds(element.get("dur")) for element in active}
    if len(durations) > 1 or None in durations:
        limitations.add("mixed_or_unreadable_animation_durations")
    starts = {_clock_seconds(element.get("begin")) for element in animations}
    if len(starts) > 1:
        limitations.add("staggered_animation_starts")
    if None in starts:
        limitations.add("unreadable_animation_start")
    if any(start not in {0.0, None} for start in starts):
        limitations.add("delayed_animation_start")
    if any(
        element.get(name) is not None
        for element in animations
        for name in ("end", "min", "max", "repeatDur")
    ):
        limitations.add("advanced_animation_duration_not_sampled")
    if any(element.get("fill") == "remove" for element in active):
        limitations.add("remove_fill_end_state_not_sampled")
    if any(element.get("calcMode") == "spline" for element in active):
        limitations.add("spline_interpolation_approximated_as_linear")
    if any(element.get("calcMode") == "paced" for element in active):
        limitations.add("paced_interpolation_not_sampled")
    if any(element.tag.rsplit("}", 1)[-1] == "animateMotion" for element in active):
        limitations.add("animateMotion_path_not_sampled")
    if any(element.tag.rsplit("}", 1)[-1] == "set" for element in animations):
        limitations.add("set_element_timing_not_sampled")
    for element in active:
        parent = parents.get(element)
        attribute = element.get("attributeName", "")
        values = _animation_values(element, parent.get(attribute) if parent is not None else None)
        numeric = [_numeric_values(value) for value in values]
        compatible = bool(numeric) and all(value is not None for value in numeric)
        if compatible:
            lengths = {len(value) for value in numeric if value is not None}
            compatible = len(lengths) == 1
        if not compatible and element.get("calcMode") != "discrete":
            limitations.add("non_numeric_interpolation_not_sampled")
    return limitations


def _animation_values(element: ET.Element, current_value: str | None) -> list[str]:
    values = element.get("values")
    if values:
        return [value.strip() for value in values.split(";")]
    if element.tag.rsplit("}", 1)[-1] == "set":
        return [current_value or "", element.get("to", "")]
    start = element.get("from", current_value or "")
    end = element.get("to")
    by = element.get("by")
    if end is None and by is not None:
        start_numbers = _numeric_values(start)
        by_numbers = _numeric_values(by)
        if start_numbers is None or by_numbers is None or len(start_numbers) != len(by_numbers):
            return []
        end = _format_numeric_values(
            [left + right for left, right in zip(start_numbers, by_numbers, strict=True)]
        )
    if end is None:
        return []
    return [start, end]


def _numeric_values(value: str) -> list[float] | None:
    parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
    if not parts or not all(_NUMBER.fullmatch(part) for part in parts):
        return None
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None
    return values if all(math.isfinite(part) for part in values) else None


def _format_numeric_values(values: list[float]) -> str:
    return " ".join(f"{value:.6g}" for value in values)


def _animation_sample(element: ET.Element, current_value: str | None, progress: float) -> str | None:
    values = _animation_values(element, current_value)
    if not values or any(not value for value in values):
        return None
    if len(values) == 1:
        return values[0]

    raw_times = element.get("keyTimes", "")
    try:
        times = [float(part) for part in raw_times.split(";")]
    except ValueError:
        times = []
    if len(times) != len(values) or any(
        not math.isfinite(time) or time < 0 or time > 1 for time in times
    ):
        times = [index / (len(values) - 1) for index in range(len(values))]

    left_index = len(values) - 2
    for index, (_, right_time) in enumerate(zip(times, times[1:], strict=True)):
        if progress <= right_time:
            left_index = index
            break
    left_time = times[left_index]
    right_time = times[left_index + 1]
    fraction = (
        1.0
        if right_time <= left_time
        else min(1.0, max(0.0, (progress - left_time) / (right_time - left_time)))
    )
    numeric_values = [_numeric_values(value) for value in values]
    if any(value is None for value in numeric_values):
        return values[left_index if progress < 1 else -1]
    numeric = [value for value in numeric_values if value is not None]
    if any(len(value) != len(numeric[0]) for value in numeric):
        return values[left_index if progress < 1 else -1]
    if element.get("calcMode") == "discrete":
        return values[left_index if progress < 1 else -1]
    sampled = [
        numeric[left_index][axis]
        + (numeric[left_index + 1][axis] - numeric[left_index][axis]) * fraction
        for axis in range(len(numeric[0]))
    ]
    return _format_numeric_values(sampled)


def _render_static_animation_frame(
    original_root: ET.Element,
    progress: float,
    *,
    max_width: int,
    max_height: int,
) -> tuple[bytes | None, tuple[str, ...]]:
    root = deepcopy(original_root)
    animations = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in _ANIMATION_TAGS
    ]
    parents = {child: parent for parent in root.iter() for child in parent}
    unsupported: set[str] = set()
    for animation in animations:
        tag = animation.tag.rsplit("}", 1)[-1]
        parent = parents.get(animation)
        if parent is None:
            continue
        if tag == "animateMotion":
            unsupported.add(tag)
            parent.remove(animation)
            continue
        if tag == "set":
            if progress > 0:
                parent.set(animation.get("attributeName", ""), animation.get("to", ""))
            parent.remove(animation)
            continue

        attribute = animation.get("attributeName", "")
        sampled = _animation_sample(animation, parent.get(attribute), progress)
        if sampled is None:
            unsupported.add(tag)
        elif tag == "animateTransform":
            transform_type = animation.get("type", "")
            existing = parent.get("transform", "").strip()
            addition = f"{transform_type}({sampled})"
            if animation.get("additive") == "sum" and existing:
                parent.set("transform", f"{existing} {addition}")
            else:
                parent.set("transform", addition)
        else:
            parent.set(attribute, sampled)
        parent.remove(animation)

    ET.register_namespace("", SVG_NAMESPACE)
    static_svg = ET.tostring(root, encoding="unicode")
    try:
        return render_svg_preview_png(static_svg, max_width=max_width, max_height=max_height), tuple(sorted(unsupported))
    except SvgPreviewRenderError:
        raise


def render_svg_animation_previews(
    markup: str,
    *,
    max_width: int = PHONE_CARD_PREVIEW_WIDTH,
    max_height: int = PHONE_CARD_PREVIEW_HEIGHT,
) -> SvgAnimationPreview:
    """Render finite start/mid/end snapshots for bounded numeric SMIL motion.

    Native SVG path motion is retained by the runtime but not sampled here. Its presence is
    reported so the author preview does not imply that the moving path was inspected.
    """
    safe_markup = sanitize_svg(markup)
    root = ET.fromstring(safe_markup)
    animations = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in _ANIMATION_TAGS
    ]
    if not animations:
        return SvgAnimationPreview((), ())
    limitations = _animation_preview_limitations(root, animations)
    if limitations:
        # Do not turn incomplete or approximate motion into confident-looking stills.
        # The author receives the limitation and the ordinary static preview instead.
        return SvgAnimationPreview((), tuple(sorted(limitations)))
    sampleable_animations = [
        element
        for element in animations
        if element.tag.rsplit("}", 1)[-1] != "animateMotion"
    ]
    if not sampleable_animations:
        return SvgAnimationPreview((), tuple(sorted(limitations)))
    has_indefinite_loop = any(
        element.get("repeatCount") == "indefinite" or element.get("repeatDur") == "indefinite"
        for element in animations
    )
    samples = (
        (("start", 0.0), ("midpoint", 0.5), ("late loop", 0.9))
        if has_indefinite_loop
        else (("start", 0.0), ("midpoint", 0.5), ("end", 1.0))
    )
    frames: list[tuple[str, bytes]] = []
    unsupported = set(limitations)
    for label, progress in samples:
        png, unsupported_types = _render_static_animation_frame(
            root, progress, max_width=max_width, max_height=max_height
        )
        unsupported.update(unsupported_types)
        if png is not None:
            frames.append((label, png))
    return SvgAnimationPreview(tuple(frames), tuple(sorted(unsupported)))


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
            if (
                preview.height > max_height
                or preview.width * preview.height > max_width * max_height
            ):
                raise SvgPreviewRenderError("The SVG preview exceeds the image-size limit.")
            preview.verify()
    except (OSError, ValueError) as exc:
        raise SvgPreviewRenderError("The SVG preview is not a valid PNG image.") from exc

    return result.stdout
