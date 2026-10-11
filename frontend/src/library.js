import { icon } from "./icons.js";
import { escapeHtml, formatBytes, openDialog, toast } from "./ui.js";
import { saveUploadedFile } from "./file-store.js";
import { getBackendClassId, getBackendUploadId, waitForUpload } from "./course-sync.js";
import { updateClass } from "./classes.js";
import {
  getUploadsApi,
  uploadFileApi,
  uploadTextApi,
  getUploadDetailApi,
  deleteUploadApi,
  retryUploadApi,
  getSyllabusApi
} from "./api.js";

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

let activeFilter = null;

function formatDate(iso) {
  if (!iso) return "recently";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function guessKind(file) {
  const lower = file.name.toLowerCase();
  if (lower.includes("syllabus")) return "Syllabus";
  if (lower.endsWith(".pptx")) return "Slides";
  if (/\.(mp4|webm|mov|mp3|m4a|wav)$/.test(lower)) return "Lecture";
  if (/\.(png|jpe?g)$/.test(lower)) return "Notes";
  return "Document";
}

function toApiKind(uiKind, fileName = "") {
  if (uiKind === "Syllabus") return "syllabus";
  if (uiKind === "Slides") return "slides";
  if (uiKind === "Notes") return "notes";
  if (uiKind === "Lecture") {
    if (/\.(mp3|m4a|wav)$/i.test(fileName)) return "lecture_audio";
    return "lecture_video";
  }
  return "document";
}

function fromApiKind(apiKind) {
  if (apiKind === "syllabus") return "Syllabus";
  if (apiKind === "slides") return "Slides";
  if (apiKind === "notes") return "Notes";
  if (apiKind === "lecture_video" || apiKind === "lecture_audio") return "Lecture";
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

export async function renderLibraryTab({ root, klass, onUploadsAdded, onUploadRemoved }) {
  // SQLite APIs use the local workspace owner and do not require a bearer token.
  let syllabusData = null;
  const backendClassId = getBackendClassId(klass);
  if (backendClassId) {
    try {
      const [remoteUploads, remoteSyllabus] = await Promise.allSettled([
        getUploadsApi(backendClassId),
        getSyllabusApi(backendClassId)
      ]);

      if (remoteUploads.status === "fulfilled" && Array.isArray(remoteUploads.value)) {
        const localByBackendId = new Map(klass.uploads.map((item) => [getBackendUploadId(item), item]));
        const remoteList = remoteUploads.value.map((item) => ({
          ...localByBackendId.get(item.id),
          id: localByBackendId.get(item.id)?.id || item.id,
          backendId: item.id,
          name: item.title || item.original_filename || "Document",
          kind: fromApiKind(item.kind),
          size: item.file_size_bytes || 0,
          status: item.status || "ready",
          progress: item.processing_progress ?? 100,
          createdAt: item.created_at || new Date().toISOString(),
          isRemote: true
        }));
        const mappedIds = new Set(remoteList.map((item) => item.id));
        klass.uploads = [...remoteList, ...klass.uploads.filter((item) => !mappedIds.has(item.id))];
        await updateClass(klass.id, { uploads: klass.uploads });
      }

      if (remoteSyllabus.status === "fulfilled" && remoteSyllabus.value?.topics?.length) {
        syllabusData = remoteSyllabus.value;
      }
    } catch (e) {
      console.warn("Could not fetch remote uploads:", e);
    }
  }

  const readyCount = klass.uploads.filter((upload) => upload.status === "ready").length;
  const workingCount = klass.uploads.filter((upload) => ["processing", "transcribing", "uploading"].includes(upload.status)).length;
  const localSyllabus = klass.uploads.find((upload) => upload.outline);

  root.innerHTML = `
    <section class="section-stack">
      <div class="library-overview">
        <div class="panel overview-card">
          <span class="kind-icon">${icon("check", { size: 20 })}</span>
          <div>
            <strong>${readyCount} ready</strong>
            <span class="row-meta">Available to study from</span>
          </div>
        </div>
        <div class="panel overview-card">
          <span class="kind-icon" data-kind="Lecture">${icon("sparkles", { size: 20 })}</span>
          <div>
            <strong>${workingCount} processing</strong>
            <span class="row-meta">Uploads process in background</span>
          </div>
        </div>
        <div class="panel overview-card">
          <span class="kind-icon" data-kind="Syllabus">${icon("syllabus", { size: 20 })}</span>
          <div>
            <strong>${escapeHtml(klass.nextExam || "No exam set")}</strong>
            <span class="row-meta">From syllabus outline</span>
          </div>
        </div>
      </div>
      ${(syllabusData || localSyllabus) ? `
        <section class="panel syllabus-panel" aria-label="Syllabus outline">
          <div>
            <h2>Syllabus map</h2>
            <p class="muted">Parsed course roadmap and exam checkpoints</p>
          </div>
          <div class="syllabus-columns">
            <div>
              <h3>Topics</h3>
              <ul class="compact-list">
                ${(syllabusData?.topics || localSyllabus?.outline?.topics || []).map((t) => `<li>${escapeHtml(typeof t === "string" ? t : t.title)}</li>`).join("")}
              </ul>
            </div>
            <div>
              <h3>Upcoming exams</h3>
              <ul class="compact-list">
                ${(syllabusData?.exams || localSyllabus?.outline?.exams || []).map((exam) => `
                  <li><strong>${escapeHtml(exam.name)}</strong><span>${escapeHtml(exam.date || "Upcoming")} · ${(exam.topics || []).join(", ")}</span></li>
                `).join("")}
              </ul>
            </div>
          </div>
        </section>
      ` : ""}
      <div class="upload-zone-wrapper">
        <label class="upload-zone" for="upload-input">
          <span class="upload-zone-icon">${icon("upload", { size: 26 })}</span>
          <strong>Drop files here or <span class="browse">browse</span></strong>
          <span class="muted">Notes, documents, syllabi, slides, and lecture video or audio</span>
          <small>PDF, DOCX, PPTX, TXT, MD, PNG, JPG, MP4, MOV, WEBM, MP3, M4A, WAV · up to ${formatBytes(MAX_FILE_SIZE)}</small>
          <input id="upload-input" class="visually-hidden" type="file" multiple
            accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.mp4,.webm,.mov,.mp3,.m4a,.wav" />
        </label>
        <div class="upload-zone-actions" style="margin-top: 0.75rem; display: flex; gap: 0.5rem; justify-content: flex-end;">
          <button id="paste-text-btn" class="btn btn-secondary" type="button">
            ${icon("pencil", { size: 16 })} Paste text notes
          </button>
        </div>
      </div>
      ${!backendClassId ? `
        <div class="account-status-banner guest-banner">
          ${icon("alert", { size: 20 })}
          <div>
            <strong>Files waiting to sync</strong>
            <span>These files are saved in this browser. Starting class tutoring or generating a study set will synchronize the original files with your class library.</span>
          </div>
        </div>
      ` : `
        <div class="account-status-banner signed-in-banner">
          ${icon("check", { size: 20 })}
          <div>
            <strong>SQLite Database Sync Active</strong>
            <span>New uploads are saved to your class library. Any older browser-only files synchronize when you start class tutoring or generate a study set.</span>
          </div>
        </div>
      `}
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
  const pasteBtn = root.querySelector("#paste-text-btn");

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
          <button class="file-name" type="button" data-preview="${escapeHtml(upload.id)}" title="Preview ${escapeHtml(upload.name)}">${escapeHtml(upload.name)}</button>
          <span class="row-meta">${escapeHtml(upload.kind)}${upload.isSample ? " - Built-in sample content" : ` - ${formatBytes(upload.size || 0)} - added ${formatDate(upload.createdAt)}`}</span>
          ${upload.summary ? `<span class="row-summary">${escapeHtml(upload.summary)}</span>` : ""}
          ${upload.sources?.length ? `<span class="source-chips">${upload.sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("")}</span>` : ""}
          ${(upload.progress < 100 && upload.status !== "failed") ? `<span class="progress" aria-label="${escapeHtml(upload.status)} ${upload.progress}% complete"><span style="width: ${upload.progress}%"></span></span>` : ""}
        </span>
        <span class="pill" data-tone="${STATUS_TONES[upload.status] || ""}"><strong>${escapeHtml(upload.status)}</strong></span>
        <span class="row-actions">
          ${upload.status === "failed" ? `<button class="btn btn-secondary btn-small" type="button" data-retry="${escapeHtml(upload.id)}">${icon("sparkles", { size: 16 })} Retry</button>` : ""}
          <button class="btn btn-secondary btn-small" type="button" data-preview="${escapeHtml(upload.id)}">${icon("file", { size: 16 })} View</button>
          <button class="btn btn-ghost btn-icon" type="button" data-remove="${upload.id}" aria-label="Remove ${escapeHtml(upload.name)}">
            ${icon("trash", { size: 18 })}
          </button>
        </span>
      </li>
    `).join("");
  }

  async function addFiles(files) {
    const candidates = [];

    for (const file of files) {
      const error = validateUpload(file);
      if (error) {
        toast(error, { tone: "error" });
      } else {
        candidates.push(file);
      }
    }

    if (!candidates.length) return;

    const nextUploads = [];

    for (const file of candidates) {
      const uiKind = guessKind(file);
      const apiKind = toApiKind(uiKind, file.name);

      if (backendClassId) {
        try {
          const res = await uploadFileApi(backendClassId, file, apiKind, file.name);
          const uploadEntry = {
            id: res.id,
            name: file.name,
            kind: uiKind,
            size: file.size,
            status: res.status || "processing",
            progress: res.processing_progress ?? 0,
            createdAt: res.created_at || new Date().toISOString(),
            isRemote: true
          };
          nextUploads.push(uploadEntry);
          await saveUploadedFile(uploadEntry.id, file).catch(() => {});
          pollUploadStatus(uploadEntry.id);
        } catch (err) {
          toast(`Backend upload failed: ${err.message}. Saving locally.`, { tone: "busy" });
          const localEntry = createLocalUpload(file);
          await saveUploadedFile(localEntry.id, file).catch(() => {});
          nextUploads.push(localEntry);
        }
      } else {
        const localEntry = createLocalUpload(file);
        await saveUploadedFile(localEntry.id, file).catch(() => {});
        nextUploads.push(localEntry);
      }
    }

    if (nextUploads.length) {
      toast(`Added ${nextUploads.length} ${nextUploads.length === 1 ? "file" : "files"} to ${klass.name}.`);
      onUploadsAdded(nextUploads);
      renderFilters();
      renderUploads();
    }
  }

  async function pollUploadStatus(uploadId, backendUploadId = uploadId) {
    const controller = new AbortController();
    const stopWaiting = () => controller.abort();
    window.addEventListener("hashchange", stopWaiting, { once: true });
    try {
      await waitForUpload(backendUploadId, {
        signal: controller.signal,
        onStatus: async (detail) => {
          const item = klass.uploads.find((u) => u.id === uploadId);
          if (!item) return;
          item.status = detail.status;
          item.progress = detail.processing_progress ?? 100;
          if (detail.summary) item.summary = detail.summary;
          await updateClass(klass.id, { uploads: klass.uploads });
          renderUploads();
        }
      });
    } catch (error) {
      if (!controller.signal.aborted) toast(error.message, { tone: "error" });
    } finally {
      window.removeEventListener("hashchange", stopWaiting);
    }
  }

  pasteBtn.addEventListener("click", async () => {
    const text = await openDialog({
      title: "Paste notes or text",
      message: "Add lecture notes, reading excerpts, or a syllabus outline.",
      input: { label: "Pasted text content", placeholder: "Paste your notes here...", multiline: true, maxLength: 1_000_000 },
      confirmLabel: "Upload text"
    });

    if (!text || !text.trim()) return;

    const title = text.trim().slice(0, 40) + "...";

    if (backendClassId) {
      try {
        const res = await uploadTextApi(backendClassId, text, "notes", title);
        const uploadEntry = {
          id: res.id,
          name: title,
          kind: "Notes",
          size: text.length,
          status: res.status || "processing",
          progress: res.processing_progress ?? 0,
          createdAt: res.created_at || new Date().toISOString(),
          content: text,
          isRemote: true
        };
        onUploadsAdded([uploadEntry]);
        pollUploadStatus(uploadEntry.id);
        toast("Pasted notes uploaded to class library.");
        return;
      } catch (err) {
        toast(`Upload failed: ${err.message}`, { tone: "error" });
      }
    }

    const localEntry = {
      id: crypto.randomUUID(),
      name: title,
      kind: "Notes",
      size: text.length,
      status: "ready",
      progress: 100,
      createdAt: new Date().toISOString(),
      content: text
    };
    onUploadsAdded([localEntry]);
    toast("Notes saved locally.");
  });

  filters.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-kind]");
    if (!chip) return;
    activeFilter = chip.dataset.kind || null;
    renderFilters();
    renderUploads();
  });

  list.addEventListener("click", async (event) => {
    const previewId = event.target.closest("[data-preview]")?.dataset.preview;
    if (previewId) {
      const upload = klass.uploads.find((item) => item.id === previewId);
      if (upload) {
        // If remote, fetch full detail including extracted text & signed URL
        const backendUploadId = getBackendUploadId(upload);
        if (backendUploadId && !upload.content) {
          try {
            const detail = await getUploadDetailApi(backendUploadId);
            upload.content = detail.extracted_text;
            upload.summary = detail.summary;
            upload.fileUrl = detail.file_url;
            upload.transcript = (detail.transcript_segments || []).map((seg) => ({
              at: `${Math.floor(seg.start_seconds / 60)}:${Math.floor(seg.start_seconds % 60).toString().padStart(2, "0")}`,
              text: seg.text
            }));
          } catch (e) {
            console.warn("Could not load upload detail:", e);
          }
        }

        import("./file-preview.js")
          .then(({ openUploadPreview }) => openUploadPreview(upload))
          .catch(() => toast("The file preview could not be opened. Try reloading the page.", { tone: "error" }));
      }
      return;
    }

    const retryId = event.target.closest("[data-retry]")?.dataset.retry;
    if (retryId) {
      try {
        const upload = klass.uploads.find((item) => item.id === retryId);
        const backendUploadId = upload && getBackendUploadId(upload);
        if (!backendUploadId) throw new Error("Upload this source to the backend before retrying processing.");
        const detail = await retryUploadApi(backendUploadId);
        upload.status = detail.status;
        upload.progress = detail.processing_progress ?? 0;
        await updateClass(klass.id, { uploads: klass.uploads });
        renderUploads();
        toast("Processing retry requested.");
        pollUploadStatus(retryId, backendUploadId);
      } catch (e) {
        toast(e.message, { tone: "error" });
      }
      return;
    }

    const button = event.target.closest("[data-remove]");
    if (button) {
      const uploadId = button.dataset.remove;
      const upload = klass.uploads.find((item) => item.id === uploadId);
      const backendUploadId = upload && getBackendUploadId(upload);
      if (backendUploadId) {
        try {
          await deleteUploadApi(backendUploadId);
        } catch (error) {
          toast(error.message, { tone: "error" });
          return;
        }
      }
      onUploadRemoved?.(uploadId);
    }
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
