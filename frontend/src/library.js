const ACCEPTED_EXTENSIONS = new Set(["pdf", "docx", "pptx", "txt", "md", "png", "jpg", "jpeg", "mp4", "webm", "mov", "mp3", "m4a", "wav"]);
const MAX_FILE_SIZE = 250 * 1024 * 1024;

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
  if (!ACCEPTED_EXTENSIONS.has(extension)) return `${file.name} is not a supported type.`;
  if (file.size > MAX_FILE_SIZE) return `${file.name} is too large.`;
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

export function renderLibraryTab({ root, klass, onUploadsAdded }) {
  root.innerHTML = `
    <section class="workspace-section">
      <div class="upload-zone" tabindex="0">
        <input id="upload-input" type="file" multiple />
        <strong>Drop files here or browse</strong>
        <span>Notes, documents, syllabi, slides, lecture video, and lecture audio</span>
      </div>
      <div class="toolbar-row" aria-label="Upload filters">
        <button class="chip is-active" type="button">All</button>
        <button class="chip" type="button">Notes</button>
        <button class="chip" type="button">Docs</button>
        <button class="chip" type="button">Syllabus</button>
        <button class="chip" type="button">Slides</button>
        <button class="chip" type="button">Lectures</button>
      </div>
      <ul class="data-list" id="upload-list"></ul>
    </section>
  `;

  const zone = root.querySelector(".upload-zone");
  const input = root.querySelector("#upload-input");
  const list = root.querySelector("#upload-list");

  function renderUploads() {
    list.replaceChildren();
    for (const upload of klass.uploads) {
      const item = document.createElement("li");
      item.innerHTML = `
        <span class="file-name">${upload.name}</span>
        <span>${upload.kind}</span>
        <span class="status-pill status-ready">${upload.status}</span>
      `;
      list.append(item);
    }
  }

  function addFiles(files) {
    const nextUploads = [];
    const errors = [];

    for (const file of files) {
      const error = validateUpload(file);
      if (error) {
        errors.push(error);
      } else {
        nextUploads.push(createLocalUpload(file));
      }
    }

    if (nextUploads.length) onUploadsAdded(nextUploads);
    if (errors.length) window.alert(errors.join("\n"));
  }

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") input.click();
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
  input.addEventListener("change", () => addFiles(input.files));

  renderUploads();
}
