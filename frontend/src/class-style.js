// Class card look: the color and icon a student picks for each class.
// The backend saves both with the class as ids from these lists; keep them in step with
// ClassColor and ClassIcon in backend/app/api/schemas.py.
import { icon } from "./icons.js";
import { escapeHtml } from "./ui.js";

// Deep inks that keep the white badge letter readable in both themes.
export const CLASS_COLORS = [
  { id: "slate", hex: "#3f6577", name: "Slate" },
  { id: "teal", hex: "#26707a", name: "Teal" },
  { id: "moss", hex: "#4f7340", name: "Moss" },
  { id: "ochre", hex: "#8a6414", name: "Ochre" },
  { id: "rust", hex: "#b04a2f", name: "Rust" },
  { id: "berry", hex: "#a33a55", name: "Berry" },
  { id: "plum", hex: "#7a4a72", name: "Plum" },
  { id: "indigo", hex: "#4b4f8c", name: "Indigo" },
  { id: "graphite", hex: "#5a5550", name: "Graphite" }
];

/** The API id for a palette hex, or null for a color outside the palette. */
export function colorId(hex) {
  return CLASS_COLORS.find((item) => item.hex === hex)?.id || null;
}

/** The hex for an API color id, or null when unset or unknown. */
export function colorHex(id) {
  return CLASS_COLORS.find((item) => item.id === id)?.hex || null;
}

// Each id is also an icon name in icons.js; "" (or null from the API) means the class initial.
export const CLASS_GLYPHS = [
  { id: "sigma", name: "Math" },
  { id: "calculator", name: "Calculator" },
  { id: "flask", name: "Chemistry" },
  { id: "atom", name: "Physics" },
  { id: "dna", name: "Biology" },
  { id: "leaf", name: "Environment" },
  { id: "globe", name: "Geography" },
  { id: "landmark", name: "History" },
  { id: "scale", name: "Law and ethics" },
  { id: "languages", name: "Languages" },
  { id: "book", name: "Literature" },
  { id: "code", name: "Computer science" },
  { id: "chart", name: "Economics" },
  { id: "music", name: "Music" },
  { id: "palette", name: "Art" },
  { id: "lightbulb", name: "Ideas" }
];

const GLYPH_IDS = new Set(CLASS_GLYPHS.map((glyph) => glyph.id));
const DEFAULT_COLOR = CLASS_COLORS[0].hex;

export function classInitial(name) {
  return String(name || "").trim().charAt(0).toUpperCase() || "C";
}

export function classColor(klass) {
  return klass?.color || DEFAULT_COLOR;
}

/** Inner markup for a `.class-icon` badge: the chosen icon, or the class initial. */
export function classBadge(klass, { size = 22 } = {}) {
  if (GLYPH_IDS.has(klass?.glyph)) return icon(klass.glyph, { size });
  return escapeHtml(klass?.icon || classInitial(klass?.name));
}

/** Edit a class's name, color, and icon. Resolves to `{ name, color, glyph }` or null on cancel. */
export function openClassEditor(klass) {
  return new Promise((resolve) => {
    const current = {
      name: klass.name,
      color: classColor(klass),
      glyph: GLYPH_IDS.has(klass.glyph) ? klass.glyph : ""
    };
    const colors = CLASS_COLORS.some((item) => item.hex === current.color)
      ? CLASS_COLORS
      : [{ hex: current.color, name: "Current color" }, ...CLASS_COLORS];

    const dialog = document.createElement("dialog");
    dialog.className = "dialog class-editor";
    dialog.setAttribute("aria-labelledby", "class-editor-title");
    dialog.innerHTML = `
      <form method="dialog" class="dialog-body">
        <div class="class-editor-head">
          <span class="class-icon" aria-hidden="true" data-preview></span>
          <h2 id="class-editor-title">Edit class</h2>
        </div>
        <label class="field">
          <span>Class name</span>
          <input name="name" required maxlength="60" autocomplete="off" value="${escapeHtml(current.name)}" />
        </label>
        <fieldset class="jot-group">
          <legend>Color</legend>
          <div class="jot-swatches">
            ${colors.map((item) => `
              <label class="jot-swatch">
                <input type="radio" name="color" value="${escapeHtml(item.hex)}"${item.hex === current.color ? " checked" : ""} />
                <span class="jot-swatch-dot" style="--swatch: ${escapeHtml(item.hex)}"></span>
                <span class="visually-hidden">${escapeHtml(item.name)}</span>
              </label>`).join("")}
          </div>
        </fieldset>
        <fieldset class="jot-group">
          <legend>Icon</legend>
          <div class="class-glyphs">
            <label class="class-glyph" title="Initial">
              <input type="radio" name="glyph" value=""${current.glyph ? "" : " checked"} />
              <span class="class-glyph-art" data-initial>${escapeHtml(classInitial(current.name))}</span>
              <span class="visually-hidden">Class initial</span>
            </label>
            ${CLASS_GLYPHS.map((glyph) => `
              <label class="class-glyph" title="${escapeHtml(glyph.name)}">
                <input type="radio" name="glyph" value="${glyph.id}"${glyph.id === current.glyph ? " checked" : ""} />
                <span class="class-glyph-art">${icon(glyph.id, { size: 20 })}</span>
                <span class="visually-hidden">${escapeHtml(glyph.name)}</span>
              </label>`).join("")}
          </div>
        </fieldset>
        <div class="dialog-actions">
          <button type="button" class="btn btn-secondary" data-cancel>Cancel</button>
          <button type="submit" class="btn btn-primary">Save</button>
        </div>
      </form>
    `;

    const form = dialog.querySelector("form");
    const nameField = form.elements.name;
    const preview = dialog.querySelector("[data-preview]");
    const initial = dialog.querySelector("[data-initial]");
    const read = () => ({
      name: nameField.value.trim(),
      color: form.elements.color.value || current.color,
      glyph: form.elements.glyph.value
    });
    const paint = () => {
      const next = read();
      initial.textContent = classInitial(next.name);
      dialog.style.setProperty("--class-color", next.color);
      preview.innerHTML = classBadge({ ...next, icon: classInitial(next.name) }, { size: 24 });
    };

    let result = null;
    form.addEventListener("input", paint);
    form.addEventListener("submit", (event) => {
      const next = read();
      if (!next.name) {
        event.preventDefault();
        nameField.focus();
        return;
      }
      result = next;
    });
    dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });

    paint();
    document.body.append(dialog);
    dialog.showModal();
    nameField.select();
  });
}
