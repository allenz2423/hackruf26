const ALLOWED_SHAPES = new Set(["pointer", "rectangle", "underline", "note"]);
const MAX_LABEL_LENGTH = 80;
const MAX_MARKS = 8;
const MARK_TTL_MS = 12000;
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const HTML_NAMESPACE = "http://www.w3.org/1999/xhtml";
const LABEL_FONT_SIZE = 14;
const MIN_LABEL_FONT_SIZE = 8;

function isFiniteUnit(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function validateAnnotation(item, frames) {
  const shape = item?.shape || item?.kind;
  const rect = item?.rect || item?.region || {};

  if (!ALLOWED_SHAPES.has(shape)) return "Unknown annotation shape.";
  if (!frames.has(item?.frame_id)) return "The referenced frame is no longer available.";
  const frame = frames.get(item.frame_id);
  if (![frame.width, frame.height].every((size) => Number.isFinite(size) && size > 0)) {
    return "The referenced frame dimensions are invalid.";
  }
  if (typeof item?.label !== "string" || item.label.length > MAX_LABEL_LENGTH) return "Annotation label is invalid.";
  if (![rect.x, rect.y, rect.width, rect.height].every(isFiniteUnit)) return "Annotation coordinates are out of bounds.";
  if (rect.width <= 0 || rect.height <= 0 || rect.x + rect.width > 1 || rect.y + rect.height > 1) {
    return "Annotation rectangle is outside the shared preview.";
  }

  return null;
}

export class AnnotationController {
  constructor({ layer, list, video, getFrames }) {
    this.layer = layer;
    this.list = list;
    this.video = video;
    this.getFrames = getFrames;
    this.marks = [];
    this.expiryTimer = null;
    this.onPreviewResize = () => this.renderOverlay();
    this.layer.setAttribute("preserveAspectRatio", "xMidYMid meet");
    this.video.addEventListener("loadedmetadata", this.onPreviewResize);
    this.video.addEventListener("resize", this.onPreviewResize);
    this.resizeObserver = new ResizeObserver(this.onPreviewResize);
    this.resizeObserver.observe(this.layer);
  }

  addMany(items) {
    const frames = this.getFrames();
    const accepted = [];
    const errors = [];

    if (!Array.isArray(items) || items.length > MAX_MARKS) {
      return { accepted, errors: ["Too many annotations were requested."] };
    }

    for (const item of items) {
      const error = validateAnnotation(item, frames);
      if (error) {
        errors.push(error);
      } else if (
        frames.get(item.frame_id).width !== this.video.videoWidth
        || frames.get(item.frame_id).height !== this.video.videoHeight
      ) {
        errors.push("The shared screen changed size. Request a mark on a new frame.");
      } else {
        accepted.push({
          ...item,
          shape: item.shape || item.kind,
          rect: item.rect || item.region,
          frame: { width: frames.get(item.frame_id).width, height: frames.get(item.frame_id).height },
          id: crypto.randomUUID(),
          createdAt: Date.now()
        });
      }
    }

    this.marks.push(...accepted);
    this.render();
    this.scheduleExpiry();
    return { accepted, errors };
  }

  remove(markId) {
    this.marks = this.marks.filter((mark) => mark.id !== markId);
    this.render();
    this.scheduleExpiry();
  }

  clear() {
    this.marks = [];
    window.clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
    this.render();
  }

  destroy() {
    this.clear();
    this.resizeObserver.disconnect();
    this.video.removeEventListener("loadedmetadata", this.onPreviewResize);
    this.video.removeEventListener("resize", this.onPreviewResize);
  }

  scheduleExpiry() {
    window.clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
    if (!this.marks.length) return;
    const expiresAt = Math.min(...this.marks.map((mark) => mark.createdAt + MARK_TTL_MS));
    this.expiryTimer = window.setTimeout(() => this.expireOldMarks(), Math.max(0, expiresAt - Date.now()));
  }

  expireOldMarks() {
    const cutoff = Date.now() - MARK_TTL_MS;
    this.marks = this.marks.filter((mark) => mark.createdAt > cutoff);
    this.render();
    this.scheduleExpiry();
  }

  render() {
    this.renderOverlay();
    this.renderList();
  }

  renderOverlay() {
    this.layer.replaceChildren();
    const width = this.video.videoWidth;
    const height = this.video.videoHeight;
    if (!width || !height) return;

    // Use the same centered, uniform scaling as the video's object-fit: contain.
    this.layer.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const bounds = this.layer.getBoundingClientRect();
    const scale = Math.min(bounds.width / width, bounds.height / height);
    if (!(scale > 0)) return;

    const previousCount = this.marks.length;
    this.marks = this.marks.filter((mark) => mark.frame.width === width && mark.frame.height === height);
    if (this.marks.length !== previousCount) {
      this.renderList();
      this.scheduleExpiry();
    }

    for (const mark of this.marks) {
      const group = document.createElementNS(SVG_NAMESPACE, "g");
      const rect = document.createElementNS(SVG_NAMESPACE, "rect");
      const x = mark.rect.x * width;
      const y = mark.rect.y * height;
      const boxWidth = mark.rect.width * width;
      const boxHeight = mark.rect.height * height;

      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(y));
      rect.setAttribute("width", String(boxWidth));
      rect.setAttribute("height", String(boxHeight));
      rect.setAttribute("class", `mark mark-${mark.shape}`);

      const labelBox = document.createElementNS(SVG_NAMESPACE, "foreignObject");
      const inset = Math.min(3 / scale, boxWidth / 4, boxHeight / 4);
      labelBox.setAttribute("x", String(x + inset));
      labelBox.setAttribute("y", String(y + inset));
      labelBox.setAttribute("width", String(boxWidth - inset * 2));
      labelBox.setAttribute("height", String(boxHeight - inset * 2));
      labelBox.setAttribute("class", "mark-label-box");

      const label = document.createElementNS(HTML_NAMESPACE, "div");
      label.className = "mark-label";
      const text = document.createElementNS(HTML_NAMESPACE, "span");
      text.className = "mark-label-text";
      text.textContent = mark.label;
      label.append(text);
      labelBox.append(label);

      group.append(rect, labelBox);
      this.layer.append(group);
      this.fitLabel(label, text, scale);
    }
  }

  fitLabel(label, text, scale) {
    let fontSize = LABEL_FONT_SIZE;
    label.style.fontSize = `${fontSize / scale}px`;
    while (
      fontSize > MIN_LABEL_FONT_SIZE
      && (label.scrollHeight > label.clientHeight || label.scrollWidth > label.clientWidth)
    ) {
      fontSize -= 1;
      label.style.fontSize = `${fontSize / scale}px`;
    }
    // Tiny regions keep an abbreviated label inside; the list retains the full text.
    const lines = Math.floor(label.clientHeight / (fontSize / scale * 1.2));
    if (lines < 1) {
      text.hidden = true;
    } else {
      text.style.webkitLineClamp = String(lines);
    }
  }

  renderList() {
    this.list.replaceChildren();
    for (const mark of this.marks) {
      const item = document.createElement("li");
      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.textContent = "Dismiss";
      dismiss.addEventListener("click", () => this.remove(mark.id));
      item.textContent = mark.label;
      item.append(dismiss);
      this.list.append(item);
    }
  }
}
