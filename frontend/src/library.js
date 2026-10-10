import { icon } from "./icons.js";
import { escapeHtml, formatBytes, toast } from "./ui.js";

const ACCEPTED_EXTENSIONS = new Set(["pdf", "docx", "pptx", "txt", "md", "png", "jpg", "jpeg", "mp4", "webm", "mov", "mp3", "m4a", "wav"]);
const MAX_FILE_SIZE = 250 * 1024 * 1024;

const KIND_ICONS = {
  Notes: "notes",
  Document: "file",
  Syllabus: "syllabus",
  Slides: "slides",
  Lecture: "video"
};

const FILTERS = [
  { label: "All", kind: null },
  { label: "Notes", kind: "Notes" },
  { label: "Docs", kind: "Document" },
  { label: "Syllabus", kind: "Syllabus" },
  { label: "Slides", kind: "Slides" },
  { label: "Lectures", kind: "Lecture" }
];

const STATUS_TONES = {
  ready: "good",
  processing: "busy",
  transcribing: "busy",
  uploading: "busy",
  failed: "bad"
};

// Remember the selected filter across re-renders of the tab.
let activeFilter = null;

function guessKind(file) {
  const lower = file.name.toLowerCase();
  if (lower.includes("syllabus")) return "Syllabus";
  if (lower.endsWith(".pptx")) return "Slides";
  if (/\.(mp4|webm|mov|mp3|m4a|wav)$/.test(lower)) return "Lecture";
  if (/\.(png|jpe?g)$/.test(lower)) return "Notes";
  return "Document";
}

export function validateUpload(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  if (!ACCEPTED_EXTENSIONS.has(extension)) return `${file.name} is not a supported file type.`;
  if (file.size > MAX_FILE_SIZE) return `${file.name} is larger than ${formatBytes(MAX_FILE_SIZE)}.`;
  return null;
}

export function createLocalUpload(file) {
  return {
    id: crypto.randomUUID(),
    name: file.name,
    kind: guessKind(file),
    size: file.size,
    status: "ready",
    progress: 100,
    createdAt: new Date().toISOString()
  };
}

export function renderLibraryTab({ root, klass, onUploadsAdded, onUploadRemoved }) {
  root.innerHTML = `
    <section class="section-stack">
      <label class="upload-zone" for="upload-input">
        <span class="upload-zone-icon">${icon("upload", { size: 26 })}</span>
        <strong>Drop files here or <span class="browse">browse</span></strong>
        <span class="muted">Notes, documents, syllabi, slides, and lecture video or audio</span>
        <small>PDF, DOCX, PPTX, TXT, MD, PNG, JPG, MP4, MOV, WEBM, MP3, M4A, WAV · up to ${formatBytes(MAX_FILE_SIZE)}</small>
        <input id="upload-input" class="visually-hidden" type="file" multiple
          accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.mp4,.webm,.mov,.mp3,.m4a,.wav" />
      </label>
      <div class="toolbar-row">
        <div class="chip-group" role="group" aria-label="Filter uploads by kind" id="upload-filters"></div>
      </div>
      <div class="panel">
        <ul class="data-list" id="upload-list" aria-live="polite"></ul>
      </div>
    </section>
  `;

  const zone = root.querySelector(".upload-zone");
  const input = root.querySelector("#upload-input");
  const list = root.querySelector("#upload-list");
  const filters = root.querySelector("#upload-filters");

  function renderFilters() {
    filters.innerHTML = FILTERS.map(({ label, kind }) => {
      const count = kind ? klass.uploads.filter((upload) => upload.kind === kind).length : klass.uploads.length;
      return `<button class="chip" type="button" data-kind="${kind ?? ""}" aria-pressed="${activeFilter === kind}">
        ${label} <span class="count">${count}</span>
      </button>`;
    }).join("");
  }

  function renderUploads() {
    const visible = klass.uploads.filter((upload) => !activeFilter || upload.kind === activeFilter);

    if (!visible.length) {
      list.innerHTML = `
        <li class="empty-state">
          <span class="empty-icon">${icon("book", { size: 26 })}</span>
          <strong>${klass.uploads.length ? "Nothing in this filter yet" : "No materials yet"}</strong>
          <span>${klass.uploads.length ? "Try another filter or upload more files." : "Upload notes, a syllabus, or a lecture to start studying this class."}</span>
        </li>`;
      return;
    }

    list.innerHTML = visible.map((upload) => `
      <li>
        <span class="kind-icon" data-kind="${escapeHtml(upload.kind)}">${icon(KIND_ICONS[upload.kind] || "file")}</span>
        <span class="row-main">
          <span class="file-name" title="${escapeHtml(upload.name)}">${escapeHtml(upload.name)}</span>
          <span class="row-meta">${escapeHtml(upload.kind)} · ${formatBytes(upload.size)}</span>
        </span>
        <span class="pill" data-tone="${STATUS_TONES[upload.status] || ""}"><strong>${escapeHtml(upload.status)}</strong></span>
        <span class="row-actions">
          <button class="btn btn-ghost btn-icon" type="button" data-remove="${upload.id}" aria-label="Remove ${escapeHtml(upload.name)}">
            ${icon("trash", { size: 18 })}
          </button>
        </span>
      </li>
    `).join("");
  }

  function addFiles(files) {
    const nextUploads = [];

    for (const file of files) {
      const error = validateUpload(file);
      if (error) {
        toast(error, { tone: "error" });
      } else {
        nextUploads.push(createLocalUpload(file));
      }
    }

    if (nextUploads.length) {
      toast(`Added ${nextUploads.length} ${nextUploads.length === 1 ? "file" : "files"} to ${klass.name}.`);
      onUploadsAdded(nextUploads);
    }
  }

  filters.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-kind]");
    if (!chip) return;
    activeFilter = chip.dataset.kind || null;
    renderFilters();
    renderUploads();
  });

  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (button) onUploadRemoved?.(button.dataset.remove);
  });

  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    addFiles(event.dataTransfer.files);
  });
  input.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });

  renderFilters();
  renderUploads();
}
