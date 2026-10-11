// Show a class upload on the tutor stage instead of a shared screen.
// Each page is drawn into a canvas whose captureStream() stands in for the
// display stream, so frame sampling, the preview, and annotation validation
// work exactly as they do for screen sharing. Pages live only in memory.
import { getUploadDetailApi } from "./api.js";
import { getBackendUploadId } from "./course-sync.js";
import { getUploadedFile } from "./file-store.js";

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "gif"];
const MEDIA_EXTENSIONS = ["mp4", "webm", "mov", "mp3", "m4a", "wav"];
const MAX_PAGE_DIMENSION = 1280;
const MAX_PDF_PAGES = 300;
const TEXT_PAGE = { width: 1000, height: 1294, margin: 64, font: 24, line: 36 };
const SLIDE_PAGE = { width: 1280, height: 720, margin: 72, font: 30, line: 44 };
const INK = "#1d2a33";
const PAPER = "#ffffff";

function extensionOf(name = "") {
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return match ? match[1].toLowerCase() : "";
}

/** How an upload can be shown: "pdf", "image", "slides", "text", or null for audio/video. */
export function materialType(upload) {
  const extension = extensionOf(upload.originalFilename || upload.name);
  if (extension === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  if (extension === "pptx") return "slides";
  if (MEDIA_EXTENSIONS.includes(extension)) return null;
  if (/lecture|video|audio/i.test(String(upload.kind || ""))) return null;
  return "text";
}

/** Same-origin path for a signed backend file URL, so the browser fetches it with page cookies. */
function sameOriginPath(url) {
  const parsed = new URL(url, window.location.origin);
  return `${parsed.pathname}${parsed.search}`;
}

async function loadSource(upload) {
  const file = upload.file || await getUploadedFile(upload.id).catch(() => null);
  if (file) return { file };

  const backendId = getBackendUploadId(upload);
  const detail = backendId ? await getUploadDetailApi(backendId).catch(() => null) : null;
  if (detail?.file_url) {
    const response = await fetch(sameOriginPath(detail.file_url), { credentials: "same-origin" });
    if (response.ok) {
      const blob = await response.blob();
      return { file: new File([blob], detail.original_filename || upload.name, { type: blob.type }) };
    }
  }
  const text = upload.content || detail?.extracted_text || "";
  if (text.trim()) return { text };
  throw new Error(`${upload.name || "This material"} isn't available in this browser. Upload it again in the Library tab.`);
}

/** Split text into pages of wrapped lines that fit the page layout. */
function paginate(context, text, layout) {
  const width = layout.width - layout.margin * 2;
  const linesPerPage = Math.max(1, Math.floor((layout.height - layout.margin * 2 - layout.line) / layout.line));
  const lines = [];
  for (const paragraph of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (!paragraph.trim()) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      const next = current ? `${current} ${word}` : word;
      if (context.measureText(next).width <= width || !current) current = next;
      else {
        lines.push(current);
        current = word;
      }
    }
    lines.push(current);
  }
  const pages = [];
  for (let index = 0; index < lines.length; index += linesPerPage) pages.push(lines.slice(index, index + linesPerPage));
  return pages.length ? pages : [[""]];
}

let pdfJsReady = null;

/**
 * Load pdf.js only when a PDF is opened. The worker is bundled by Vite as a plain
 * .js chunk: some servers send .mjs as text/plain, which browsers refuse to run.
 */
function loadPdfJs() {
  pdfJsReady ||= Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?worker")
  ]).then(([pdfjs, { default: PdfWorker }]) => {
    pdfjs.GlobalWorkerOptions.workerPort = new PdfWorker();
    return pdfjs;
  }).catch((error) => {
    pdfJsReady = null;
    throw error;
  });
  return pdfJsReady;
}

function textFont(layout) {
  return `${layout.font}px system-ui, -apple-system, "Segoe UI", sans-serif`;
}

export class MaterialStage {
  constructor(upload) {
    this.upload = upload;
    this.name = upload.name || "Material";
    this.canvas = document.createElement("canvas");
    this.context = this.canvas.getContext("2d");
    this.stream = null;
    this.pageIndex = 0;
    this.pageCount = 1;
    this.pdf = null;
    this.image = null;
    this.textPages = null;
    this.layout = TEXT_PAGE;
    this.renderId = 0;
  }

