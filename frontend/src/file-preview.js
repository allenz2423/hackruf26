import JSZip from "jszip";
import { getUploadedFile } from "./file-store.js";
import { escapeHtml, formatBytes, toast } from "./ui.js";
import { getBackendUploadId } from "./course-sync.js";
import {
  getUploadDetailApi,
  getUploadDocumentApi,
  createUploadDocumentApi,
  downloadUploadDocumentApi,
  retryUploadApi
} from "./api.js";
import { formatSourceCitation } from "./source-citations.js";
import { mathHtml, typesetMath } from "./math.js";

const MAX_TEXT_PREVIEW = 2 * 1024 * 1024;

export function uploadDocumentErrorMessage(error) {
  if (error?.status === 404) {
    return "This course material is no longer available. Refresh the Library; if it was removed, upload the file again if you still have access.";
  }
  return error?.message || "Could not load the study document. Try again.";
}

export function describeUploadProcessing(detail = {}) {
  if (detail.status === "failed") {
    return {
      state: "failed",
      progress: null,
      message: "The original upload failed to process. Retry upload processing before generating its study document."
    };
  }
  if (detail.status === "ready") {
    return { state: "ready", progress: 100, message: "Original upload is ready." };
  }
  const rawProgress = Number(detail.processing_progress);
  const progress = Number.isFinite(rawProgress) ? Math.max(0, Math.min(100, rawProgress)) : 0;
  return {
    state: "processing",
    progress,
    message: `Processing original upload… ${progress}%`
  };
}

