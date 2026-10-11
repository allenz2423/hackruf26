// Class tutor materials: which uploads the tutor reads as context, and which
// upload to open on the stage. The context choice is a per-class convenience
// kept in this browser; null means "every upload", so new uploads join automatically.
import { icon } from "./icons.js";
import { materialType } from "./material-stage.js";
import { escapeHtml } from "./ui.js";

const storageKey = (klass) => `jot-tutor-sources:${klass.id}`;

/** Saved upload ids for this class's tutor context, or null for every upload. */
export function loadTutorSources(klass) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(klass)));
    if (!Array.isArray(saved)) return null;
    const existing = new Set((klass.uploads || []).map((upload) => upload.id));
    return saved.filter((id) => existing.has(id));
  } catch {
    return null;
  }
}

export function saveTutorSources(klass, ids) {
  try {
    if (ids === null) localStorage.removeItem(storageKey(klass));
    else localStorage.setItem(storageKey(klass), JSON.stringify(ids));
  } catch {
    // Blocked storage: the choice still applies until the page is left.
  }
}

export function sourcesSummary(klass, ids) {
  const total = klass.uploads?.length || 0;
  if (!total) return "No materials yet";
  if (ids === null || ids.length === total) return "All materials";
  if (!ids.length) return "No materials";
  return `${ids.length} of ${total} materials`;
}

const MATERIAL_ICONS = { pdf: "file", image: "slides", slides: "slides", text: "notes" };

function uploadMeta(upload) {
  if (upload.status === "failed") return "Processing failed";
  if (upload.status === "processing") return `${upload.kind || "Material"} · processing`;
  return upload.kind || "Material";
}

function makeDialog(title, body, actions) {
  const dialog = document.createElement("dialog");
  dialog.className = "dialog tutor-materials-dialog";
  dialog.setAttribute("aria-labelledby", "tutor-materials-title");
  dialog.innerHTML = `
    <form method="dialog" class="dialog-body">
      <h2 id="tutor-materials-title">${escapeHtml(title)}</h2>
      ${body}
      <div class="dialog-actions">${actions}</div>
    </form>
  `;
  return dialog;
}

function show(dialog, onClose) {
  dialog.querySelector("[data-cancel]")?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    dialog.remove();
    onClose();
  });
  document.body.append(dialog);
  dialog.showModal();
}

const emptyLibrary = (klass) => `
  <p class="muted">Add notes, a syllabus, slides, or other files in the
  <a href="#/classes/${encodeURIComponent(klass.id)}/library">Library</a> first.</p>`;

/**
 * Ask which uploads the tutor should read. Resolves to null (every upload), an
 * array of upload ids, or undefined when the student cancels.
 */
export function chooseTutorSources(klass, current) {
  return new Promise((resolve) => {
    const uploads = klass.uploads || [];
    const chosen = new Set(current === null ? uploads.map((upload) => upload.id) : current);
    const body = uploads.length ? `
      <p class="muted">The tutor reads these as background for ${escapeHtml(klass.name)}. A material you open on the stage is always included.</p>
      <div class="source-tools">
        <button type="button" class="btn btn-ghost" data-select="all">Select all</button>
        <button type="button" class="btn btn-ghost" data-select="none">Clear</button>
      </div>
      <ul class="source-list">
        ${uploads.map((upload) => `
          <li>
            <label class="source-option">
              <input type="checkbox" name="source" value="${escapeHtml(upload.id)}"${chosen.has(upload.id) ? " checked" : ""} />
              <span class="source-text">
                <span class="source-name">${escapeHtml(upload.name || "Untitled")}</span>
                <span class="row-meta">${escapeHtml(uploadMeta(upload))}</span>
              </span>
            </label>
          </li>`).join("")}
      </ul>` : emptyLibrary(klass);
    const actions = uploads.length
      ? `<button type="button" class="btn btn-secondary" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">Save</button>`
      : `<button type="button" class="btn btn-primary" data-cancel>Close</button>`;
    const dialog = makeDialog("Tutor materials", body, actions);

    let result;
    const boxes = () => [...dialog.querySelectorAll('input[name="source"]')];
    dialog.querySelectorAll("[data-select]").forEach((button) => button.addEventListener("click", () => {
      boxes().forEach((box) => { box.checked = button.dataset.select === "all"; });
    }));
    dialog.querySelector("form").addEventListener("submit", () => {
      const ids = boxes().filter((box) => box.checked).map((box) => box.value);
      result = ids.length === uploads.length ? null : ids;
    });
    show(dialog, () => resolve(result));
  });
}

/** Ask which upload to open on the stage. Resolves to the upload, or null. */
export function chooseMaterial(klass) {
  return new Promise((resolve) => {
    const uploads = (klass.uploads || []).filter((upload) => materialType(upload) && upload.status !== "failed");
    const hasMedia = (klass.uploads || []).some((upload) => !materialType(upload));
    const body = uploads.length ? `
      <p class="muted">Show a page to the tutor instead of your screen. It can point things out on it, and you can turn pages.</p>
      <ul class="material-choices">
        ${uploads.map((upload) => `
          <li>
            <button type="button" class="material-choice" data-upload="${escapeHtml(upload.id)}">
              ${icon(MATERIAL_ICONS[materialType(upload)], { size: 20 })}
              <span class="source-text">
                <span class="source-name">${escapeHtml(upload.name || "Untitled")}</span>
                <span class="row-meta">${escapeHtml(uploadMeta(upload))}</span>
              </span>
            </button>
          </li>`).join("")}
      </ul>
      ${hasMedia ? `<p class="row-meta">Lecture recordings can't be shown here; the tutor can still read their transcripts.</p>` : ""}`
      : emptyLibrary(klass);
    const dialog = makeDialog("Open a material", body, `<button type="button" class="btn btn-secondary" data-cancel>Cancel</button>`);

    let result = null;
    dialog.querySelectorAll("[data-upload]").forEach((button) => button.addEventListener("click", () => {
      result = uploads.find((upload) => upload.id === button.dataset.upload) || null;
      dialog.close();
    }));
    show(dialog, () => resolve(result));
  });
}
