const SVG_NS = "http://www.w3.org/2000/svg";
const XLINK_NS = "http://www.w3.org/1999/xlink";
const MAX_SVG_CHARACTERS = 60_000;
const MAX_ELEMENTS = 600;
const MAX_DEPTH = 32;
const MAX_TEXT = 8_000;

const ALLOWED_TAGS = new Set([
  "svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line",
  "polyline", "polygon", "text", "tspan", "marker", "linearGradient", "radialGradient",
  "stop", "clipPath", "mask", "pattern", "symbol", "use", "animate", "animateTransform",
  "animateMotion", "mpath", "set", "filter", "feGaussianBlur", "feOffset", "feBlend",
  "feColorMatrix", "feComponentTransfer", "feFuncR", "feFuncG", "feFuncB", "feFuncA",
  "feFlood", "feComposite", "feMerge", "feMergeNode"
]);

const GLOBAL_ATTRS = new Set([
  "id", "transform", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
  "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
  "stroke-dashoffset", "opacity", "vector-effect", "clip-path", "mask", "filter", "role",
  "aria-label", "aria-labelledby", "aria-describedby", "focusable", "display", "visibility",
  "color", "color-interpolation", "color-interpolation-filters", "shape-rendering", "text-rendering",
  "paint-order", "pointer-events", "font-family", "font-size", "font-style", "font-weight",
  "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing", "word-spacing",
  "direction", "unicode-bidi", "clip-rule", "href"
]);

const ELEMENT_ATTRS = {
  svg: ["viewBox", "width", "height", "preserveAspectRatio", "version"],
  path: ["d", "pathLength"],
  rect: ["x", "y", "width", "height", "rx", "ry"],
  circle: ["cx", "cy", "r"],
  ellipse: ["cx", "cy", "rx", "ry"],
  line: ["x1", "y1", "x2", "y2"],
  polyline: ["points"],
  polygon: ["points"],
  text: ["x", "y", "dx", "dy", "rotate", "textLength", "lengthAdjust"],
  tspan: ["x", "y", "dx", "dy", "rotate", "textLength", "lengthAdjust"],
  marker: ["viewBox", "refX", "refY", "markerWidth", "markerHeight", "markerUnits", "orient", "preserveAspectRatio"],
  linearGradient: ["x1", "y1", "x2", "y2", "gradientUnits", "gradientTransform", "spreadMethod"],
  radialGradient: ["cx", "cy", "r", "fx", "fy", "fr", "gradientUnits", "gradientTransform", "spreadMethod"],
  stop: ["offset", "stop-color", "stop-opacity"],
  clipPath: ["clipPathUnits"],
  mask: ["maskUnits", "maskContentUnits", "x", "y", "width", "height"],
  pattern: ["patternUnits", "patternContentUnits", "x", "y", "width", "height", "patternTransform", "viewBox", "preserveAspectRatio"],
  symbol: ["viewBox", "preserveAspectRatio"],
  use: ["x", "y", "width", "height"],
  animate: ["attributeName", "attributeType", "begin", "dur", "end", "min", "max", "restart", "repeatCount", "repeatDur", "fill", "calcMode", "values", "from", "to", "by", "keyTimes", "keySplines", "additive", "accumulate"],
  animateTransform: ["attributeName", "attributeType", "type", "begin", "dur", "end", "min", "max", "restart", "repeatCount", "repeatDur", "fill", "calcMode", "values", "from", "to", "by", "keyTimes", "keySplines", "additive", "accumulate"],
  animateMotion: ["begin", "dur", "end", "min", "max", "restart", "repeatCount", "repeatDur", "fill", "calcMode", "values", "from", "to", "by", "keyTimes", "keySplines", "additive", "accumulate", "path", "rotate"],
  mpath: [],
  set: ["attributeName", "attributeType", "begin", "dur", "end", "min", "max", "restart", "repeatCount", "repeatDur", "fill", "to"],
  filter: ["x", "y", "width", "height", "filterUnits", "primitiveUnits", "color-interpolation-filters"],
  feGaussianBlur: ["in", "stdDeviation", "edgeMode", "result"],
  feOffset: ["in", "dx", "dy", "result"],
  feBlend: ["in", "in2", "mode", "result"],
  feColorMatrix: ["in", "type", "values", "result"],
  feComponentTransfer: ["in", "result"],
  feFuncR: ["type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"],
  feFuncG: ["type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"],
  feFuncB: ["type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"],
  feFuncA: ["type", "tableValues", "slope", "intercept", "amplitude", "exponent", "offset"],
  feFlood: ["flood-color", "flood-opacity", "result"],
  feComposite: ["in", "in2", "operator", "k1", "k2", "k3", "k4", "result"],
  feMerge: [],
  feMergeNode: ["in"]
};