function waitFor(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason || new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(signal.reason || new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

function extensionOf(name) {
  return name.split(".").pop().toLowerCase();
}

function makeDialog(upload) {
  const dialog = document.createElement("dialog");
  dialog.className = "dialog preview-dialog";
  dialog.setAttribute("aria-labelledby", "preview-title");
  dialog.innerHTML = `
    <header class="preview-header">
      <div>
        <h2 id="preview-title">${escapeHtml(upload.name)}</h2>
        <p class="row-meta">${escapeHtml(upload.kind)}${upload.isSample ? " - Sample file" : ` - ${formatBytes(upload.size)}`}</p>
      </div>
      <button class="btn btn-secondary" type="button" data-close aria-label="Close file preview">Close</button>
    </header>
    <div class="preview-content" id="preview-content" aria-live="polite">
      <p class="muted">Opening preview...</p>
    </div>
    <footer class="preview-footer" id="preview-footer"></footer>
  `;
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function setTextPreview(container, text, citation = null) {
  const sourceReference = citation
    ? `<p class="field-hint" role="note">Source reference: ${escapeHtml(formatSourceCitation(citation))}</p>`
    : "";
  container.innerHTML = `${sourceReference}<pre class="document-text">${escapeHtml(text)}</pre>`;
}

export async function readPowerPoint(file) {
  const archive = await JSZip.loadAsync(file);
  const slideNames = Object.keys(archive.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
    .sort((a, b) => Number(a.match(/slide(\d+)/)[1]) - Number(b.match(/slide(\d+)/)[1]));
  const slides = await Promise.all(slideNames.map(async (name, index) => {
    const xml = new DOMParser().parseFromString(await archive.files[name].async("text"), "application/xml");
    const paragraphs = [...xml.getElementsByTagNameNS("*", "p")].map((paragraph) =>
      [...paragraph.getElementsByTagNameNS("*", "t")].map((node) => node.textContent).join("").trim()
    ).filter(Boolean);
    return paragraphs.length ? `Slide ${index + 1}\n${paragraphs.join("\n")}` : "";
  }));
  return slides.filter(Boolean).join("\n\n");
}

export async function readWordDocument(file) {
  const archive = await JSZip.loadAsync(file);
  const documentFile = archive.file("word/document.xml");
  if (!documentFile) return "";
  const xml = new DOMParser().parseFromString(await documentFile.async("text"), "application/xml");
  return [...xml.getElementsByTagNameNS("*", "p")].map((paragraph) =>
    [...paragraph.getElementsByTagNameNS("*", "t")].map((node) => node.textContent).join("").trim()
  ).filter(Boolean).join("\n\n");
}

function locateOriginal(container, citation) {
  const media = container.querySelector("audio, video");
  if (media && Number.isFinite(citation?.timestamp_seconds) && citation.timestamp_seconds >= 0) {
    const seek = () => { media.currentTime = Math.min(citation.timestamp_seconds, media.duration || citation.timestamp_seconds); };
    if (media.readyState >= 1) seek();
    else media.addEventListener("loadedmetadata", seek, { once: true });
  }
}

export function citedPdfUrl(url, citation) {
  return Number.isInteger(citation?.page) && citation.page > 0
    ? `${url.split("#")[0]}#page=${citation.page}` : url;
}

export function uploadDocumentSectionsHtml(sections, uploadName) {
  return (Array.isArray(sections) ? sections : []).map((section) => {
    const paragraphs = String(section.body || "")
      .split(/\n{2,}/)
      .filter((paragraph) => paragraph.trim())
      .map((paragraph) => `<p>${mathHtml(paragraph.trim()).replace(/\n/g, "<br>")}</p>`)
      .join("");
    const sources = (section.sources || [])
      .map((source) => escapeHtml(formatSourceCitation({ ...source, name: uploadName })))
      .join("; ");
    const keyTerms = Array.isArray(section.key_terms) && section.key_terms.length
      ? `<p><strong>Key terms:</strong> ${section.key_terms.map((term) => mathHtml(term)).join(", ")}</p>`
      : "";
    return `
      <section class="guide-section">
        <h3 class="guide-section-heading">${mathHtml(section.heading || "")}</h3>
        <div class="guide-section-body">${paragraphs}</div>
        ${keyTerms}
        <p class="row-meta">${sources}</p>
      </section>`;
  }).join("");
}

function renderFilePreview(dialog, file, citation) {
  const content = dialog.querySelector("#preview-original") || dialog.querySelector("#preview-content");
  const footer = dialog.querySelector("#preview-footer");
  const extension = extensionOf(file.name);
  const url = URL.createObjectURL(file);
  dialog.addEventListener("close", () => URL.revokeObjectURL(url), { once: true });
  footer.innerHTML = `<a class="btn btn-secondary" href="${url}" download="${escapeHtml(file.name)}">Download original</a>`;

  if (extension === "pdf") {
    content.innerHTML = `<iframe class="document-frame" src="${escapeHtml(citedPdfUrl(url, citation))}" title="${escapeHtml(file.name)}"></iframe>`;
  } else if (["png", "jpg", "jpeg"].includes(extension)) {
    content.innerHTML = `<img class="image-preview" src="${url}" alt="Preview of ${escapeHtml(file.name)}" />`;
  } else if (["mp4", "webm", "mov"].includes(extension)) {
    content.innerHTML = `<video class="media-preview" src="${url}" controls playsinline>Your browser cannot play this video format.</video>`;
  } else if (["mp3", "m4a", "wav"].includes(extension)) {
    content.innerHTML = `<audio class="audio-preview" src="${url}" controls>Your browser cannot play this audio format.</audio>`;
  } else if (["txt", "md"].includes(extension)) {
    file.slice(0, MAX_TEXT_PREVIEW).text().then((text) => {
      setTextPreview(content, text + (file.size > MAX_TEXT_PREVIEW ? "\n\n[Preview truncated. Download the original to read the rest.]" : ""));
    });
  } else if (extension === "docx") {
    readWordDocument(file).then((text) => setTextPreview(content, text || "No readable text was found in this document."))
      .catch(() => setTextPreview(content, "This Word document could not be read in the preview. Download the original file to open it."));
  } else if (extension === "pptx") {
    readPowerPoint(file).then((text) => setTextPreview(content, text || "No slide text was found in this presentation."))
      .catch(() => setTextPreview(content, "This PowerPoint file could not be read in the preview. Download the original file to open it."));
  }
  locateOriginal(content, citation);
}

export async function openUploadPreview(upload, { citation = null, initialView = "document" } = {}) {
  const dialog = makeDialog(upload);
  const content = dialog.querySelector("#preview-content");
  const backendId = getBackendUploadId(upload);
  if (backendId) {
    content.innerHTML = `
      <nav class="study-mode-actions" aria-label="Document views">
        <button class="btn btn-primary" type="button" data-preview-view="document" aria-pressed="true">Study document</button>
        <button class="btn btn-secondary" type="button" data-preview-view="original" aria-pressed="false">Original file</button>
        <button class="btn btn-secondary" type="button" data-preview-view="text" aria-pressed="false">Original text</button>
      </nav>
      <section id="preview-document">
        <div class="study-mode-actions">
          <label for="document-detail-level">Explanation detail</label>
          <select id="document-detail-level">
            <option value="concise">Concise</option><option value="balanced" selected>Balanced</option><option value="detailed">Detailed</option>
          </select>
          <button class="btn btn-secondary" type="button" data-upload-retry hidden>Retry upload processing</button>
          <button class="btn btn-secondary" type="button" data-document-retry hidden>Retry document</button>
        </div>
        <p class="field-hint">A study-friendly explanation with the original extracted source text included in the saved PDF. Your uploaded file stays unchanged.</p>
        <p role="status" aria-live="polite" data-document-status></p>
        <div class="progress preview-processing-progress" data-processing-progress hidden role="progressbar" aria-label="Processing progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span></span></div>
        <div class="study-mode-actions" data-document-downloads hidden>
          <button class="btn btn-primary" type="button" data-document-download="pdf">Download PDF</button>
          <button class="btn btn-secondary" type="button" data-document-download="latex">Download LaTeX</button>
          <button class="btn btn-secondary" type="button" data-document-download="original_text">Download source text</button>
        </div>
        <div class="guide-content" data-document-body></div>
      </section>
      <section id="preview-original" hidden></section>
      <section id="preview-text" hidden></section>`;
    const original = content.querySelector("#preview-original");
    const textPanel = content.querySelector("#preview-text");
    const documentPanel = content.querySelector("#preview-document");
    const status = content.querySelector("[data-document-status]");
    const body = content.querySelector("[data-document-body]");
    const detailSelect = content.querySelector("#document-detail-level");
    const retry = content.querySelector("[data-document-retry]");
    const uploadRetry = content.querySelector("[data-upload-retry]");
    const progress = content.querySelector("[data-processing-progress]");
    const progressFill = progress.querySelector("span");
    const downloads = content.querySelector("[data-document-downloads]");
    let controller = null;
    let downloading = false;
    dialog.addEventListener("close", () => controller?.abort(), { once: true });
    setTextPreview(textPanel, upload.content || "The original extracted text is not available yet.", citation);

    function setProgress(label, value) {
      const amount = Math.max(0, Math.min(100, Number(value) || 0));
      status.textContent = `${label} ${amount}%`;
      progress.hidden = false;
      progress.setAttribute("aria-label", label.trim());
      progress.setAttribute("aria-valuenow", String(amount));
      progressFill.style.width = `${amount}%`;
      retry.hidden = true;
      uploadRetry.hidden = true;
    }

    function showUploadState(detail) {
      const state = describeUploadProcessing(detail);
      if (state.state === "failed") {
        status.textContent = upload.canRetryProcessing
          ? state.message
          : "The original upload failed to process. Ask a class manager or the file's owner to retry upload processing.";
        progress.hidden = true;
        uploadRetry.hidden = !upload.canRetryProcessing;
        retry.hidden = true;
        return state.state;
      }
      if (state.state === "ready") {
        progress.hidden = true;
        uploadRetry.hidden = true;
        return state.state;
      }
      setProgress("Processing original upload…", state.progress);
      return state.state;
    }

    async function waitForUploadReady(signal) {
      let detail = await getUploadDetailApi(backendId, signal);
      while (!signal.aborted) {
        const state = showUploadState(detail);
        if (state === "failed") return null;
        if (state === "ready") return detail;
        await waitFor(1500, signal);
        detail = await getUploadDetailApi(backendId, signal);
      }
      return null;
    }

    let documentLoaded = false;
    function selectView(view) {
      documentPanel.hidden = view !== "document";
      original.hidden = view !== "original";
      textPanel.hidden = view !== "text";
      content.querySelectorAll("[data-preview-view]").forEach((item) => {
        const active = item.dataset.previewView === view;
        item.setAttribute("aria-pressed", String(active));
        item.classList.toggle("btn-primary", active);
        item.classList.toggle("btn-secondary", !active);
      });
      if (view === "document" && !documentLoaded) void showDocument();
    }
    content.querySelectorAll("[data-preview-view]").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.previewView)));

    async function showDocument() {
      documentLoaded = true;
      controller?.abort();
      const currentController = new AbortController();
      controller = currentController;
      const signal = currentController.signal;
      const level = detailSelect.value;
      downloads.hidden = true;
      retry.hidden = true;
      uploadRetry.hidden = true;
      progress.hidden = true;
      body.innerHTML = "";
      status.textContent = "Loading study document…";
      try {
        let document = await getUploadDocumentApi(backendId, level, signal);
        if (document.status === "not_created") {
          const detail = await waitForUploadReady(signal);
          if (!detail || signal.aborted) return;
          upload.content = detail.extracted_text;
          setTextPreview(textPanel, upload.content || "No readable source text is available.", citation);
          document = await createUploadDocumentApi(backendId, level, signal);
        }
        while (document.status === "generating") {
          setProgress(`Preparing ${level} explanation and saved PDF…`, document.progress);
          await waitFor(1500, signal);
          signal.throwIfAborted();
          document = await getUploadDocumentApi(backendId, level, signal);
        }
        if (document.status !== "ready") throw new Error("The study document could not be generated. Retry it; the original upload is still available.");
        if (!dialog.open || signal.aborted) return;
        progress.hidden = true;
        status.textContent = `${level[0].toUpperCase() + level.slice(1)} document saved. The PDF includes the original extracted text.`;
        downloads.hidden = false;
        body.innerHTML = uploadDocumentSectionsHtml(document.content?.sections, upload.name);
        void typesetMath(body);
      } catch (error) {
        if (signal.aborted || !dialog.open) return;
        if (error.code === "upload_not_ready") {
          documentLoaded = false;
          await showDocument();
          return;
        }
        status.textContent = uploadDocumentErrorMessage(error);
        progress.hidden = true;
        retry.hidden = error.status === 404 || error.status === 403;
      }
    }
    detailSelect.addEventListener("change", showDocument);
    retry.addEventListener("click", async () => {
      try {
        await createUploadDocumentApi(backendId, detailSelect.value);
        if (dialog.open) await showDocument();
      } catch (error) {
        status.textContent = uploadDocumentErrorMessage(error);
        retry.hidden = error.status === 404 || error.status === 403 || error.code === "upload_not_ready";
      }
    });
    uploadRetry.addEventListener("click", async () => {
      if (uploadRetry.disabled) return;
      uploadRetry.disabled = true;
      status.textContent = "Retrying original upload processing…";
      try {
        await retryUploadApi(backendId);
        if (dialog.open) await showDocument();
      } catch (error) {
        status.textContent = uploadDocumentErrorMessage(error);
        uploadRetry.hidden = error.status === 404 || error.status === 403;
      } finally {
        uploadRetry.disabled = false;
      }
    });
    content.querySelectorAll("[data-document-download]").forEach((button) => button.addEventListener("click", async () => {
      if (downloading) return;
      downloading = true;
      const level = detailSelect.value;
      const buttons = content.querySelectorAll("[data-document-download]");
      buttons.forEach((item) => { item.disabled = true; });
      try {
        const { blob, filename } = await downloadUploadDocumentApi(backendId, button.dataset.documentDownload, level);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = filename;
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { toast(error.message, { tone: "error" }); }
      finally { downloading = false; buttons.forEach((item) => { item.disabled = false; }); }
    }));
    selectView(initialView);
    const file = upload.file || await getUploadedFile(upload.id).catch(() => null);
    if (!dialog.open) return;
    if (file) renderFilePreview(dialog, file, citation);
    else if (upload.fileUrl) {
      const extension = extensionOf(upload.originalFilename || upload.name);
      const url = escapeHtml(extension === "pdf" ? citedPdfUrl(upload.fileUrl, citation) : upload.fileUrl);
      dialog.querySelector("#preview-footer").innerHTML = `<a class="btn btn-secondary" href="${url}" download>Download original</a>`;
      if (extension === "pdf") original.innerHTML = `<iframe class="document-frame" src="${url}" title="${escapeHtml(upload.name)}"></iframe>`;
      else if (["png", "jpg", "jpeg"].includes(extension)) original.innerHTML = `<img class="image-preview" src="${url}" alt="${escapeHtml(upload.name)}" />`;
      else if (["mp4", "webm", "mov"].includes(extension)) original.innerHTML = `<video class="media-preview" src="${url}" controls playsinline></video>`;
      else if (["mp3", "m4a", "wav"].includes(extension)) original.innerHTML = `<audio class="audio-preview" src="${url}" controls></audio>`;
      else setTextPreview(original, upload.content || "Download the original file to open it.");
    } else setTextPreview(original, upload.content || "No original file is available yet.");
    locateOriginal(original, citation);
    if (citation) {
      const label = document.createElement("p");
      label.className = "field-hint";
      label.textContent = `Source reference: ${formatSourceCitation(citation)}`;
      original.prepend(label);
    }
    return;
  }

  if (upload.file) {
    renderFilePreview(dialog, upload.file, citation);
    return;
  }

  if (upload.content || upload.transcript?.length) {
    const transcript = upload.transcript?.map(({ at, text }) => `${at} - ${text}`).join("\n") || "";
    setTextPreview(content, [upload.content, transcript && `Transcript excerpts\n${transcript}`].filter(Boolean).join("\n\n"));
    return;
  }

  try {
    const file = await getUploadedFile(upload.id);
    if (!dialog.open) return;
    if (!file) {
      setTextPreview(content, "The file contents are no longer available in this browser. Remove this entry and upload the file again to preview it.");
      return;
    }
    renderFilePreview(dialog, file, citation);
  } catch {
    if (dialog.open) setTextPreview(content, "The saved file could not be opened. Try uploading it again.");
  }
}
