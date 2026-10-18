import { icon } from "./icons.js";
import { escapeHtml, formatBytes, openDialog, toast } from "./ui.js";
import { saveUploadedFile } from "./file-store.js";
import { getBackendClassId, getBackendUploadId, waitForUpload } from "./course-sync.js";
import { classHasCapability, updateClass } from "./classes.js";
import {
  getUploadsApi,
  uploadFileApi,
  uploadTextApi,
  getUploadDetailApi,
  deleteUploadApi,
  retryUploadApi,
  getSyllabusApi,
  updateResourcePublicationApi,
  searchClassSourcesApi,
  getClassSourceChunkApi
} from "./api.js";
import { sourceChunkMarkup, sourceSearchHitMarkup } from "./source-search.js";

const ACCEPTED_EXTENSIONS = new Set(["pdf", "docx", "pptx", "txt", "md", "png", "jpg", "jpeg", "mp4", "webm", "mov", "mp3", "m4a", "wav"]);
const MAX_FILE_SIZE = 100 * 1024 * 1024;

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

export function reconcileRemoteUploads(localUploads, remoteUploads) {
  const localByBackendId = new Map(localUploads.map((item) => [getBackendUploadId(item), item]));
  const remoteList = remoteUploads.map((item) => ({
    ...localByBackendId.get(item.id),
    id: localByBackendId.get(item.id)?.id || item.id,
    backendId: item.id,
    name: item.title || item.original_filename || "Document",
    kind: fromApiKind(item.kind),
    size: item.file_size_bytes || 0,
    status: item.status || "ready",
    progress: item.processing_progress ?? 100,
    createdAt: item.created_at || new Date().toISOString(),
    resourceScope: item.resource_scope || "class_owned",
    createdBy: item.created_by || null,
    publicationState: item.publication_state || "published",
    releaseAt: item.release_at || null,
    isRemote: true
  }));
  const localOnly = localUploads.filter((item) => !getBackendUploadId(item));
  return [...remoteList, ...localOnly];
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

export async function renderLibraryTab({ root, klass, currentUserId = null, onUploadsAdded, onUploadRemoved }) {
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
        klass.uploads = reconcileRemoteUploads(klass.uploads, remoteUploads.value);
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
  const searchableUploads = klass.uploads.filter((upload) =>
    getBackendUploadId(upload)
    && upload.status === "ready");

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
      ${backendClassId && classHasCapability(klass, "read") ? `
        <section class="panel source-search-panel" aria-labelledby="source-search-title">
          <div class="source-search-heading">
            <div>
              <h2 id="source-search-title">Search course sources</h2>
              <p class="muted">Search indexed material available to you. The server checks access and release status for every search and fetch.</p>
            </div>
          </div>
          <form class="source-search-form" role="search" aria-label="Search course sources" id="source-search-form">
            <label class="field source-search-query"><span>Search terms</span><input type="search" name="query" required maxlength="300" autocomplete="off" placeholder="e.g. DNA replication" /></label>
            <label class="field source-search-filter"><span>Source</span><select name="upload_id">
              <option value="">All released sources</option>
              ${searchableUploads.map((upload) => `<option value="${escapeHtml(getBackendUploadId(upload))}">${escapeHtml(upload.name || "Course material")}</option>`).join("")}
            </select></label>
            <button class="btn btn-secondary source-search-submit" type="submit">Search sources</button>
          </form>
          ${searchableUploads.length ? "" : `<p class="field-hint">No ready backend sources are available to search yet. Search becomes available after a source finishes processing and indexing.</p>`}
          <p class="field-hint source-search-status" id="source-search-status" role="status" aria-live="polite"></p>
          <ol class="source-search-results" id="source-search-results" aria-label="Source search results" aria-live="polite"></ol>
          <div class="source-chunk-detail" id="source-chunk-detail" hidden aria-live="polite"></div>
        </section>
      ` : ""}
      ${classHasCapability(klass, "create") ? `<div class="upload-zone-wrapper">
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
      </div>` : `<div class="account-status-banner guest-banner"><div><strong>Read-only class access</strong><span>You can study and tutor from released class materials. A class manager can grant permission to add shared resources.</span></div></div>`}
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
            <strong>Saved to your account</strong>
            <span>New uploads are saved to your class library. Study generation, reasoning checks, and quiz-review tutoring send authorized course excerpts to this deployment's configured AI provider. Live tutoring uses Gemini Live; scanned PDFs and lecture recordings may use Gemini Files. Older browser-only files synchronize when you start class tutoring or generate a study set.</span>
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
  const sourceSearchForm = root.querySelector("#source-search-form");
  const sourceSearchStatus = root.querySelector("#source-search-status");
  const sourceSearchResults = root.querySelector("#source-search-results");
  const sourceChunkDetail = root.querySelector("#source-chunk-detail");
  let activeSearchController = null;
  let activeChunkController = null;
  let sourceSearchGeneration = 0;
  let activeChunkButton = null;

  function clearSourceChunk({ restoreFocus = false } = {}) {
    activeChunkController?.abort();
    activeChunkController = null;
    if (sourceChunkDetail) {
      sourceChunkDetail.hidden = true;
      sourceChunkDetail.textContent = "";
    }
    sourceSearchResults?.querySelectorAll("[data-source-chunk]").forEach((button) => {
      button.setAttribute("aria-expanded", "false");
    });
    if (restoreFocus) activeChunkButton?.focus();
    activeChunkButton = null;
  }

  if (sourceSearchForm && sourceSearchStatus && sourceSearchResults && sourceChunkDetail) {
    const queryInput = sourceSearchForm.elements.query;
    const uploadFilter = sourceSearchForm.elements.upload_id;
    const searchButton = sourceSearchForm.querySelector('button[type="submit"]');
    sourceSearchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const query = String(queryInput.value || "").trim();
      if (!query) {
        sourceSearchStatus.textContent = "Enter a topic or phrase to search.";
        queryInput.focus();
        return;
      }
      activeSearchController?.abort();
      activeChunkController?.abort();
      clearSourceChunk();
      activeSearchController = new AbortController();
      const controller = activeSearchController;
      const generation = ++sourceSearchGeneration;
      sourceSearchResults.innerHTML = "";
      sourceSearchStatus.textContent = "Searching released sources…";
      searchButton.disabled = true;
      sourceSearchForm.setAttribute("aria-busy", "true");
      try {
        const results = await searchClassSourcesApi(backendClassId, {
          query,
          uploadId: uploadFilter.value || null,
          limit: 20,
          signal: controller.signal
        });
        if (controller.signal.aborted || generation !== sourceSearchGeneration) return;
        sourceSearchResults.innerHTML = results.map((item, index) => sourceSearchHitMarkup(item, index)).filter(Boolean).join("");
        const count = sourceSearchResults.querySelectorAll("[data-source-chunk]").length;
        sourceSearchStatus.textContent = count
          ? `Found ${count} source ${count === 1 ? "passage" : "passages"}. Select one to load its exact indexed text.`
          : "No matching passages were found in released, indexed sources.";
      } catch (error) {
        if (controller.signal.aborted || generation !== sourceSearchGeneration) return;
        sourceSearchStatus.textContent = error.message || "Could not search course sources. Try again.";
      } finally {
        if (generation === sourceSearchGeneration) {
          searchButton.disabled = false;
          sourceSearchForm.removeAttribute("aria-busy");
          activeSearchController = null;
        }
      }
    });

    sourceSearchResults.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-source-chunk]");
      if (!button) return;
      const chunkRevisionId = button.dataset.sourceChunk;
      if (!chunkRevisionId) return;
      clearSourceChunk();
      const controller = new AbortController();
      activeChunkController = controller;
      activeChunkButton = button;
      button.setAttribute("aria-expanded", "true");
      sourceChunkDetail.hidden = false;
      sourceChunkDetail.textContent = "Loading the exact source revision…";
      sourceSearchStatus.textContent = "Loading selected source passage…";
      try {
        const chunk = await getClassSourceChunkApi(backendClassId, chunkRevisionId, { signal: controller.signal });
        if (controller.signal.aborted || activeChunkController !== controller) return;
        if (chunk.chunk_revision_id !== chunkRevisionId || typeof chunk.text !== "string") {
          throw new Error("The server did not return the exact indexed source revision. Search again.");
        }
        sourceChunkDetail.innerHTML = sourceChunkMarkup(chunk);
        sourceSearchStatus.textContent = "Exact indexed source loaded.";
      } catch (error) {
        if (controller.signal.aborted || activeChunkController !== controller) return;
        clearSourceChunk({ restoreFocus: true });
        sourceSearchStatus.textContent = "The selected source could not be loaded. It may have changed or access may have been removed.";
      }
    });

    sourceChunkDetail.addEventListener("click", (event) => {
      if (event.target.closest("[data-close-source-chunk]")) clearSourceChunk({ restoreFocus: true });
    });

    sourceChunkDetail.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-open-original-source]");
      if (!button || button.disabled) return;
      const controller = activeChunkController;
      if (!controller || controller.signal.aborted) return;
      button.disabled = true;
      sourceSearchStatus.textContent = "Opening the authorized original source…";
      try {
        const citation = JSON.parse(button.dataset.sourceCitation || "{}");
        if (!citation.upload_id) throw new Error("This passage has no original source link.");
        const detail = await getUploadDetailApi(citation.upload_id);
        if (controller.signal.aborted || activeChunkController !== controller || !button.isConnected) return;
        if (!detail.id || detail.status !== "ready") {
          throw new Error("This source is no longer ready to open. Search again for the latest version.");
        }
        const upload = {
          id: detail.id,
          backendId: detail.id,
          name: detail.title || detail.original_filename || citation.name || "Course material",
          kind: detail.kind,
          size: detail.file_size_bytes || 0,
          content: detail.extracted_text || "",
          fileUrl: detail.file_url,
          originalFilename: detail.original_filename,
          status: detail.status
        };
        const extension = String(detail.original_filename || "").split(".").pop().toLowerCase();
        const initialView = ["docx", "pptx", "txt", "md"].includes(extension) ? "text" : "original";
        const { openUploadPreview } = await import("./file-preview.js");
        if (controller.signal.aborted || activeChunkController !== controller || !button.isConnected) return;
        await openUploadPreview(upload, { citation, initialView });
        sourceSearchStatus.textContent = `Opened the original source at ${sourceLocatorText(citation)}.`;
      } catch (error) {
        if (controller.signal.aborted || activeChunkController !== controller || !button.isConnected) return;
        sourceSearchStatus.textContent = error.message || "The original source could not be opened. Search again or try later.";
        button.disabled = false;
        button.focus();
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });

    window.addEventListener("hashchange", () => {
      sourceSearchGeneration += 1;
      activeSearchController?.abort();
      activeChunkController?.abort();
    }, { once: true });
  }

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

    list.innerHTML = visible.map((upload) => {
      const isAuthor = upload.createdBy && String(upload.createdBy) === String(currentUserId);
      const canEdit = klass.isOwner || (isAuthor && classHasCapability(klass, "edit"));
      const canDelete = klass.isOwner || (isAuthor && classHasCapability(klass, "delete"));
      const canPublish = klass.isOwner || classHasCapability(klass, "manage_permissions")
        || (isAuthor && classHasCapability(klass, "create"));
      return `
      <li>
        <span class="kind-icon" data-kind="${escapeHtml(upload.kind)}">${icon(KIND_ICONS[upload.kind] || "file")}</span>
        <div class="row-main">
          <button class="file-name" type="button" data-preview="${escapeHtml(upload.id)}" title="Preview ${escapeHtml(upload.name)}">${escapeHtml(upload.name)}</button>
          <span class="row-meta">${escapeHtml(upload.kind)}${upload.isSample ? " - Built-in sample content" : ` - ${formatBytes(upload.size || 0)} - added ${formatDate(upload.createdAt)}`}</span>
          ${upload.localOnly ? `<span class="field-hint" role="note">Saved on this device only${upload.localCopyStatus === "session" ? " for this browser session" : ""}; not uploaded to Jot.</span>` : upload.localCopyStatus === "unavailable" ? `<span class="field-hint" role="note">The server copy is saved, but this browser could not keep a local recovery copy.</span>` : upload.localCopyStatus === "session" ? `<span class="field-hint" role="note">The server copy is saved; its local recovery copy lasts only for this browser session.</span>` : ""}
          ${upload.summary ? `<span class="row-summary">${escapeHtml(upload.summary)}</span>` : ""}
          ${upload.resourceScope === "student_contribution" ? `<span class="permission-badge">Shared contribution</span>` : ""}
          ${upload.publicationState && upload.resourceScope !== "personal" ? `<span class="permission-badge" data-publication="${escapeHtml(upload.publicationState)}">${upload.publicationState === "scheduled" ? `Scheduled for ${escapeHtml(formatDate(upload.releaseAt))}` : escapeHtml(upload.publicationState)}</span>` : ""}
          ${upload.sources?.length ? `<span class="source-chips">${upload.sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("")}</span>` : ""}
          ${(upload.progress < 100 && upload.status !== "failed") ? `<span class="progress" aria-label="${escapeHtml(upload.status)} ${upload.progress}% complete"><span style="width: ${upload.progress}%"></span></span>` : ""}
          ${upload.isRemote && upload.resourceScope !== "personal" && canPublish ? `<details class="resource-release-control"><summary>Manage release</summary><form data-upload-publication="${escapeHtml(upload.id)}"><label class="field"><span>Visibility</span><select name="state"><option value="draft" ${upload.publicationState === "draft" ? "selected" : ""}>Draft</option><option value="published" ${upload.publicationState === "published" ? "selected" : ""}>Published</option><option value="scheduled" ${upload.publicationState === "scheduled" ? "selected" : ""}>Schedule release</option></select></label><label class="field" data-release-date-wrap ${upload.publicationState === "scheduled" ? "" : "hidden"}><span>Release date and time</span><input name="release_at" type="datetime-local" value="${escapeHtml(toLocalDateTime(upload.releaseAt))}" /></label><button class="btn btn-secondary btn-small" type="submit">Save release</button></form></details>` : ""}
        </div>
        <span class="pill" data-tone="${STATUS_TONES[upload.status] || ""}"><strong>${escapeHtml(upload.status)}</strong></span>
        <span class="row-actions">
          ${upload.status === "failed" && canEdit ? `<button class="btn btn-secondary btn-small" type="button" data-retry="${escapeHtml(upload.id)}">${icon("sparkles", { size: 16 })} Retry</button>` : ""}
          <button class="btn btn-secondary btn-small" type="button" data-preview="${escapeHtml(upload.id)}">${icon("file", { size: 16 })} View</button>
          ${canDelete ? `<button class="btn btn-ghost btn-icon" type="button" data-remove="${escapeHtml(upload.id)}" aria-label="Remove ${escapeHtml(upload.name)}">${icon("trash", { size: 18 })}</button>` : ""}
        </span>
      </li>
    `;
    }).join("");
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
            resourceScope: res.resource_scope || "class_owned",
            createdBy: res.created_by || null,
            publicationState: res.publication_state || "published",
            releaseAt: res.release_at || null,
            localCopyStatus: "saving",
            isRemote: true
          };
          nextUploads.push(uploadEntry);
          try {
            uploadEntry.localCopyStatus = await saveUploadedFile(uploadEntry.id, file);
          } catch (storageError) {
            uploadEntry.localCopyStatus = "unavailable";
            toast(`Uploaded to Jot, but this browser could not save a recovery copy: ${storageError.message || "local storage failed"}.`, { tone: "error" });
          }
          pollUploadStatus(uploadEntry.id);
        } catch (err) {
          const localEntry = createLocalUpload(file);
          try {
            localEntry.localOnly = true;
            localEntry.localCopyStatus = await saveUploadedFile(localEntry.id, file);
            nextUploads.push(localEntry);
            toast(`Jot upload failed (${err.message}). This file is saved on this device only and has not been uploaded.`, { tone: "busy" });
          } catch (storageError) {
            toast(`Jot upload failed (${err.message}), and this browser could not save a local copy (${storageError.message || "local storage failed"}). The file was not added.`, { tone: "error" });
          }
        }
      } else {
        const localEntry = createLocalUpload(file);
        try {
          localEntry.localOnly = true;
          localEntry.localCopyStatus = await saveUploadedFile(localEntry.id, file);
          nextUploads.push(localEntry);
        } catch (storageError) {
          toast(`This file was not added because local browser storage failed: ${storageError.message || "storage unavailable"}.`, { tone: "error" });
        }
      }
    }

    if (nextUploads.length) {
      const localOnlyCount = nextUploads.filter((upload) => upload.localOnly).length;
      const uploadedCount = nextUploads.length - localOnlyCount;
      if (localOnlyCount && uploadedCount) {
        toast(`Uploaded ${uploadedCount} file${uploadedCount === 1 ? "" : "s"} to Jot; ${localOnlyCount} saved on this device only.`, { tone: "busy" });
      } else if (localOnlyCount) {
        toast(`${localOnlyCount} file${localOnlyCount === 1 ? "" : "s"} saved on this device only; not uploaded to Jot.`, { tone: "busy" });
      } else {
        toast(`Uploaded ${uploadedCount} ${uploadedCount === 1 ? "file" : "files"} to ${klass.name}.`);
      }
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

  pasteBtn?.addEventListener("click", async () => {
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
          resourceScope: res.resource_scope || "class_owned",
          createdBy: res.created_by || null,
          publicationState: res.publication_state || "published",
          releaseAt: res.release_at || null,
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
        if (backendUploadId) {
          try {
            const detail = await getUploadDetailApi(backendUploadId);
            upload.content = detail.extracted_text;
            upload.summary = detail.summary;
            upload.fileUrl = detail.file_url;
            upload.originalFilename = detail.original_filename;
            upload.document = detail.document;
            upload.status = detail.status;
            upload.transcript = (detail.transcript_segments || []).map((seg) => ({
              at: `${Math.floor(seg.start_seconds / 60)}:${Math.floor(seg.start_seconds % 60).toString().padStart(2, "0")}`,
              text: seg.text
            }));
          } catch (e) {
            console.warn("Could not load upload detail:", e);
          }
        }

        const isAuthor = upload.createdBy && String(upload.createdBy) === String(currentUserId);
        const canRetryProcessing = klass.isOwner || (isAuthor && classHasCapability(klass, "edit"));
        import("./file-preview.js")
          .then(({ openUploadPreview }) => openUploadPreview({ ...upload, canRetryProcessing }))
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

  list.addEventListener("change", (event) => {
    if (!event.target.matches('[data-upload-publication] [name="state"]')) return;
    const form = event.target.closest("[data-upload-publication]");
    form.querySelector("[data-release-date-wrap]").hidden = event.target.value !== "scheduled";
  });

  list.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-upload-publication]");
    if (!form) return;
    event.preventDefault();
    const upload = klass.uploads.find((item) => item.id === form.dataset.uploadPublication);
    if (!upload) return;
    const state = form.querySelector('[name="state"]').value;
    const localDate = form.querySelector('[name="release_at"]').value;
    if (state === "scheduled" && !localDate) {
      toast("Choose a release date and time.", { tone: "error" });
      return;
    }
    try {
      const response = await updateResourcePublicationApi("uploads", upload.backendId || upload.id, {
        state,
        releaseAt: state === "scheduled" ? new Date(localDate).toISOString() : null
      });
      upload.publicationState = response.publication_state || state;
      upload.releaseAt = response.release_at || (state === "scheduled" ? new Date(localDate).toISOString() : null);
      await updateClass(klass.id, { uploads: klass.uploads });
      toast(state === "scheduled" ? "Release scheduled." : `Course material ${state}.`);
      renderUploads();
    } catch (error) {
      toast(`Could not update release settings: ${error.message}`, { tone: "error" });
    }
  });

  zone?.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  });
  zone?.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone?.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    addFiles(event.dataTransfer.files);
  });
  input?.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });

  renderFilters();
  renderUploads();
}

function toLocalDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Date(date.valueOf() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
