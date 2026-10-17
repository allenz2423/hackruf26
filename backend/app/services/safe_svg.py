"""Sanitize model-authored SVG into a bounded, script-free vector document."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
MAX_SVG_CHARACTERS = 60_000
MAX_SVG_ELEMENTS = 600
MAX_SVG_DEPTH = 32
MAX_SVG_TEXT_CHARACTERS = 8_000

_SVG_TAGS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "marker",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "pattern",
        "symbol",
        "use",
        "animate",
        "animateTransform",
        "animateMotion",
        "mpath",
        "set",
        "filter",
        "feGaussianBlur",
        "feOffset",
        "feBlend",
        "feColorMatrix",
        "feComponentTransfer",
        "feFuncR",
        "feFuncG",
        "feFuncB",
        "feFuncA",
        "feFlood",
        "feComposite",
        "feMerge",
        "feMergeNode",
    }
)
_GLOBAL_ATTRIBUTES = frozenset(
    {
        "id",
        "transform",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "opacity",
        "vector-effect",
        "clip-path",
        "mask",
        "filter",
        "role",
        "aria-label",
        "aria-labelledby",
        "aria-describedby",
        "focusable",
        "display",
        "visibility",
        "color",
        "color-interpolation",
        "color-interpolation-filters",
        "shape-rendering",
        "text-rendering",
        "paint-order",
        "pointer-events",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
        "alignment-baseline",
        "letter-spacing",
        "word-spacing",
        "direction",
        "unicode-bidi",
        "clip-rule",
        "href",
    }
)
_ELEMENT_ATTRIBUTES: dict[str, frozenset[str]] = {
    "svg": frozenset({"viewBox", "width", "height", "preserveAspectRatio", "version"}),
    "path": frozenset({"d", "pathLength"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}),
    "text": frozenset({"x", "y", "dx", "dy", "rotate", "textLength", "lengthAdjust"}),
    "tspan": frozenset({"x", "y", "dx", "dy", "rotate", "textLength", "lengthAdjust"}),
    "marker": frozenset(
        {
            "viewBox",
            "refX",
            "refY",
            "markerWidth",
            "markerHeight",
            "markerUnits",
            "orient",
            "preserveAspectRatio",
        }
    ),
    "linearGradient": frozenset(
        {"x1", "y1", "x2", "y2", "gradientUnits", "gradientTransform", "spreadMethod"}
    ),
    "radialGradient": frozenset(
        {"cx", "cy", "r", "fx", "fy", "fr", "gradientUnits", "gradientTransform", "spreadMethod"}
    ),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
    "clipPath": frozenset({"clipPathUnits"}),
    "mask": frozenset({"maskUnits", "maskContentUnits", "x", "y", "width", "height"}),
    "pattern": frozenset(
        {
            "patternUnits",
            "patternContentUnits",
            "x",
            "y",
            "width",
            "height",
            "patternTransform",
            "viewBox",
            "preserveAspectRatio",
        }
    ),
    "symbol": frozenset({"viewBox", "preserveAspectRatio"}),
    "use": frozenset({"x", "y", "width", "height"}),
    "animate": frozenset(
        {
            "attributeName",
            "attributeType",
            "begin",
            "dur",
            "end",
            "min",
            "max",
            "restart",
            "repeatCount",
            "repeatDur",
            "fill",
            "calcMode",
            "values",
            "from",
            "to",
            "by",
            "keyTimes",
            "keySplines",
            "additive",
            "accumulate",
        }
    ),
    "animateTransform": frozenset(
        {
            "attributeName",
            "attributeType",
            "type",
            "begin",
            "dur",
            "end",
            "min",
            "max",
            "restart",
            "repeatCount",
            "repeatDur",
            "fill",
            "calcMode",
            "values",
            "from",
            "to",
            "by",
            "keyTimes",
            "keySplines",
            "additive",
            "accumulate",
        }
    ),
    "animateMotion": frozenset(
        {
            "begin",
            "dur",
            "end",
            "min",
            "max",
            "restart",
            "repeatCount",
            "repeatDur",
            "fill",
            "calcMode",
            "values",
            "from",
            "to",
            "by",
            "keyTimes",
            "keySplines",
            "additive",
            "accumulate",
            "path",
            "rotate",
        }
    ),
    "mpath": frozenset(),
    "set": frozenset(
        {
            "attributeName",
            "attributeType",
            "begin",
            "dur",
            "end",
            "min",
            "max",
            "restart",
            "repeatCount",
            "repeatDur",
            "fill",
            "to",
        }
    ),
    "filter": frozenset(
        {
            "x",
            "y",
            "width",
            "height",
            "filterUnits",
            "primitiveUnits",
            "color-interpolation-filters",
        }
    ),
    "feGaussianBlur": frozenset({"in", "stdDeviation", "edgeMode", "result"}),
    "feOffset": frozenset({"in", "dx", "dy", "result"}),
    "feBlend": frozenset({"in", "in2", "mode", "result"}),
    "feColorMatrix": frozenset({"in", "type", "values", "result"}),
    "feComponentTransfer": frozenset({"in", "result"}),
    "feFuncR": frozenset(
        {"type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"}
    ),
    "feFuncG": frozenset(
        {"type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"}
    ),
    "feFuncB": frozenset(
        {"type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"}
    ),
    "feFuncA": frozenset(
        {"type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"}
    ),
    "feFlood": frozenset({"flood-color", "flood-opacity", "result"}),
    "feComposite": frozenset({"in", "in2", "operator", "k1", "k2", "k3", "k4", "result"}),
    "feMerge": frozenset(),
    "feMergeNode": frozenset({"in"}),
}
_LENGTH_ATTRIBUTES = frozenset(
    {
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "dx",
        "dy",
        "width",
        "height",
        "refX",
        "refY",
        "markerWidth",
        "markerHeight",
        "stroke-width",
        "stroke-miterlimit",
        "stroke-dashoffset",
        "letter-spacing",
        "word-spacing",
        "font-size",
        "textLength",
        "pathLength",
        "stdDeviation",
        "offset",
        "stop-opacity",
        "flood-opacity",
        "fill-opacity",
        "stroke-opacity",
        "opacity",
        "k1",
        "k2",
        "k3",
        "k4",
        "slope",
        "intercept",
        "amplitude",
        "exponent",
    }
)
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NUMBER_RE = re.compile(_NUMBER)
_NUMBER_LIST = re.compile(rf"^\s*{_NUMBER}(?:[\s,]+{_NUMBER})*\s*$")
_LENGTH = re.compile(rf"^\s*({_NUMBER})(?:px|pt|pc|mm|cm|in|em|ex|%)?\s*$", re.I)
_PATH_DATA = re.compile(r"^[\d\s.,+\-eE MmZzLlHhVvCcSsQqTtAa]+$")
_COLOR = re.compile(
    r"^(?:none|currentColor|transparent|#[\da-fA-F]{3,8}|[A-Za-z]{1,32}|"
    r"(?:rgb|rgba|hsl|hsla)\([\d\s.,%+-]{1,100}\))$",
    re.I,
)
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,95}$")
_TIMING = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:ms|s)?|indefinite)(?:\s*;\s*(?:\d+(?:\.\d+)?(?:ms|s)?|indefinite))*$"
)
_SAFE_ANIMATED_ATTRIBUTES = frozenset(
    {
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "transform",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dashoffset",
        "stroke-dasharray",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "visibility",
    }
)


def _bounded_numeric_tokens(value: str, limit: float = 1_000_000) -> bool:
    try:
        numbers = [float(token) for token in _NUMBER_RE.findall(value)]
    except ValueError:
        return False
    return bool(numbers) and all(
        math.isfinite(number) and abs(number) <= limit for number in numbers
    )


def _parse_key_times(value: str, expected_count: int | None = None) -> list[float] | None:
    """Parse the semicolon-delimited SMIL keyTimes list and check its range/order."""
    parts = [item.strip() for item in value.split(";")]
    if len(parts) < 2 or (expected_count is not None and len(parts) != expected_count):
        return None
    try:
        values = [float(item) for item in parts]
    except ValueError:
        return None
    if (
        not all(math.isfinite(item) and 0 <= item <= 1 for item in values)
        or values[0] != 0
        or values[-1] != 1
        or any(left > right for left, right in zip(values, values[1:], strict=False))
    ):
        return None
    return values


def _local_name(qualified_name: str) -> tuple[str | None, str]:
    if qualified_name.startswith("{"):
        namespace, local_name = qualified_name[1:].split("}", 1)
        return namespace, local_name
    return None, qualified_name


def _safe_attribute_value(name: str, value: str, element: str) -> bool:
    value = value.strip()
    if len(value) > 20_000 or any(ord(char) < 0x20 and char not in "\t\n\r" for char in value):
        return False
    if name in {"id", "result"}:
        return bool(_SAFE_ID.fullmatch(value))
    if name in {"d", "path"}:
        return (
            len(value) <= 20_000
            and bool(_PATH_DATA.fullmatch(value))
            and _bounded_numeric_tokens(value)
        )
    if name == "keyTimes":
        return _parse_key_times(value) is not None
    if name == "points" or name in {"viewBox", "keySplines", "tableValues", "rotate"}:
        return bool(_NUMBER_LIST.fullmatch(value)) and _bounded_numeric_tokens(value)
    if name == "transform" or name in {"gradientTransform", "patternTransform"}:
        valid_transform = bool(
            re.fullmatch(
                rf"\s*(?:(?:matrix|translate|scale|rotate|skewX|skewY)\(\s*{_NUMBER}(?:[\s,]+{_NUMBER})*\s*\)\s*)+",
                value,
                re.I,
            )
        )
        return valid_transform and _bounded_numeric_tokens(value)
    if name in _LENGTH_ATTRIBUTES:
        match = _LENGTH.fullmatch(value)
        if not match:
            return False
        try:
            limit = 1_000 if name == "stdDeviation" else 100_000
            return math.isfinite(float(match.group(1))) and abs(float(match.group(1))) <= limit
        except ValueError:
            return False
    if name in {"fill", "stroke", "stop-color", "flood-color", "color"}:
        if element in {"animate", "animateTransform", "animateMotion", "set"} and name == "fill":
            return value in {"freeze", "remove"}
        return bool(
            _COLOR.fullmatch(value) or re.fullmatch(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)", value)
        )
    if name in {"clip-path", "mask", "filter", "marker-start", "marker-mid", "marker-end"}:
        return value == "none" or bool(re.fullmatch(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)", value))
    if name == "stroke-dasharray":
        return value == "none" or (
            bool(_NUMBER_LIST.fullmatch(value)) and _bounded_numeric_tokens(value)
        )
    if name == "href":
        return value.startswith("#") and bool(_SAFE_ID.fullmatch(value[1:]))
    if name in {"in", "in2"}:
        return value in {
            "SourceGraphic",
            "SourceAlpha",
            "BackgroundImage",
            "BackgroundAlpha",
            "FillPaint",
            "StrokePaint",
        } or bool(_SAFE_ID.fullmatch(value))
    if name in {"begin", "end", "min", "max"}:
        return bool(_TIMING.fullmatch(value))
    if name in {"dur", "repeatDur"}:
        if value == "indefinite":
            return name == "repeatDur"
        match = re.fullmatch(rf"({_NUMBER})(ms|s)?", value)
        return bool(
            match and math.isfinite(float(match.group(1))) and 0 < float(match.group(1)) <= 120
        )
    if name == "repeatCount":
        if value == "indefinite":
            return True
        try:
            count = float(value)
            return math.isfinite(count) and 0 < count <= 100
        except ValueError:
            return False
    if name == "attributeName":
        return value in _SAFE_ANIMATED_ATTRIBUTES
    if name == "attributeType":
        return value in {"XML", "CSS"}
    if name in {"values", "from", "to", "by", "stdDeviation"}:
        return len(value) <= 20_000 and not any(char in value for char in "<>\"'")
    if name in {"aria-label", "aria-labelledby", "aria-describedby"}:
        return len(value) <= 500 and not any(ord(char) < 0x20 for char in value)
    if name == "font-family":
        return bool(re.fullmatch(r"[A-Za-z0-9 _,'\"-]{1,100}", value))
    if name in {
        "role",
        "font-style",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
        "alignment-baseline",
        "direction",
        "unicode-bidi",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "fill-rule",
        "clip-rule",
        "vector-effect",
        "display",
        "visibility",
        "focusable",
        "shape-rendering",
        "text-rendering",
        "paint-order",
        "pointer-events",
        "preserveAspectRatio",
        "gradientUnits",
        "spreadMethod",
        "markerUnits",
        "clipPathUnits",
        "maskUnits",
        "maskContentUnits",
        "patternUnits",
        "patternContentUnits",
        "filterUnits",
        "primitiveUnits",
        "edgeMode",
        "mode",
        "type",
        "operator",
        "calcMode",
        "restart",
        "additive",
        "accumulate",
        "lengthAdjust",
        "orient",
        "version",
    }:
        return len(value) <= 100 and bool(re.fullmatch(r"[A-Za-z0-9 _.,+\-]*", value))
    return False


def _animation_is_complete(source: ET.Element, tag: str) -> bool:
    """Drop animation nodes that cannot produce a bounded transition."""
    attribute = source.get("attributeName", "").strip()
    if tag == "set":
        value = source.get("to", "")
        return attribute in _SAFE_ANIMATED_ATTRIBUTES and _animation_value_is_valid(
            attribute, value, tag, source
        )

    if tag == "animateMotion":
        duration = source.get("dur", "")
        path = source.get("path", "")
        has_path = bool(path and _safe_attribute_value("path", path, tag)) or any(
            _local_name(child.tag)[1] == "mpath"
            and _safe_attribute_value(
                "href", child.get("href") or child.get(f"{{{XLINK_NAMESPACE}}}href", ""), "mpath"
            )
            for child in list(source)
        )
        return bool(duration and _safe_attribute_value("dur", duration, tag) and has_path)

    if attribute not in _SAFE_ANIMATED_ATTRIBUTES:
        return False
    duration = source.get("dur", "")
    if not duration or not _safe_attribute_value("dur", duration, tag):
        return False

    raw_values = source.get("values")
    if tag == "animateTransform":
        if attribute != "transform" or source.get("type") not in {
            "translate", "scale", "rotate", "skewX", "skewY",
        }:
            return False
        expected_lengths = {
            "translate": {1, 2}, "scale": {1, 2}, "rotate": {1, 3},
            "skewX": {1}, "skewY": {1},
        }
        expected = expected_lengths[source.get("type", "")]

        def valid_transform_value(value: str) -> bool:
            return (
                bool(_NUMBER_LIST.fullmatch(value.strip()))
                and len(_NUMBER_RE.findall(value)) in expected
                and _bounded_numeric_tokens(value)
            )

        if raw_values:
            values = raw_values.split(";")
            return len(values) >= 2 and all(valid_transform_value(item) for item in values)
        from_value = source.get("from", "")
        to_value = source.get("to", "")
        return bool(
            from_value
            and to_value
            and valid_transform_value(from_value)
            and valid_transform_value(to_value)
        )

    if raw_values:
        values = raw_values.split(";")
        return len(values) >= 2 and all(
            _animation_value_is_valid(attribute, value, tag, source) for value in values
        )
    from_value = source.get("from", "")
    to_value = source.get("to", "")
    by_value = source.get("by", "")
    candidates = (
        (from_value, to_value),
        (from_value, by_value),
        (to_value, by_value),
        (by_value, by_value) if by_value and not from_value and not to_value else ("", ""),
    )
    return any(
        left
        and right
        and _animation_value_is_valid(attribute, left, tag, source)
        and _animation_value_is_valid(attribute, right, tag, source)
        for left, right in candidates
    )


def _animation_value_is_valid(attribute: str, value: str, tag: str, source: ET.Element) -> bool:
    value = value.strip()
    if attribute in {"fill", "stroke"}:
        return bool(
            _COLOR.fullmatch(value)
            or re.fullmatch(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)", value)
        )
    if attribute == "transform":
        if tag == "animateTransform":
            transform_type = source.get("type", "")
            allowed_lengths = {
                "translate": {1, 2}, "scale": {1, 2}, "rotate": {1, 3},
                "skewX": {1}, "skewY": {1},
            }.get(transform_type, set())
            return bool(
                _NUMBER_LIST.fullmatch(value)
                and len(_NUMBER_RE.findall(value)) in allowed_lengths
                and _bounded_numeric_tokens(value)
            )
        return _safe_attribute_value("transform", value, tag)
    if attribute == "d":
        return bool(_PATH_DATA.fullmatch(value) and _bounded_numeric_tokens(value))
    return bool(_NUMBER_LIST.fullmatch(value) and _bounded_numeric_tokens(value))


def _canonicalize_animation(target: ET.Element, source: ET.Element) -> None:
    """Make value timing explicit and retain a finite animation's final frame."""
    raw_values = source.get("values")
    if raw_values:
        count = len(raw_values.split(";"))
        if _parse_key_times(target.get("keyTimes", ""), count) is None:
            target.set(
                "keyTimes",
                ";".join(f"{index / (count - 1):g}" for index in range(count)),
            )
    if (
        target.get("fill") is None
        and source.get("repeatCount") != "indefinite"
        and source.get("repeatDur") != "indefinite"
    ):
        target.set("fill", "freeze")


