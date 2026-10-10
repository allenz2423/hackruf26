const ALLOWED_SHAPES = new Set(["pointer", "rectangle", "underline", "note"]);
const MAX_LABEL_LENGTH = 80;
const MAX_MARKS = 8;
const MARK_TTL_MS = 12000;

function isFiniteUnit(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function validateAnnotation(item, frames) {
  const rect = item?.rect || {};

  if (!ALLOWED_SHAPES.has(item?.shape)) return "Unknown annotation shape.";
  if (!frames.has(item?.frame_id)) return "The referenced frame is no longer available.";
  if (typeof item?.label !== "string" || item.label.length > MAX_LABEL_LENGTH) return "Annotation label is invalid.";
  if (![rect.x, rect.y, rect.width, rect.height].every(isFiniteUnit)) return "Annotation coordinates are out of bounds.";
  if (rect.width <= 0 || rect.height <= 0 || rect.x + rect.width > 1 || rect.y + rect.height > 1) {
    return "Annotation rectangle is outside the shared preview.";
  }

  return null;
}

export class AnnotationController {
  constructor({ layer, list, getFrames }) {
    this.layer = layer;
    this.list = list;
    this.getFrames = getFrames;
    this.marks = [];
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
      } else {
        accepted.push({ ...item, id: crypto.randomUUID(), createdAt: Date.now() });
      }
    }

    this.marks.push(...accepted);
    this.render();
    window.setTimeout(() => this.expireOldMarks(), MARK_TTL_MS);
    return { accepted, errors };
  }

  remove(markId) {
    this.marks = this.marks.filter((mark) => mark.id !== markId);
    this.render();
  }

  clear() {
    this.marks = [];
    this.render();
  }

  expireOldMarks() {
    const cutoff = Date.now() - MARK_TTL_MS;
    this.marks = this.marks.filter((mark) => mark.createdAt > cutoff);
    this.render();
  }

  render() {
    this.layer.replaceChildren();
    this.list.replaceChildren();

    for (const mark of this.marks) {
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");

      rect.setAttribute("x", String(mark.rect.x * 100));
      rect.setAttribute("y", String(mark.rect.y * 100));
      rect.setAttribute("width", String(mark.rect.width * 100));
      rect.setAttribute("height", String(mark.rect.height * 100));
      rect.setAttribute("class", `mark mark-${mark.shape}`);

      label.setAttribute("x", String(mark.rect.x * 100));
      label.setAttribute("y", String(Math.max(5, mark.rect.y * 100 - 1.5)));
      label.setAttribute("class", "mark-label");
      label.textContent = mark.label;

      group.append(rect, label);
      this.layer.append(group);

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