const NUMBER = String.raw`[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?`;
const NUMBER_LIST = new RegExp(`^\\s*${NUMBER}(?:[\\s,]+${NUMBER})*\\s*$`);
const LENGTH = new RegExp(`^\\s*(${NUMBER})(?:px|pt|pc|mm|cm|in|em|ex|%)?\\s*$`, "i");
const COLOR = /^(?:none|currentColor|transparent|#[\da-f]{3,8}|[a-z]{1,32}|(?:rgb|rgba|hsl|hsla)\([\d\s.,%+-]{1,100}\))$/i;
const PATH = /^[\d\s.,+\-eE MmZzLlHhVvCcSsQqTtAa]+$/;
const SAFE_ANIMATED = new Set([
  "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
  "transform", "fill", "stroke", "stroke-width", "stroke-dashoffset", "stroke-dasharray",
  "opacity", "fill-opacity", "stroke-opacity", "visibility"
]);
const LENGTH_ATTRS = new Set([
  "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "dx", "dy", "width",
  "height", "refX", "refY", "markerWidth", "markerHeight", "stroke-width", "stroke-dashoffset",
  "letter-spacing", "word-spacing", "font-size", "textLength", "pathLength", "stdDeviation", "stroke-miterlimit",
  "offset", "stop-opacity", "flood-opacity", "fill-opacity", "stroke-opacity", "opacity", "k1",
  "k2", "k3", "k4", "slope", "intercept", "amplitude", "exponent"
]);
const ANIMATION_TAGS = new Set(["animate", "animateTransform", "animateMotion", "set"]);

function parseSvg(markup) {
  if (typeof DOMParser === "undefined" || typeof XMLSerializer === "undefined") return null;
  if (typeof markup !== "string" || !markup.trim() || markup.length > MAX_SVG_CHARACTERS
    || /<!\s*(?:doctype|entity)/i.test(markup)) return null;
  const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
  const root = parsed.documentElement;
  if (!root || root.localName !== "svg" || ![SVG_NS, null, ""].includes(root.namespaceURI)
    || parsed.querySelector("parsererror")) return null;
  return { parsed, root };
}

function numericLength(value, name = "") {
  const match = LENGTH.exec(value);
  const limit = name === "stdDeviation" ? 1_000 : 100_000;
  return Boolean(match && Number.isFinite(Number(match[1])) && Math.abs(Number(match[1])) <= limit);
}

function boundedNumericTokens(value, limit = 1_000_000) {
  const tokens = String(value).match(new RegExp(NUMBER, "g")) || [];
  return tokens.length > 0 && tokens.every((token) => Number.isFinite(Number(token)) && Math.abs(Number(token)) <= limit);
}

function safeTransform(value) {
  const shapeIsSafe = new RegExp(`^\\s*(?:(?:matrix|translate|scale|rotate|skewX|skewY)\\(\\s*${NUMBER}(?:[\\s,]+${NUMBER})*\\s*\\)\\s*)+$`, "i").test(value);
  return shapeIsSafe && boundedNumericTokens(value);
}

function safeAttribute(name, value, tag) {
  const text = value.trim();
  if (text.length > 20_000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) return false;
  if (name === "id" || name === "result") return /^[A-Za-z_][A-Za-z\d_.:-]{0,95}$/.test(text);
  if (name === "d" || name === "path") return text.length <= 20_000 && PATH.test(text) && boundedNumericTokens(text);
  if (name === "keyTimes") return parseKeyTimes(text) !== null;
  if (["points", "viewBox", "keySplines", "tableValues", "rotate"].includes(name)) return NUMBER_LIST.test(text) && boundedNumericTokens(text);
  if (["transform", "gradientTransform", "patternTransform"].includes(name)) return safeTransform(text);
  if (LENGTH_ATTRS.has(name)) return numericLength(text, name);
  if (["fill", "stroke", "stop-color", "flood-color", "color"].includes(name)) {
    if (ANIMATION_TAGS.has(tag) && name === "fill") return ["freeze", "remove"].includes(text);
    return COLOR.test(text) || /^url\(\s*#[A-Za-z_][\w.:-]*\s*\)$/.test(text);
  }
  if (["clip-path", "mask", "filter", "marker-start", "marker-mid", "marker-end"].includes(name)) {
    return text === "none" || /^url\(\s*#[A-Za-z_][\w.:-]*\s*\)$/.test(text);
  }
  if (name === "stroke-dasharray") return text === "none" || (NUMBER_LIST.test(text) && boundedNumericTokens(text));
  if (name === "href") return /^#[A-Za-z_][\w.:-]*$/.test(text);
  if (["in", "in2"].includes(name)) {
    return ["SourceGraphic", "SourceAlpha", "BackgroundImage", "BackgroundAlpha", "FillPaint", "StrokePaint"].includes(text)
      || /^[A-Za-z_][A-Za-z\d_.:-]{0,95}$/.test(text);
  }
  if (["begin", "end", "min", "max"].includes(name)) return /^(?:\d+(?:\.\d+)?(?:ms|s)?|indefinite)(?:\s*;\s*(?:\d+(?:\.\d+)?(?:ms|s)?|indefinite))*$/.test(text);
  if (["dur", "repeatDur"].includes(name)) {
    if (text === "indefinite") return name === "repeatDur";
    const match = new RegExp(`^(${NUMBER})(?:ms|s)?$`).exec(text);
    return Boolean(match && Number(match[1]) > 0 && Number(match[1]) <= 120);
  }
  if (name === "repeatCount") return text === "indefinite" || (Number(text) > 0 && Number(text) <= 100);
  if (name === "attributeName") return SAFE_ANIMATED.has(text);
  if (name === "attributeType") return ["XML", "CSS"].includes(text);
  if (["values", "from", "to", "by", "stdDeviation"].includes(name)) return text.length <= 20_000 && !/[<>"']/.test(text);
  if (["aria-label", "aria-labelledby", "aria-describedby"].includes(name)) return text.length <= 500;
  if (name === "font-family") return /^[A-Za-z0-9 _,'"-]{1,100}$/.test(text);
  if (["role", "font-style", "font-weight", "text-anchor", "dominant-baseline", "alignment-baseline", "direction", "unicode-bidi", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "fill-rule", "clip-rule", "vector-effect", "display", "visibility", "focusable", "shape-rendering", "text-rendering", "paint-order", "pointer-events", "preserveAspectRatio", "gradientUnits", "spreadMethod", "markerUnits", "clipPathUnits", "maskUnits", "maskContentUnits", "patternUnits", "patternContentUnits", "filterUnits", "primitiveUnits", "edgeMode", "mode", "type", "operator", "calcMode", "restart", "additive", "accumulate", "lengthAdjust", "orient", "version"].includes(name)) {
    return text.length <= 100 && /^[A-Za-z0-9 _.,+\-]*$/.test(text);
  }
  return false;
}

function safeAnimationValues(source, name, value) {
  if (!ANIMATION_TAGS.has(source.localName) || !["values", "from", "to", "by"].includes(name)) return true;
  const attribute = source.getAttribute("attributeName") || "";
  const values = name === "values" ? value.split(";") : [value];
  if (["fill", "stroke"].includes(attribute)) {
    return values.every((item) => COLOR.test(item.trim()) || /^url\(\s*#[A-Za-z_][\w.:-]*\s*\)$/.test(item.trim()));
  }
  if (source.localName === "animateTransform") {
    const expectedLengths = {
      translate: new Set([1, 2]), scale: new Set([1, 2]), rotate: new Set([1, 3]),
      skewX: new Set([1]), skewY: new Set([1])
    }[source.getAttribute("type")];
    return Boolean(expectedLengths && values.every((item) => {
      const trimmed = item.trim();
      const count = trimmed.match(new RegExp(NUMBER, "g"))?.length || 0;
      return NUMBER_LIST.test(trimmed) && expectedLengths.has(count) && boundedNumericTokens(trimmed);
    }));
  }
  if (attribute === "transform") return values.every(safeTransform);
  if (attribute === "d") return values.every((item) => PATH.test(item.trim()) && boundedNumericTokens(item.trim()));
  return values.every((item) => NUMBER_LIST.test(item.trim()) && boundedNumericTokens(item.trim()));
}

function parseKeyTimes(value, expectedCount = null) {
  const parts = String(value).split(";");
  if (parts.length < 2 || (expectedCount !== null && parts.length !== expectedCount)) return null;
  const times = parts.map((item) => Number(item.trim()));
  if (!times.every((item) => Number.isFinite(item) && item >= 0 && item <= 1)
    || times[0] !== 0 || times.at(-1) !== 1
    || times.some((item, index) => index > 0 && times[index - 1] > item)) return null;
  return times;
}

function animationIsComplete(source) {
  const tag = source.localName;
  const attribute = source.getAttribute("attributeName") || "";
  const values = source.getAttribute("values");
  if (tag === "set") {
    const targetValue = source.getAttribute("to") || "";
    return SAFE_ANIMATED.has(attribute) && safeAnimationValues(source, "to", targetValue);
  }
  if (tag === "animateMotion") {
    const duration = source.getAttribute("dur") || "";
    const path = source.getAttribute("path") || "";
    const linkedPath = source.querySelector("mpath[href]");
    return safeAttribute("dur", duration, tag)
      && ((path && safeAttribute("path", path, tag)) || (linkedPath && safeAttribute("href", linkedPath.getAttribute("href"), "mpath")));
  }
  if (!SAFE_ANIMATED.has(attribute) || !safeAttribute("dur", source.getAttribute("dur") || "", tag)) return false;
  if (tag === "animateTransform") {
    if (attribute !== "transform") return false;
    if (values) return values.split(";").length >= 2 && safeAnimationValues(source, "values", values);
    const from = source.getAttribute("from") || "";
    const to = source.getAttribute("to") || "";
    return Boolean(from && to && safeAnimationValues(source, "from", from) && safeAnimationValues(source, "to", to));
  }
  if (values) return values.split(";").length >= 2 && safeAnimationValues(source, "values", values);
  const from = source.getAttribute("from") || "";
  const to = source.getAttribute("to") || "";
  const by = source.getAttribute("by") || "";
  return Boolean(
    (from && to && safeAnimationValues(source, "from", from) && safeAnimationValues(source, "to", to))
    || (from && by && safeAnimationValues(source, "from", from) && safeAnimationValues(source, "by", by))
    || (to && by && safeAnimationValues(source, "to", to) && safeAnimationValues(source, "by", by))
    || (by && !from && !to && safeAnimationValues(source, "by", by))
  );
}

function canonicalizeAnimation(source, output) {
  const values = source.getAttribute("values");
  if (values) {
    const count = values.split(";").length;
    const keyTimes = parseKeyTimes(output.getAttribute("keyTimes") || "", count);
    if (!keyTimes) {
      output.setAttribute("keyTimes", Array.from({ length: count }, (_, index) => (index / (count - 1)).toString()).join(";"));
    }
  }
  if (!output.hasAttribute("fill")
    && source.getAttribute("repeatCount") !== "indefinite"
    && source.getAttribute("repeatDur") !== "indefinite") {
    output.setAttribute("fill", "freeze");
  }
}

function prefixReferences(value, idMap) {
  return value.replace(/url\(\s*#([A-Za-z_][\w.:-]*)\s*\)/g, (whole, id) => {
    const mapped = idMap.get(id);
    return mapped ? `url(#${mapped})` : "none";
  });
}

/** Sanitize generated SVG before inserting it into the page. Returns null for invalid roots. */
export function sanitizeSvgMarkup(markup, { idPrefix = "guide-svg", altText = "" } = {}) {
  const source = parseSvg(markup);
  if (!source) return null;
  const ids = new Map();
  const uniquePrefix = String(idPrefix).replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 50) || "guide-svg";
  const elements = [...source.root.querySelectorAll("*")];
  if (elements.length + 1 > MAX_ELEMENTS || !source.root.hasAttribute("viewBox")) return null;
  const viewBoxValues = source.root.getAttribute("viewBox").trim().split(/[\s,]+/).map(Number);
  if (viewBoxValues.length !== 4
    || !viewBoxValues.every((value) => Number.isFinite(value) && Math.abs(value) <= 1_000_000)
    || viewBoxValues[2] <= 0 || viewBoxValues[3] <= 0
    || viewBoxValues[2] > 100_000 || viewBoxValues[3] > 100_000) return null;
  for (const element of [source.root, ...elements]) {
    if ((element.namespaceURI && element.namespaceURI !== SVG_NS) || !ALLOWED_TAGS.has(element.localName)) continue;
    const id = element.getAttribute("id");
    if (id && /^[A-Za-z_][A-Za-z\d_.:-]{0,95}$/.test(id) && !ids.has(id)) {
      ids.set(id, `${uniquePrefix}-${id}`);
    }
  }

  let nodeCount = 0;
  let textCount = 0;
  const textElements = new Set(["title", "desc", "text", "tspan"]);
  const outputDoc = source.parsed.implementation.createDocument(SVG_NS, "svg", null);
  const outRoot = outputDoc.documentElement;

  function cloneNode(input, output, depth) {
    if (depth > MAX_DEPTH) return;
    for (const child of [...input.childNodes]) {
      if (child.nodeType === 3) {
        if (textElements.has(input.localName)) {
          textCount += child.nodeValue.length;
          if (textCount > MAX_TEXT) return;
          output.appendChild(outputDoc.createTextNode(child.nodeValue));
        }
        continue;
      }
      if (child.nodeType !== 1 || (child.namespaceURI !== SVG_NS && child.namespaceURI)) continue;
      const tag = child.localName;
      if (!ALLOWED_TAGS.has(tag)) continue;
      if (ANIMATION_TAGS.has(tag) && !animationIsComplete(child)) continue;
      nodeCount += 1;
      if (nodeCount > MAX_ELEMENTS) return;
      const safeChild = outputDoc.createElementNS(SVG_NS, tag);
      for (const attribute of [...child.attributes]) {
        const name = attribute.localName;
        if (attribute.namespaceURI && !(name === "href" && attribute.namespaceURI === XLINK_NS)) continue;
        if (name.toLowerCase().startsWith("on") || name === "style") continue;
        if (!(GLOBAL_ATTRS.has(name) || (ELEMENT_ATTRS[tag] || []).includes(name))) continue;
        if (ANIMATION_TAGS.has(tag) && name === "attributeName" && !SAFE_ANIMATED.has(attribute.value.trim())) continue;
        const value = attribute.value.trim();
        if (!safeAttribute(name, value, tag) || !safeAnimationValues(child, name, value)) continue;
        if (name === "id") {
          const mapped = ids.get(value);
          if (mapped) safeChild.setAttribute(name, mapped);
          continue;
        }
        if (["href", "aria-labelledby", "aria-describedby"].includes(name)) {
          if (name === "href") {
            const target = ids.get(value.slice(1));
            if (target) safeChild.setAttribute(name, `#${target}`);
          } else {
            const refs = value.split(/\s+/).map((item) => ids.get(item));
            if (refs.every(Boolean)) safeChild.setAttribute(name, refs.join(" "));
          }
          continue;
        }
        safeChild.setAttribute(name, prefixReferences(value, ids));
      }
      if (ANIMATION_TAGS.has(tag)) canonicalizeAnimation(child, safeChild);
      output.appendChild(safeChild);
      cloneNode(child, safeChild, depth + 1);
    }
  }

  for (const attribute of [...source.root.attributes]) {
    const name = attribute.localName;
    if (name.toLowerCase().startsWith("on") || name === "style") continue;
    if (!(GLOBAL_ATTRS.has(name) || (ELEMENT_ATTRS.svg || []).includes(name))) continue;
    const value = attribute.value.trim();
    if (!safeAttribute(name, value, "svg")) continue;
    if (name === "id") {
      const mapped = ids.get(value);
      if (mapped) outRoot.setAttribute(name, mapped);
      continue;
    }
    if (["aria-labelledby", "aria-describedby"].includes(name)) {
      const refs = value.split(/\s+/).map((item) => ids.get(item));
      if (refs.every(Boolean)) outRoot.setAttribute(name, refs.join(" "));
      continue;
    }
    outRoot.setAttribute(name, prefixReferences(value, ids));
  }
  if (altText) {
    outRoot.setAttribute("role", "img");
    outRoot.setAttribute("aria-label", String(altText).slice(0, 500));
  }
  cloneNode(source.root, outRoot, 1);

  const idNodes = new Map();
  for (const element of [outRoot, ...outRoot.querySelectorAll("[id]")]) {
    if (element.id) idNodes.set(element.id, element);
  }
  const useGraph = new Map();
  for (const [id, element] of idNodes) {
    const uses = [
      ...(element.localName === "use" && element.hasAttribute("href") ? [element] : []),
      ...element.querySelectorAll("use[href]")
    ];
    useGraph.set(id, uses
      .map((use) => use.getAttribute("href").slice(1))
      .filter((target) => idNodes.has(target)));
  }
  const visiting = new Set();
  const visited = new Set();
  function hasCycle(id) {
    if (visiting.has(id)) return true;
    if (visited.has(id)) return false;
    visiting.add(id);
    for (const target of useGraph.get(id) || []) {
      if (hasCycle(target)) return true;
    }
    visiting.delete(id);
    visited.add(id);
    return false;
  }
  if ([...idNodes.keys()].some(hasCycle)) {
    outRoot.querySelectorAll("use").forEach((element) => element.remove());
  }
  return new XMLSerializer().serializeToString(outRoot);
}

export function svgHasAnimation(markup) {
  return typeof markup === "string" && /<(?:animate|animateTransform|animateMotion|set)(?:\s|\/|>)/i.test(markup);
}