def sanitize_svg(markup: str) -> str:
    """Return a safe, self-contained SVG string or raise ``ValueError``.

    Active content, foreign objects, external URLs, CSS, event handlers, and unknown
    elements/attributes are removed. Standard SVG geometry, gradients, clipping, masks,
    filters, local references, and bounded SMIL animations remain available.
    """
    if not isinstance(markup, str) or not markup.strip() or len(markup) > MAX_SVG_CHARACTERS:
        raise ValueError("SVG source is empty or exceeds the supported limit.")
    if re.search(r"<!\s*(?:doctype|entity)", markup, re.I):
        raise ValueError("SVG document declarations are not supported.")
    try:
        original = ET.fromstring(markup)
    except Exception as exc:
        raise ValueError("SVG source is not valid XML.") from exc

    namespace, root_name = _local_name(original.tag)
    if root_name != "svg" or namespace not in {None, "", SVG_NAMESPACE}:
        raise ValueError("SVG source must have an SVG root element.")

    counters = {"elements": 0, "text": 0, "animations": 0}
    id_map: dict[str, str] = {}
    ET.register_namespace("", SVG_NAMESPACE)

    def copy_node(source: ET.Element, depth: int) -> ET.Element | None:
        if depth > MAX_SVG_DEPTH:
            raise ValueError("SVG nesting exceeds the supported limit.")
        source_namespace, tag = _local_name(source.tag)
        if source_namespace not in {None, "", SVG_NAMESPACE} or tag not in _SVG_TAGS:
            return None
        is_animation = tag in {"animate", "animateTransform", "animateMotion", "set"}
        if is_animation and not _animation_is_complete(source, tag):
            return None
        counters["elements"] += 1
        if counters["elements"] > MAX_SVG_ELEMENTS:
            raise ValueError("SVG has too many elements.")
        if tag in {"animate", "animateTransform", "animateMotion", "set"}:
            counters["animations"] += 1
            if counters["animations"] > 24:
                raise ValueError("SVG has too many animation instructions.")

        target = ET.Element(f"{{{SVG_NAMESPACE}}}{tag}")
        allowed = _GLOBAL_ATTRIBUTES | _ELEMENT_ATTRIBUTES.get(tag, frozenset())
        for qualified_name, raw_value in source.attrib.items():
            attr_namespace, name = _local_name(qualified_name)
            if name == "href" and attr_namespace in {None, "", XLINK_NAMESPACE}:
                name = "href"
            elif attr_namespace is not None:
                continue
            if (
                tag == "svg"
                and name == "viewBox"
                and not _safe_attribute_value(name, raw_value, tag)
            ):
                raise ValueError("SVG viewBox is invalid or exceeds the supported limits.")
            if name not in allowed or name.lower().startswith("on"):
                continue
            if tag in {"animate", "animateTransform", "animateMotion", "set"}:
                if name == "attributeName" and raw_value.strip() not in _SAFE_ANIMATED_ATTRIBUTES:
                    continue
            value = raw_value.strip()
            if tag in {"animate", "animateTransform", "animateMotion", "set"} and name in {
                "values",
                "from",
                "to",
                "by",
            }:
                animated_attribute = source.get("attributeName", "")
                animation_values = value.split(";") if name == "values" else [value]
                if animated_attribute in {"fill", "stroke"}:
                    valid_animation = all(
                        _COLOR.fullmatch(item.strip())
                        or re.fullmatch(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)", item.strip())
                        for item in animation_values
                    )
                elif tag == "animateTransform":
                    transform_type = source.get("type", "")
                    allowed_lengths = {
                        "translate": {1, 2}, "scale": {1, 2}, "rotate": {1, 3},
                        "skewX": {1}, "skewY": {1},
                    }.get(transform_type, set())
                    valid_animation = all(
                        _NUMBER_LIST.fullmatch(item.strip())
                        and len(_NUMBER_RE.findall(item)) in allowed_lengths
                        and _bounded_numeric_tokens(item)
                        for item in animation_values
                    )
                elif animated_attribute == "transform":
                    valid_animation = all(
                        _safe_attribute_value("transform", item, tag) for item in animation_values
                    )
                elif animated_attribute == "d":
                    valid_animation = all(
                        _PATH_DATA.fullmatch(item.strip()) for item in animation_values
                    )
                else:
                    valid_animation = all(
                        _NUMBER_LIST.fullmatch(item.strip()) for item in animation_values
                    )
                if not valid_animation:
                    continue
            if not _safe_attribute_value(name, value, tag):
                continue
            if name == "id":
                if value in id_map:
                    continue
                id_map[value] = value
            target.set(name, value)

        if tag in {"animate", "animateTransform", "animateMotion", "set"}:
            _canonicalize_animation(target, source)

        if tag in {"title", "desc", "text", "tspan"}:
            text = source.text or ""
            counters["text"] += len(text)
            if counters["text"] > MAX_SVG_TEXT_CHARACTERS:
                raise ValueError("SVG text exceeds the supported limit.")
            target.text = text
        for source_child in list(source):
            child = copy_node(source_child, depth + 1)
            if child is not None:
                target.append(child)
                if tag in {"title", "desc", "text", "tspan"} and source_child.tail:
                    counters["text"] += len(source_child.tail)
                    if counters["text"] > MAX_SVG_TEXT_CHARACTERS:
                        raise ValueError("SVG text exceeds the supported limit.")
                    child.tail = source_child.tail
            elif tag in {"title", "desc", "text", "tspan"} and source_child.tail:
                target.text = (target.text or "") + source_child.tail
        return target

    root = copy_node(original, 0)
    if root is None:
        raise ValueError("SVG source could not be rendered.")

    view_box = root.get("viewBox")
    if view_box:
        try:
            values = [float(item) for item in re.split(r"[\s,]+", view_box.strip())]
        except ValueError:
            values = []
        if (
            len(values) != 4
            or not all(math.isfinite(value) and abs(value) <= 1_000_000 for value in values)
            or values[2] <= 0
            or values[3] <= 0
            or values[2] > 100_000
            or values[3] > 100_000
        ):
            raise ValueError("SVG viewBox is invalid or exceeds the supported limits.")
    else:

        def inferred_dimension(name: str, default: float) -> float:
            match = _LENGTH.fullmatch(root.get(name, ""))
            if not match:
                return default
            value = float(match.group(1))
            return min(10_000, max(1, value))

        root.set(
            "viewBox",
            f"0 0 {inferred_dimension('width', 960):g} {inferred_dimension('height', 540):g}",
        )

    ids = set(id_map)
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name == "id":
                continue
            references = re.findall(
                r"url\(\s*#([A-Za-z_][\w.:-]*)\s*\)|^#([A-Za-z_][\w.:-]*)$", value
            )
            if "url(" in value or name == "href":
                targets = [left or right for left, right in references]
                if not targets or any(target_id not in ids for target_id in targets):
                    del element.attrib[name]
            elif name in {"aria-labelledby", "aria-describedby"}:
                targets = value.split()
                if not all(target_id in ids for target_id in targets):
                    del element.attrib[name]

    # A <use> reference cycle can make a renderer recurse indefinitely, including
    # when a <use> points to a group that contains that same <use> indirectly.
    id_elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    use_graph = {
        element_id: {
            child.get("href", "").removeprefix("#")
            for child in element.iter()
            if child.tag == f"{{{SVG_NAMESPACE}}}use" and child.get("href")
        }
        for element_id, element in id_elements.items()
    }
    visitation: dict[str, int] = {}

    def has_cycle(element_id: str) -> bool:
        state = visitation.get(element_id, 0)
        if state == 1:
            return True
        if state == 2:
            return False
        visitation[element_id] = 1
        if any(
            has_cycle(target_id)
            for target_id in use_graph.get(element_id, ())
            if target_id in id_elements
        ):
            return True
        visitation[element_id] = 2
        return False

    if any(has_cycle(element_id) for element_id in id_elements):
        parents = {child: parent for parent in root.iter() for child in list(parent)}
        for element in root.iter():
            if element.tag == f"{{{SVG_NAMESPACE}}}use":
                parent = parents.get(element)
                if parent is not None:
                    parent.remove(element)

    result = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    if len(result) > MAX_SVG_CHARACTERS:
        raise ValueError("Sanitized SVG exceeds the supported limit.")
    return result