  /** Load the upload and draw its first page. Resolves to a MediaStream for the preview. */
  async open() {
    const type = materialType(this.upload);
    if (!type) throw new Error("Audio and video can't be shown on the stage. Pick a document, slides, or an image.");
    const source = await loadSource(this.upload);

    if (source.file && type === "pdf") {
      const pdfjs = await loadPdfJs();
      this.pdf = await pdfjs.getDocument({ data: await source.file.arrayBuffer(), isEvalSupported: false }).promise;
      this.pageCount = Math.min(this.pdf.numPages, MAX_PDF_PAGES);
    } else if (source.file && type === "image") {
      this.image = await createImageBitmap(source.file);
    } else {
      let text = source.text || "";
      if (source.file) {
        const extension = extensionOf(source.file.name);
        if (extension === "pptx" || extension === "docx") {
          const { readPowerPoint, readWordDocument } = await import("./file-preview.js");
          text = extension === "pptx" ? await readPowerPoint(source.file) : await readWordDocument(source.file);
        } else {
          text = await source.file.slice(0, 2 * 1024 * 1024).text();
        }
      }
      if (!text.trim()) throw new Error(`No readable text was found in ${this.name}.`);
      this.layout = type === "slides" ? SLIDE_PAGE : TEXT_PAGE;
      this.context.font = textFont(this.layout);
      const chunks = type === "slides" ? text.split(/\n\n(?=Slide \d+\n)/) : [text];
      this.textPages = chunks.flatMap((chunk) => paginate(this.context, chunk, this.layout));
      this.pageCount = this.textPages.length;
    }

    this.stream = this.canvas.captureStream();
    await this.showPage(0);
    return this.stream;
  }

  async showPage(index) {
    const pageIndex = Math.max(0, Math.min(this.pageCount - 1, index));
    const renderId = ++this.renderId;
    this.pageIndex = pageIndex;

    if (this.pdf) {
      const page = await this.pdf.getPage(pageIndex + 1);
      if (renderId !== this.renderId) return;
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: MAX_PAGE_DIMENSION / Math.max(base.width, base.height) });
      // Render off to the side, then copy, so the stream never shows a half-drawn page.
      const scratch = document.createElement("canvas");
      scratch.width = Math.round(viewport.width);
      scratch.height = Math.round(viewport.height);
      const scratchContext = scratch.getContext("2d");
      scratchContext.fillStyle = PAPER;
      scratchContext.fillRect(0, 0, scratch.width, scratch.height);
      await page.render({ canvasContext: scratchContext, viewport }).promise;
      page.cleanup();
      if (renderId !== this.renderId) return;
      this.paint(scratch.width, scratch.height, (context) => context.drawImage(scratch, 0, 0));
      scratch.width = 0;
    } else if (this.image) {
      const scale = Math.min(1, MAX_PAGE_DIMENSION / Math.max(this.image.width, this.image.height));
      const width = Math.round(this.image.width * scale);
      const height = Math.round(this.image.height * scale);
      this.paint(width, height, (context) => context.drawImage(this.image, 0, 0, width, height));
    } else {
      const layout = this.layout;
      const lines = this.textPages[pageIndex];
      this.paint(layout.width, layout.height, (context) => {
        context.font = textFont(layout);
        context.fillStyle = INK;
        context.textBaseline = "top";
        lines.forEach((line, row) => context.fillText(line, layout.margin, layout.margin + row * layout.line));
        context.font = `${Math.round(layout.font * 0.7)}px system-ui, sans-serif`;
        context.globalAlpha = 0.55;
        context.fillText(`${this.name} · page ${pageIndex + 1} of ${this.pageCount}`, layout.margin, layout.height - layout.margin * 0.75);
        context.globalAlpha = 1;
      });
    }
  }

  paint(width, height, draw) {
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    this.context.fillStyle = PAPER;
    this.context.fillRect(0, 0, width, height);
    draw(this.context);
    this.stream?.getVideoTracks()[0]?.requestFrame?.();
  }

  close() {
    this.renderId += 1;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    void this.pdf?.destroy();
    this.pdf = null;
    this.image?.close();
    this.image = null;
    this.textPages = null;
    this.canvas.width = 0;
    this.canvas.height = 0;
  }
}
