import JSZip from "jszip";
import { getUploadedFile } from "./file-store.js";
import { escapeHtml, formatBytes } from "./ui.js";

const MAX_TEXT_PREVIEW = 2 * 1024 * 1024;

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

function setTextPreview(container, text) {
  container.innerHTML = `<pre class="document-text">${escapeHtml(text)}</pre>`;
}

async function readPowerPoint(file) {
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

async function readWordDocument(file) {
  const archive = await JSZip.loadAsync(file);
  const documentFile = archive.file("word/document.xml");
  if (!documentFile) return "";
  const xml = new DOMParser().parseFromString(await documentFile.async("text"), "application/xml");
  return [...xml.getElementsByTagNameNS("*", "p")].map((paragraph) =>
    [...paragraph.getElementsByTagNameNS("*", "t")].map((node) => node.textContent).join("").trim()
  ).filter(Boolean).join("\n\n");
}

function renderFilePreview(dialog, file) {
  const content = dialog.querySelector("#preview-content");
  const footer = dialog.querySelector("#preview-footer");
  const extension = extensionOf(file.name);
  const url = URL.createObjectURL(file);
  dialog.addEventListener("close", () => URL.revokeObjectURL(url), { once: true });
  footer.innerHTML = `<a class="btn btn-secondary" href="${url}" download="${escapeHtml(file.name)}">Download original</a>`;

  if (extension === "pdf") {
    content.innerHTML = `<iframe class="document-frame" src="${url}" title="${escapeHtml(file.name)}"></iframe>`;
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
}

export async function openUploadPreview(upload) {
  const dialog = makeDialog(upload);
  const content = dialog.querySelector("#preview-content");

  if (upload.file) {
    renderFilePreview(dialog, upload.file);
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
    renderFilePreview(dialog, file);
  } catch {
    if (dialog.open) setTextPreview(content, "The saved file could not be opened. Try uploading it again.");
  }
}
