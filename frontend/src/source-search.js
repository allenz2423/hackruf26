import { escapeHtml } from "./ui.js";

const SNIPPET_LIMIT = 600;

function valueText(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

export function sourceHierarchyText(source) {
  const hierarchy = source.hierarchy_path ?? source.heading_path ?? source.hierarchy ?? source.section_path ?? [];
  if (Array.isArray(hierarchy)) {
    return hierarchy.map((part) => {
      if (typeof part === "string") return part;
      return part?.title || part?.heading || part?.name || part?.label || "";
    }).filter(Boolean).join(" › ");
  }
  if (hierarchy && typeof hierarchy === "object") {
    return ["class", "material", "section", "subsection", "chunk"].map((key) => {
      const part = hierarchy[key];
      if (typeof part === "string") return part;
      return part?.title || part?.heading || part?.name || part?.label || "";
    }).filter(Boolean).join(" › ");
  }
  return valueText(hierarchy);
}

export function sourceLocatorText(source) {
  const locator = source.locator || {};
  const page = locator.page ?? source.page;
  const slide = locator.slide ?? source.slide;
  const timestamp = locator.timestamp_seconds ?? locator.timestamp_start_seconds ?? locator.timestamp ?? source.timestamp_seconds;
  const timestampEnd = locator.timestamp_end_seconds ?? source.timestamp_end_seconds;
  const parts = [];
  if (page != null) parts.push("Page " + page);
  if (slide != null) parts.push("Slide " + slide);
  if (timestamp != null && Number.isFinite(Number(timestamp))) {
    const formatTime = (value) => {
      const seconds = Math.max(0, Math.floor(Number(value)));
      return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    };
    const start = formatTime(timestamp);
    const end = timestampEnd != null && Number.isFinite(Number(timestampEnd)) ? formatTime(timestampEnd) : null;
    parts.push(end && end !== start ? `${start}–${end}` : start);
  }
  if (locator.paragraph != null || source.paragraph_order != null) {
    parts.push("Paragraph " + (locator.paragraph ?? source.paragraph_order));
  }
  const blockStart = locator.block_order_start ?? source.block_order_start;
  const blockEnd = locator.block_order_end ?? source.block_order_end;
  if (blockStart != null) {
    parts.push(blockEnd != null && blockEnd !== blockStart
      ? `Text blocks ${blockStart}–${blockEnd}`
      : `Text block ${blockStart}`);
  }
  return parts.join(" · ") || "Source location unavailable";
}

export function sourceCitationFromChunk(chunk) {
  const materialId = valueText(chunk?.material_id);
  if (!materialId) return null;
  const locator = chunk.locator || {};
  const citation = {
    upload_id: materialId,
    material_id: materialId,
    name: valueText(chunk.title || chunk.material_title || "Course material")
  };
  const page = locator.page ?? chunk.page;
  const slide = locator.slide ?? chunk.slide;
  const timestamp = locator.timestamp_seconds ?? locator.timestamp_start_seconds ?? locator.timestamp ?? chunk.timestamp_seconds;
  const timestampEnd = locator.timestamp_end_seconds ?? chunk.timestamp_end_seconds;
  const paragraph = locator.paragraph ?? chunk.paragraph_order;
  if (page != null && Number.isInteger(Number(page)) && Number(page) > 0) citation.page = Number(page);
  if (slide != null && Number.isInteger(Number(slide)) && Number(slide) > 0) citation.slide = Number(slide);
  if (timestamp != null && Number.isFinite(Number(timestamp)) && Number(timestamp) >= 0) {
    citation.timestamp_seconds = Number(timestamp);
  }
  if (timestampEnd != null && Number.isFinite(Number(timestampEnd)) && Number(timestampEnd) >= 0) {
    citation.timestamp_end_seconds = Number(timestampEnd);
  }
  if (paragraph != null) citation.paragraph = paragraph;
  if (locator.block_order_start != null) {
    citation.block_order_start = locator.block_order_start;
    if (locator.block_order_end != null) citation.block_order_end = locator.block_order_end;
  }
  return citation;
}

export function sourceSearchHitMarkup(source) {
  const chunkRevisionId = valueText(source.chunk_revision_id);
  if (!chunkRevisionId) return "";
  const title = source.title || source.material_title || "Course material";
  const rawSnippet = String(source.snippet || "");
  const snippet = rawSnippet.slice(0, SNIPPET_LIMIT);
  const hierarchy = sourceHierarchyText(source);
  const revision = source.revision_id || source.content_revision_id || "Revision unavailable";
  const sectionId = source.section_id || "—";
  const chunkId = source.chunk_id || "—";
  const order = source.order ?? source.chunk_order;
  return '<li class="source-search-hit">' +
    '<button type="button" class="source-search-hit-button" data-source-chunk="' + escapeHtml(chunkRevisionId) + '" aria-controls="source-chunk-detail" aria-expanded="false">' +
      '<span class="source-search-hit-heading"><strong>' + escapeHtml(title) + '</strong><span class="row-meta">' + escapeHtml(sourceLocatorText(source)) + '</span></span>' +
      (hierarchy ? '<span class="source-search-hierarchy">' + escapeHtml(hierarchy) + '</span>' : '') +
      '<span class="source-search-snippet">' + escapeHtml(snippet) + (rawSnippet.length > SNIPPET_LIMIT ? "…" : "") + '</span>' +
      '<span class="row-meta">Section ' + escapeHtml(sectionId) + ' · Chunk ' + escapeHtml(chunkId) +
      (order != null ? ' · Order ' + escapeHtml(order) : '') + ' · Revision ' + escapeHtml(revision) + '</span>' +
    '</button></li>';
}

export function sourceChunkMarkup(chunk) {
  const title = chunk.title || chunk.material_title || "Course material";
  const hierarchy = sourceHierarchyText(chunk);
  const revision = chunk.revision_id || chunk.content_revision_id || "Revision unavailable";
  const chunkRevision = chunk.chunk_revision_id || "Revision unavailable";
  const contentHash = chunk.content_sha256 || chunk.content_hash || chunk.sha256 || "Unavailable";
  const method = chunk.extraction_method || "Not specified";
  const order = chunk.order ?? chunk.chunk_order;
  const text = typeof chunk.text === "string" ? chunk.text : "";
  const location = sourceLocatorText(chunk);
  const citation = sourceCitationFromChunk(chunk);
  const citationData = citation ? escapeHtml(JSON.stringify(citation)) : "";
  return '<section class="source-chunk-detail-card" aria-labelledby="source-chunk-title">' +
    '<div class="source-chunk-heading"><div><p class="eyebrow">Exact indexed source</p><h3 id="source-chunk-title">' + escapeHtml(title) + '</h3></div>' +
    '<button type="button" class="btn btn-ghost btn-small" data-close-source-chunk>Close</button></div>' +
    (hierarchy ? '<p class="source-search-hierarchy">' + escapeHtml(hierarchy) + '</p>' : '') +
    (citation ? '<div class="source-chunk-actions"><button type="button" class="btn btn-secondary btn-small" data-open-original-source data-source-citation="' + citationData + '" aria-label="Open original source at ' + escapeHtml(location) + '">Open original source</button></div>' : '') +
    '<dl class="source-chunk-metadata">' +
      '<div><dt>Location</dt><dd>' + escapeHtml(location) + '</dd></div>' +
      '<div><dt>Material</dt><dd>' + escapeHtml(chunk.material_id || "Unavailable") + '</dd></div>' +
      '<div><dt>Revision</dt><dd>' + escapeHtml(revision) + '</dd></div>' +
      '<div><dt>Chunk revision</dt><dd>' + escapeHtml(chunkRevision) + '</dd></div>' +
      (order != null ? '<div><dt>Index order</dt><dd>' + escapeHtml(order) + '</dd></div>' : '') +
      '<div><dt>Extraction</dt><dd>' + escapeHtml(method) + '</dd></div>' +
      '<div><dt>Content hash</dt><dd><code>' + escapeHtml(contentHash) + '</code></dd></div>' +
    '</dl><pre class="source-chunk-text">' + escapeHtml(text) + '</pre></section>';
}
