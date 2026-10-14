import { escapeHtml, toast } from "./ui.js";
import { getClassById } from "./classes.js";
import { getBackendUploadId } from "./course-sync.js";
import { getUploadDetailApi } from "./api.js";

export function formatSourceCitation(source) {
  if (!source) return "";
  if (typeof source === "string") return source;
  const parts = [];
  if (source.name || source.title || source.filename) parts.push(source.name || source.title || source.filename);
  if (source.page != null) parts.push(`p. ${source.page}`);
  if (source.slide != null) parts.push(`slide ${source.slide}`);
  if (source.timestamp_seconds != null) {
    const formatTime = (value) => {
      const minutes = Math.floor(value / 60);
      const seconds = Math.floor(value % 60).toString().padStart(2, "0");
      return `${minutes}:${seconds}`;
    };
    const start = formatTime(source.timestamp_seconds);
    const end = source.timestamp_end_seconds != null && Number.isFinite(Number(source.timestamp_end_seconds))
      ? formatTime(Number(source.timestamp_end_seconds))
      : null;
    parts.push(end && end !== start ? `${start}–${end}` : start);
  }
  if (source.paragraph != null) parts.push(`paragraph ${source.paragraph}`);
  if (source.block_order_start != null) {
    parts.push(source.block_order_end != null && source.block_order_end !== source.block_order_start
      ? `text blocks ${source.block_order_start}–${source.block_order_end}`
      : `text block ${source.block_order_start}`);
  }
  return parts.join(" · ") || (source.upload_id ? `Source ${String(source.upload_id).slice(0, 8)}` : "Source reference");
}

export function sourceCitationHtml(source) {
  const label = escapeHtml(formatSourceCitation(source));
  if (!label) return "";
  if (!source?.upload_id) return `<span class="chip source-chip">${label}</span>`;
  return `<button type="button" class="chip source-chip" data-source-citation="${escapeHtml(JSON.stringify(source))}" title="Open source">${label}</button>`;
}

export function sourceWarningsHtml(material) {
  const messages = {
    changed: "Some source uploads have changed since this study set was generated.",
    missing: "Some source uploads were deleted. This saved study set and its scores are still available.",
    truncated: "Large sources were excerpted for generation; this set may not cover every page."
  };
  return (material.sourceWarnings || []).map((warning) => messages[warning]
    ? `<p class="field-hint" role="note">${messages[warning]}</p>` : "").join("");
}

export function bindSourceCitations(root, material) {
  root.querySelectorAll("[data-source-citation]").forEach((button) => {
    button.addEventListener("keydown", (event) => event.stopPropagation());
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      button.disabled = true;
      try {
        const citation = JSON.parse(button.dataset.sourceCitation);
        const { openUploadPreview } = await import("./file-preview.js");
        const local = getClassById(material.classId)?.uploads.find((upload) =>
          getBackendUploadId(upload) === citation.upload_id || upload.id === citation.upload_id);
        const detail = await getUploadDetailApi(citation.upload_id);
        const upload = {
          ...local,
          id: local?.id || detail.id,
          backendId: detail.id,
          name: detail.title,
          kind: detail.kind,
          size: detail.file_size_bytes,
          content: detail.extracted_text,
          fileUrl: detail.file_url,
          originalFilename: detail.original_filename,
          status: detail.status
        };
        await openUploadPreview(upload, { citation, initialView: "original" });
      } catch {
        toast("This source could not be opened. It may have been deleted; try the Library tab.", { tone: "error" });
      } finally {
        button.disabled = false;
      }
    });
  });
}
