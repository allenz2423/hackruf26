import {
  addClassConceptAliasApi,
  createClassConceptApi,
  createClassConceptEdgeApi,
  decideClassConceptAliasApi,
  decideClassConceptApi,
  decideClassConceptEdgeApi,
  deleteClassConceptAliasApi,
  deleteClassConceptEdgeApi,
  getClassConceptExtractionApi,
  getClassConceptGraphApi,
  getClassConceptMergeHistoryApi,
  getClassConceptGraphReviewApi,
  getUploadsApi,
  mergeClassConceptApi,
  startClassConceptExtractionApi,
  updateClassConceptApi,
  getClassSourceChunkApi,
  getUploadDetailApi
} from "./api.js";
import { classHasCapability } from "./classes.js";
import { getBackendClassId } from "./course-sync.js";
import { sourceCitationFromChunk } from "./source-search.js";
import { escapeHtml, openDialog } from "./ui.js";

const EDGE_TYPES = [
  ["part_of", "Part of"],
  ["prerequisite_for", "Prerequisite for"],
  ["related_to", "Related to"],
  ["applied_in", "Applied in"]
];
const PENDING_STATUSES = new Set(["pending", "queued", "processing", "running"]);
const POLL_INTERVAL_MS = 2500;
const MAX_POLL_COUNT = 180;

function list(value) {
  return Array.isArray(value) ? value : [];
}

function displayName(concept) {
  return concept?.canonical_name || concept?.name || "Unnamed concept";
}

function sourceLocator(source) {
  const locator = source?.locator || {};
  const parts = [];
  if (locator.page != null) parts.push(`page ${locator.page}`);
  if (locator.slide != null) parts.push(`slide ${locator.slide}`);
  const timestamp = locator.timestamp_seconds ?? locator.timestamp_start_seconds;
  if (timestamp != null && Number.isFinite(Number(timestamp))) {
    const seconds = Math.max(0, Math.floor(Number(timestamp)));
    const start = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    const endValue = locator.timestamp_end_seconds;
    if (endValue != null && Number.isFinite(Number(endValue))) {
      const endSeconds = Math.max(0, Math.floor(Number(endValue)));
      const end = `${Math.floor(endSeconds / 60)}:${String(endSeconds % 60).padStart(2, "0")}`;
      parts.push(end === start ? start : `${start}–${end}`);
    } else parts.push(start);
  }
  if (locator.paragraph != null) parts.push(`paragraph ${locator.paragraph}`);
  if (locator.block_order_start != null) {
    parts.push(locator.block_order_end != null && locator.block_order_end !== locator.block_order_start
      ? `text blocks ${locator.block_order_start}–${locator.block_order_end}`
      : `text block ${locator.block_order_start}`);
  }
  return parts.join(" · ") || "Location unavailable";
}

function sourceReferenceMarkup(source, { excerpt = false } = {}) {
  const materialId = source?.material_id;
  const chunkRevisionId = source?.chunk_revision_id;
  const label = `${source?.title || "Course source"} · ${sourceLocator(source)}`;
  const excerptText = source?.snippet || source?.excerpt || source?.bounded_excerpt || "";
  const boundedExcerpt = typeof excerptText === "string" ? excerptText.slice(0, 600) : "";
  return `<li class="concept-source-reference">
    ${materialId && chunkRevisionId
      ? `<button class="concept-source-link" type="button" data-graph-source="${escapeHtml(chunkRevisionId)}" aria-label="Open ${escapeHtml(label)}">${escapeHtml(label)}</button>`
      : `<span>${escapeHtml(label)}</span>`}
    ${excerpt && boundedExcerpt ? `<blockquote>${escapeHtml(boundedExcerpt)}${excerptText.length > 600 ? "…" : ""}</blockquote>` : ""}
  </li>`;
}

function sourceListMarkup(sources, options = {}) {
  const items = list(sources);
  if (!items.length) return `<p class="field-hint">No source references are attached.</p>`;
  return `<ul class="concept-source-list">${items.map((source) => sourceReferenceMarkup(source, options)).join("")}</ul>`;
}

function aliasesMarkup(concept, canManage) {
  const aliases = list(concept.aliases).filter((alias) => alias.status !== "proposed");
  return `<div class="concept-aliases">
    <h4>Aliases</h4>
    ${aliases.length
      ? `<ul>${aliases.map((alias) => `<li><span>${escapeHtml(alias.name)} <small>${escapeHtml(alias.language || "und")}</small></span>${canManage && alias.id && concept.version != null ? `<button class="btn btn-ghost btn-small" type="button" data-delete-alias="${escapeHtml(alias.id)}" data-concept-id="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version)}" aria-label="Remove alias ${escapeHtml(alias.name)}">Remove</button>` : ""}</li>`).join("")}</ul>`
      : `<p class="field-hint">No aliases added.</p>`}
    ${canManage ? `<form class="concept-alias-form" data-add-alias="${escapeHtml(concept.id)}">
      <label class="field"><span>Add alias</span><input name="name" maxlength="160" required /></label>
      <label class="field"><span>Language tag</span><input name="language" maxlength="24" value="und" /></label>
      <button class="btn btn-secondary btn-small" type="submit">Add alias</button>
    </form>` : ""}
  </div>`;
}

function conceptCardMarkup(concept, { concepts, canManage, proposal = false } = {}) {
  const parent = list(concepts).find((item) => String(item.id) === String(concept.parent_concept_id));
  const sources = sourceListMarkup(concept.sources, { excerpt: proposal });
  const aliases = aliasesMarkup(concept, canManage && !proposal);
  const status = concept.state || concept.status || (proposal ? "proposed" : "active");
  const parentChoices = list(concepts).filter((item) => String(item.id) !== String(concept.id));
  return `<article class="concept-card ${proposal ? "concept-card--proposal" : ""}">
    <div class="concept-card-heading">
      <div><span class="permission-badge">${escapeHtml(status)}</span><h3>${escapeHtml(displayName(concept))}</h3></div>
      ${concept.version != null ? `<span class="row-meta">Version ${escapeHtml(concept.version)}</span>` : ""}
    </div>
    ${concept.description ? `<p>${escapeHtml(concept.description)}</p>` : `<p class="muted">No description yet.</p>`}
    ${parent ? `<p class="row-meta">Topic: ${escapeHtml(displayName(parent))}</p>` : ""}
    ${aliases}
    <details class="concept-sources"><summary>Sources (${list(concept.sources).length})</summary>${sources}</details>
    ${proposal && canManage ? `<div class="concept-review-actions">
      <button class="btn btn-primary btn-small" type="button" data-concept-decision="approve" data-concept-id="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version ?? "")}">Approve concept</button>
      <button class="btn btn-secondary btn-small" type="button" data-concept-decision="reject" data-concept-id="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version ?? "")}">Reject</button>
    </div>` : ""}
    ${canManage && !proposal ? `<details class="concept-edit-details"><summary class="btn btn-ghost btn-small">Edit concept</summary>
      <form class="concept-edit-form" data-edit-concept="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version ?? "")}">
        <label class="field"><span>Canonical name</span><input name="canonical_name" maxlength="200" required value="${escapeHtml(displayName(concept))}" /></label>
        <label class="field"><span>Assessable description</span><textarea name="description" maxlength="2000" rows="3">${escapeHtml(concept.description || "")}</textarea></label>
        <label class="field"><span>Parent topic</span><select name="parent_concept_id"><option value="">No parent topic</option>${parentChoices.map((item) => `<option value="${escapeHtml(item.id)}" ${String(item.id) === String(concept.parent_concept_id) ? "selected" : ""}>${escapeHtml(displayName(item))}</option>`).join("")}</select></label>
        <button class="btn btn-secondary btn-small" type="submit">Save concept</button>
      </form>
    </details>
    ${(concept.state || concept.status) === "validated" ? `<button class="btn btn-ghost btn-small" type="button" data-concept-decision="deprecate" data-concept-id="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version ?? "")}">Deprecate concept</button>` : ""}
    ${parentChoices.length ? `<details class="concept-merge-details"><summary>Merge into another concept</summary>
      <form class="concept-merge-form" data-merge-concept="${escapeHtml(concept.id)}" data-version="${escapeHtml(concept.version ?? "")}">
        <label class="field"><span>Keep this concept and merge into</span><select name="target_concept_id" required><option value="">Choose target</option>${parentChoices.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(displayName(item))}</option>`).join("")}</select></label>
        <button class="btn btn-secondary btn-small" type="submit">Review merge</button>
      </form>
    </details>` : ""}` : ""}
  </article>`;
}

function edgeLabel(edge) {
  return EDGE_TYPES.find(([key]) => key === edge.type)?.[1] || edge.type || "Relationship";
}

function edgeCardMarkup(edge, concepts, canManage, { proposal = false } = {}) {
  const from = concepts.find((item) => String(item.id) === String(edge.from_concept_id));
  const to = concepts.find((item) => String(item.id) === String(edge.to_concept_id));
  const fromName = from ? displayName(from) : "Unknown concept";
  const toName = to ? displayName(to) : "Unknown concept";
  const necessity = edge.necessity ? ` · ${edge.necessity}` : "";
  const oppositePrerequisite = list(edge.review_flags).includes("opposite_prerequisite");
  return `<article class="concept-edge-card ${proposal ? "concept-edge-card--proposal" : ""}">
    <div class="concept-edge-route" aria-label="Relationship from ${escapeHtml(fromName)} to ${escapeHtml(toName)}"><strong>${escapeHtml(fromName)}</strong><span aria-hidden="true">→</span><strong>${escapeHtml(toName)}</strong></div>
    <p><span class="permission-badge">${escapeHtml(edgeLabel(edge))}${escapeHtml(necessity)}</span> <span class="row-meta">${escapeHtml(edge.provenance || "provenance unavailable")}</span></p>
    ${oppositePrerequisite ? `<p class="concept-review-warning" role="note"><strong>Check relationship direction.</strong> Source evidence also suggests the opposite prerequisite direction. Review the cited material before approving this relationship.</p>` : ""}
    ${edge.explanation ? `<p>${escapeHtml(edge.explanation)}</p>` : ""}
    <details class="concept-sources"><summary>Evidence (${list(edge.sources).length})</summary>${sourceListMarkup(edge.sources, { excerpt: proposal })}</details>
    ${proposal && canManage ? `<div class="concept-review-actions">
      <button class="btn btn-primary btn-small" type="button" data-edge-decision="approve" data-edge-id="${escapeHtml(edge.id)}" data-version="${escapeHtml(edge.version ?? "")}">Approve edge</button>
      <button class="btn btn-secondary btn-small" type="button" data-edge-decision="reject" data-edge-id="${escapeHtml(edge.id)}" data-version="${escapeHtml(edge.version ?? "")}">Reject</button>
    </div>` : canManage && edge.id && edge.version != null ? `<button class="btn btn-ghost btn-small" type="button" data-delete-edge="${escapeHtml(edge.id)}" data-version="${escapeHtml(edge.version)}" aria-label="Remove ${escapeHtml(edgeLabel(edge))} relationship">Remove relationship</button>` : ""}
  </article>`;
}

function mergeHistoryMarkup(history, error = "") {
  if (error) return `<p class="concept-graph-alert" role="alert">Merge history could not be loaded. Refresh the map to retry.</p>`;
  const items = list(history?.items);
  if (!items.length) return `<details class="concept-merge-history"><summary>Merge history</summary><p class="field-hint">No concept merges have been recorded for this class.</p></details>`;
  return `<details class="concept-merge-history"><summary>Merge history (${items.length})</summary><ol>${items.map((item) => {
    const created = typeof item.created_at === "string" && Number.isFinite(Date.parse(item.created_at))
      ? new Date(item.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Date unavailable";
    return `<li><article class="concept-merge-history-item"><p><strong>${escapeHtml(item.source_name || "Former concept")}</strong> was merged into <strong>${escapeHtml(item.target_name || "the target concept")}</strong>.</p>
      ${item.source_description ? `<p>${escapeHtml(item.source_description)}</p>` : ""}
      <p class="row-meta">Source version ${escapeHtml(item.source_version)} · ${escapeHtml(created)}</p></article></li>`;
  }).join("")}</ol></details>`;
}

function normalizeReview(review) {
  return {
    concepts: list(review?.concept_proposals || review?.proposed_concepts || review?.concepts),
    aliases: list(review?.alias_proposals || review?.proposed_aliases),
    edges: list(review?.edge_proposals || review?.proposed_edges || review?.edges),
    extractions: list(review?.extractions)
  };
}

function aliasProposalMarkup(alias) {
  return `<article class="concept-alias-proposal">
    <div class="concept-alias-proposal-heading"><div><span class="permission-badge">Proposed alias</span><h5>${escapeHtml(alias.name || "Unnamed alias")}</h5></div><span class="row-meta">${escapeHtml(alias.language || "und")}</span></div>
    <p>Suggested name for <strong>${escapeHtml(alias.concept_name || "this concept")}</strong>.</p>
    ${sourceListMarkup(alias.sources, { excerpt: true })}
    <div class="concept-review-actions">
      <button class="btn btn-primary btn-small" type="button" data-alias-decision="approve" data-concept-id="${escapeHtml(alias.concept_id)}" data-alias-id="${escapeHtml(alias.id)}" data-version="${escapeHtml(alias.concept_version ?? "")}" aria-label="Approve alias ${escapeHtml(alias.name || "")}">Approve alias</button>
      <button class="btn btn-secondary btn-small" type="button" data-alias-decision="reject" data-concept-id="${escapeHtml(alias.concept_id)}" data-alias-id="${escapeHtml(alias.id)}" data-version="${escapeHtml(alias.concept_version ?? "")}" aria-label="Reject alias ${escapeHtml(alias.name || "")}">Reject alias</button>
    </div>
  </article>`;
}

function extractionRowMarkup(upload, status) {
  const sourceReady = upload.status === "ready" && (!upload.source_index_status || upload.source_index_status === "ready");
  const indexFailed = upload.source_index_status === "failed";
  const currentStatus = status?.status || (sourceReady ? "not_started" : upload.source_index_status || upload.status || "pending");
  const pending = PENDING_STATUSES.has(currentStatus);
  const failed = currentStatus === "failed";
  const label = indexFailed ? "Source indexing failed — retry in Library"
    : pending ? "Extraction in progress" : failed ? "Extraction failed"
      : currentStatus === "completed" || currentStatus === "succeeded" ? "Extraction complete"
        : sourceReady ? "Ready to map" : `Source ${currentStatus}`;
  const operation = status?.operation_id || "";
  return `<li class="concept-extraction-row">
    <div><strong>${escapeHtml(upload.title || upload.original_filename || "Course material")}</strong><span class="row-meta">${escapeHtml(label)}</span></div>
    ${operation && pending ? `<button class="btn btn-ghost btn-small" type="button" data-check-extraction="${escapeHtml(operation)}">Check status</button>` : ""}
    <button class="btn ${failed || currentStatus === "not_started" ? "btn-secondary" : "btn-ghost"} btn-small" type="button" data-start-extraction="${escapeHtml(upload.id)}" ${!sourceReady || pending ? "disabled" : ""}>${indexFailed ? "Waiting for source index" : failed ? "Retry extraction" : "Extract concepts"}</button>
    ${status?.error_code ? `<span class="field-hint" role="note">${escapeHtml(extractionErrorText(status.error_code))}</span>` : ""}
  </li>`;
}

function extractionErrorText(code) {
  const messages = {
    source_not_ready: "This source is not ready yet. Try again after processing finishes.",
    source_revision_changed: "The source changed during extraction. Retry against its current revision.",
    provider_unavailable: "Concept extraction could not finish. You can retry it.",
    invalid_source: "This source is no longer available to this class."
  };
  return messages[code] || "Extraction failed. You can retry it.";
}

function extractionStorageKey(classId) {
  return `jot.concept-graph-extractions:${classId}`;
}

function readStoredExtractionJobs(classId) {
  try {
    const raw = sessionStorage.getItem(extractionStorageKey(classId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((job) => job?.operation_id && job?.upload_id) : [];
  } catch {
    return [];
  }
}

function storeExtractionJobs(classId, jobs) {
  try {
    const retained = list(jobs).filter((job) => job?.operation_id && job?.upload_id
      && (isPending(job.status) || job.status === "failed"));
    sessionStorage.setItem(extractionStorageKey(classId), JSON.stringify(retained.map((job) => ({
      operation_id: job.operation_id,
      upload_id: job.upload_id,
      status: job.status,
      error_code: job.error_code || null
    }))));
  } catch { /* Session storage is optional; server review/status APIs remain authoritative. */ }
}

export function conceptGraphPageMarkup({
  graph = {}, review = {}, uploads = [], extractionJobs = [], canManage = false,
  mergeHistory = {}, mergeHistoryError = "", error = "", reviewError = "", uploadError = "", statusMessage = ""
} = {}) {
  const concepts = list(graph.concepts);
  const edges = list(graph.edges);
  const proposals = normalizeReview(review);
  const jobByUpload = new Map(list(extractionJobs).map((job) => [String(job.upload_id), job]));
  const knownSources = [...concepts, ...proposals.concepts, ...proposals.edges]
    .flatMap((item) => list(item.sources))
    .concat(proposals.aliases.flatMap((item) => list(item.sources)));
  const uniqueSources = [...new Map(knownSources
    .filter((source) => source?.chunk_revision_id)
    .map((source) => [String(source.chunk_revision_id), source])).values()];
  const conceptOptions = concepts.map((concept) => `<option value="${escapeHtml(concept.id)}">${escapeHtml(displayName(concept))}</option>`).join("");
  const sourceOptions = uniqueSources.map((source) => `<option value="${escapeHtml(source.chunk_revision_id)}">${escapeHtml(source.title || "Course source")} · ${escapeHtml(sourceLocator(source))}</option>`).join("");
  return `<section class="section-stack concept-graph-page" aria-labelledby="concept-graph-title">
    <div class="concept-graph-heading">
      <div><p class="eyebrow">Shared class map</p><h2 id="concept-graph-title" tabindex="-1">Concepts and relationships</h2>
      <p class="muted">Browse source-grounded course concepts and validated relationships.</p></div>
      <div class="concept-graph-heading-actions"><span class="pill no-dot">${concepts.length} ${concepts.length === 1 ? "concept" : "concepts"}</span><button class="btn btn-secondary btn-small" type="button" data-refresh-concept-graph>Refresh map</button></div>
    </div>
    ${error ? `<p class="concept-graph-alert" role="alert">${escapeHtml(error)}</p>` : ""}
    ${reviewError ? `<p class="concept-graph-alert" role="alert">Manager review could not be loaded. Refresh to try again.</p>` : ""}
    ${uploadError && canManage ? `<p class="concept-graph-alert" role="alert">Source uploads could not be loaded. Refresh to retry extraction controls.</p>` : ""}
    <p class="concept-graph-status" role="status" aria-live="polite">${escapeHtml(statusMessage)}</p>
    ${error ? `<div class="panel concept-empty-state" role="group" aria-label="Concept map unavailable"><strong>Concept map unavailable</strong><span>Refresh the map to check your access and load the latest class concepts.</span><button class="btn btn-secondary btn-small" type="button" data-refresh-concept-graph>Refresh map</button></div>` : `
    ${!concepts.length ? `<div class="panel concept-empty-state"><strong>No validated concepts yet</strong><span>${canManage ? "Start concept extraction from a ready source or review pending proposals below." : "A class manager can add concepts after reviewing course sources."}</span></div>` : `
      <section class="panel concept-list-panel" aria-labelledby="validated-concepts-title"><div class="concept-section-heading"><div><h3 id="validated-concepts-title">Course concepts</h3><p class="muted">Descriptions, aliases, and source references for this class.</p></div></div>
        <div class="concept-card-list">${concepts.map((concept) => conceptCardMarkup(concept, { concepts, canManage })).join("")}</div>
      </section>`}
    ${edges.length ? `<section class="panel concept-list-panel" aria-labelledby="validated-edges-title"><div class="concept-section-heading"><div><h3 id="validated-edges-title">Validated relationships</h3><p class="muted">A relationship is not necessarily a required prerequisite.</p></div></div>
      <div class="concept-edge-list">${edges.map((edge) => edgeCardMarkup(edge, concepts, canManage)).join("")}</div>
    </section>` : `<section class="panel concept-empty-state"><strong>No validated relationships yet</strong><span>Only relationships reviewed and approved by a class manager appear here.</span></section>`}
    ${canManage ? `<section class="panel concept-manager-panel" aria-labelledby="concept-manager-title"><div class="concept-section-heading"><div><p class="eyebrow">Manager tools</p><h3 id="concept-manager-title">Review and maintain the map</h3></div></div>
      ${mergeHistoryMarkup(mergeHistory, mergeHistoryError)}
      ${uploadError ? "" : `<div class="concept-extraction-panel"><h4>Extract from course sources</h4><p class="muted">Extraction creates reviewable proposals. Nothing becomes part of the shared map until approved.</p>
        ${uploads.length ? `<ul class="concept-extraction-list">${uploads.filter((upload) => upload.status === "ready" || upload.source_index_status === "failed").map((upload) => extractionRowMarkup(upload, jobByUpload.get(String(upload.id)))).join("") || `<li class="muted">No ready sources yet.</li>`}</ul>` : `<p class="field-hint">No uploads are ready for concept extraction.</p>`}
      </div>`}
      <details class="concept-create-details"><summary class="btn btn-secondary">Create a concept</summary><form class="concept-create-form" id="create-class-concept">
        <label class="field"><span>Canonical name</span><input name="canonical_name" maxlength="200" required /></label>
        <label class="field"><span>Assessable description</span><textarea name="description" maxlength="2000" rows="3" required></textarea></label>
        <label class="field"><span>Parent topic (optional)</span><select name="parent_concept_id"><option value="">No parent topic</option>${conceptOptions}</select></label>
        <label class="field"><span>Supporting source chunks</span><select name="source_chunk_revision_ids" multiple size="4" ${sourceOptions ? "" : "disabled"}>${sourceOptions}</select><small class="field-hint">Select evidence from an existing concept or proposal; hold Ctrl/Command to select more than one.</small></label>
        <button class="btn btn-primary" type="submit">Create proposed concept</button>
      </form></details>
      ${reviewError ? `<p class="field-hint">The review queue is unavailable; refresh to try again.</p>` : `
        ${proposals.concepts.length ? `<section class="concept-review-section" aria-labelledby="concept-proposals-title"><h4 id="concept-proposals-title">Concept proposals</h4><div class="concept-card-list">${proposals.concepts.map((concept) => conceptCardMarkup(concept, { concepts: [...concepts, ...proposals.concepts], canManage, proposal: true })).join("")}</div></section>` : `<p class="field-hint">No concept proposals need review.</p>`}
        ${proposals.aliases.length ? `<section class="concept-review-section" aria-labelledby="alias-proposals-title"><h4 id="alias-proposals-title">Alias proposals</h4><div class="concept-alias-proposal-list">${proposals.aliases.map(aliasProposalMarkup).join("")}</div></section>` : `<p class="field-hint">No alias proposals need review.</p>`}
        ${proposals.edges.length ? `<section class="concept-review-section" aria-labelledby="edge-proposals-title"><h4 id="edge-proposals-title">Relationship proposals</h4><div class="concept-edge-list">${proposals.edges.map((edge) => edgeCardMarkup(edge, [...concepts, ...proposals.concepts], canManage, { proposal: true })).join("")}</div></section>` : `<p class="field-hint">No relationship proposals need review.</p>`}`}
      <details class="concept-create-details"><summary class="btn btn-secondary">Add a source-backed relationship</summary><form class="concept-edge-create-form" id="create-class-concept-edge">
        <label class="field"><span>From concept</span><select name="from_concept_id" required><option value="">Choose concept</option>${conceptOptions}</select></label>
        <label class="field"><span>Relationship</span><select name="type">${EDGE_TYPES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
        <label class="field"><span>To concept</span><select name="to_concept_id" required><option value="">Choose concept</option>${conceptOptions}</select></label>
        <label class="field"><span>Prerequisite importance</span><select name="necessity"><option value="">Not a prerequisite</option><option value="essential">Essential</option><option value="helpful">Helpful</option></select></label>
        <label class="field"><span>Evidence provenance</span><select name="provenance"><option value="explicit">Explicit in the source</option><option value="inferred">Inferred from the source</option></select></label>
        <label class="field"><span>Explanation</span><textarea name="explanation" maxlength="1200" rows="3" required></textarea></label>
        <label class="field"><span>Supporting source chunks</span><select name="source_chunk_revision_ids" multiple size="4" required ${sourceOptions ? "" : "disabled"}>${sourceOptions}</select><small class="field-hint">Choose the exact source chunk(s) supporting this relationship.</small></label>
        <button class="btn btn-primary" type="submit" ${sourceOptions ? "" : "disabled"}>Add proposed relationship</button>
      </form></details>
    </section>` : ""}`}
  </section>`;
}

function safeErrorMessage(error) {
  const code = error?.code;
  const messages = {
    permission_denied: "You no longer have permission to make this change.",
    not_found: "This concept or source is no longer available. Refresh the map and try again.",
    version_conflict: "The map changed while you were editing. Refresh and retry your change.",
    prerequisite_cycle: "That relationship would create a prerequisite cycle. Review its direction.",
    self_edge: "A concept cannot be related to itself.",
    duplicate_edge: "This relationship already exists.",
    unsupported_source: "The selected source does not support this proposal.",
    invalid_parent: "Choose a valid parent topic from this class.",
    invalid_transition: "That review decision is no longer available. Refresh the map."
  };
  if (messages[code]) return messages[code];
  if (error?.status === 401 || error?.status === 403) return "You do not have access to this class graph.";
  return "The graph change could not be saved. Check the form and try again.";
}

function isPending(status) {
  return PENDING_STATUSES.has(String(status || "").toLowerCase());
}

function isComplete(status) {
  return ["completed", "complete", "succeeded", "ready"].includes(String(status || "").toLowerCase());
}

function inputValue(form, name) {
  return String(new FormData(form).get(name) || "").trim();
}

function selectedValues(form, name) {
  return new FormData(form).getAll(name).map((value) => String(value).trim()).filter(Boolean);
}

function uploadPreviewView(upload) {
  const extension = String(upload.original_filename || "").split(".").pop().toLowerCase();
  return ["docx", "pptx", "txt", "md"].includes(extension) ? "text" : "original";
}

async function openGraphSource(classId, chunkRevisionId, isCurrent = () => true) {
  const chunk = await getClassSourceChunkApi(classId, chunkRevisionId);
  if (!isCurrent()) return false;
  if (chunk.chunk_revision_id !== chunkRevisionId || typeof chunk.text !== "string") {
    throw new Error("The exact source revision could not be confirmed.");
  }
  const citation = sourceCitationFromChunk(chunk);
  if (!citation) throw new Error("This source no longer has an authorized material reference.");
  const detail = await getUploadDetailApi(chunk.material_id);
  if (!isCurrent()) return false;
  if (!detail.id || detail.status !== "ready") throw new Error("This source is no longer ready to open.");
  const upload = {
    id: detail.id,
    backendId: detail.id,
    name: detail.title || detail.original_filename || chunk.title || "Course material",
    kind: detail.kind,
    size: detail.file_size_bytes || 0,
    content: detail.extracted_text || "",
    fileUrl: detail.file_url,
    originalFilename: detail.original_filename,
    status: detail.status
  };
  const { openUploadPreview } = await import("./file-preview.js");
  if (!isCurrent()) return false;
  await openUploadPreview(upload, { citation, initialView: uploadPreviewView(upload) });
  return true;
}

export async function renderConceptGraphTab({ root, klass }) {
  const classId = getBackendClassId(klass);
  const canManage = classHasCapability(klass, "manage_permissions");
  const state = {
    graph: { concepts: [], edges: [] }, review: {}, uploads: [], extractionJobs: [],
    mergeHistory: {}, mergeHistoryError: "", canManage, error: "", reviewError: "", uploadError: "", statusMessage: ""
  };
  const polling = new Set();
  let loadGeneration = 0;
  root.innerHTML = `<div class="panel empty-state" role="status">Loading class concepts…</div>`;

  const render = () => { root.innerHTML = conceptGraphPageMarkup(state); };

  async function loadData({ preserveStatus = false } = {}) {
    if (!root.isConnected) return;
    if (!classId) {
      state.error = "Save this class to your account before opening its shared concept map.";
      render();
      return;
    }
    const generation = ++loadGeneration;
    state.error = "";
    state.reviewError = "";
    state.uploadError = "";
    state.mergeHistoryError = "";
    state.graph = { concepts: [], edges: [] };
    state.review = {};
    state.uploads = [];
    if (!preserveStatus) state.statusMessage = "";
    root.innerHTML = `<div class="panel empty-state" role="status">Loading the latest class graph…</div>`;
    const [graphResult, reviewResult, uploadResult, mergeHistoryResult] = await Promise.allSettled([
      getClassConceptGraphApi(classId),
      canManage ? getClassConceptGraphReviewApi(classId) : Promise.resolve({}),
      canManage ? getUploadsApi(classId) : Promise.resolve([]),
      canManage ? getClassConceptMergeHistoryApi(classId) : Promise.resolve({})
    ]);
    if (!root.isConnected || generation !== loadGeneration) return;
    if (graphResult.status === "fulfilled") state.graph = graphResult.value;
    else state.error = "The class concept map could not be loaded. Check access and try again.";
    if (reviewResult.status === "fulfilled") state.review = reviewResult.value;
    else state.reviewError = "Could not load proposed concepts or relationships.";
    if (uploadResult.status === "fulfilled") state.uploads = uploadResult.value;
    else state.uploadError = "Could not load the class source list.";
    if (mergeHistoryResult.status === "fulfilled") state.mergeHistory = mergeHistoryResult.value;
    else state.mergeHistoryError = "Could not load concept merge history.";
    const fromReview = normalizeReview(state.review).extractions;
    const existing = new Map(state.extractionJobs.map((job) => [String(job.operation_id), job]));
    for (const job of fromReview) if (job.operation_id) existing.set(String(job.operation_id), job);
    for (const job of readStoredExtractionJobs(classId)) {
      if (!existing.has(String(job.operation_id))) existing.set(String(job.operation_id), job);
    }
    state.extractionJobs = [...existing.values()];
    storeExtractionJobs(classId, state.extractionJobs);
    render();
    for (const job of state.extractionJobs) if (isPending(job.status)) void pollExtraction(job.operation_id);
  }

  async function pollExtraction(operationId, count = 0) {
    const key = String(operationId || "");
    if (!key || polling.has(key)) return;
    polling.add(key);
    const poll = async (attempt) => {
      if (!root.isConnected) { polling.delete(key); return; }
      if (attempt >= MAX_POLL_COUNT) {
        state.statusMessage = "Extraction is still running. You can check its status or leave this page open.";
        render();
        polling.delete(key);
        return;
      }
      try {
        const result = await getClassConceptExtractionApi(classId, key);
        if (!root.isConnected) { polling.delete(key); return; }
        const jobs = new Map(state.extractionJobs.map((job) => [String(job.operation_id), job]));
        jobs.set(key, result);
        state.extractionJobs = [...jobs.values()];
        storeExtractionJobs(classId, state.extractionJobs);
        render();
        if (isPending(result.status)) {
          window.setTimeout(() => void poll(attempt + 1), POLL_INTERVAL_MS);
        } else {
          polling.delete(key);
          if (isComplete(result.status)) {
            state.statusMessage = "Extraction finished. Review the new proposals below.";
            await loadData({ preserveStatus: true });
          }
        }
      } catch {
        if (!root.isConnected) { polling.delete(key); return; }
        const jobs = new Map(state.extractionJobs.map((job) => [String(job.operation_id), job]));
        const existing = jobs.get(key) || { operation_id: key, status: "pending" };
        jobs.set(key, { ...existing, status_check_failed: true });
        state.extractionJobs = [...jobs.values()];
        storeExtractionJobs(classId, state.extractionJobs);
        state.statusMessage = "Could not check extraction status. Use Check status to retry.";
        render();
        polling.delete(key);
      }
    };
    window.setTimeout(() => void poll(count), POLL_INTERVAL_MS);
  }

  async function reportMutationError(error, target) {
    state.statusMessage = safeErrorMessage(error);
    if (error?.code === "version_conflict" || error?.status === 409) {
      await loadData({ preserveStatus: true });
      root.querySelector("#concept-graph-title")?.focus();
      return;
    }
    const status = root.querySelector(".concept-graph-status");
    if (status) status.textContent = state.statusMessage;
    root.querySelector(target)?.focus();
  }

  root.addEventListener("click", async (event) => {
    const refreshButton = event.target.closest("[data-refresh-concept-graph]");
    if (refreshButton) { await loadData(); return; }

    const sourceButton = event.target.closest("[data-graph-source]");
    if (sourceButton) {
      const chunkRevisionId = sourceButton.dataset.graphSource;
      const status = root.querySelector(".concept-graph-status");
      const previousFocus = sourceButton;
      sourceButton.disabled = true;
      if (status) status.textContent = "Opening the authorized source…";
      try {
        const opened = await openGraphSource(classId, chunkRevisionId, () => root.isConnected && sourceButton.isConnected);
        if (opened && status?.isConnected) status.textContent = "Source opened at its cited location.";
      } catch {
        if (status?.isConnected) status.textContent = "This source could not be opened. It may have changed or access may have been removed.";
        if (previousFocus.isConnected) previousFocus.focus();
      } finally {
        if (sourceButton.isConnected) sourceButton.disabled = false;
      }
      return;
    }

    const extractionButton = event.target.closest("[data-start-extraction]");
    if (extractionButton) {
      const uploadId = extractionButton.dataset.startExtraction;
      extractionButton.disabled = true;
      state.statusMessage = "Starting source concept extraction…";
      render();
      try {
        const result = await startClassConceptExtractionApi(classId, uploadId);
        if (!root.isConnected) return;
        const jobs = new Map(state.extractionJobs.map((job) => [String(job.operation_id), job]));
        if (result.operation_id) jobs.set(String(result.operation_id), { ...result, upload_id: uploadId });
        state.extractionJobs = [...jobs.values()];
        storeExtractionJobs(classId, state.extractionJobs);
        state.statusMessage = isComplete(result.status) ? "Extraction finished. Loading proposals…" : "Extraction queued. You can keep reviewing the map.";
        render();
        if (result.operation_id && isPending(result.status)) void pollExtraction(result.operation_id);
        else await loadData({ preserveStatus: true });
      } catch (error) {
        state.statusMessage = safeErrorMessage(error);
        render();
      }
      return;
    }

    const checkButton = event.target.closest("[data-check-extraction]");
    if (checkButton) { void pollExtraction(checkButton.dataset.checkExtraction); return; }

    const conceptDecision = event.target.closest("[data-concept-decision]");
    if (conceptDecision) {
      if (conceptDecision.dataset.conceptDecision === "deprecate") {
        const confirmed = await openDialog({
          title: "Deprecate this concept?",
          message: "It will be removed from the active student map while its existing references remain auditable.",
          confirmLabel: "Deprecate concept",
          danger: true
        });
        if (!confirmed) return;
      }
      conceptDecision.disabled = true;
      try {
        await decideClassConceptApi(classId, conceptDecision.dataset.conceptId, conceptDecision.dataset.conceptDecision, Number(conceptDecision.dataset.version));
        state.statusMessage = "Concept review saved.";
        await loadData({ preserveStatus: true });
        root.querySelector("#concept-graph-title")?.focus();
      } catch (error) { await reportMutationError(error, `[data-concept-id="${CSS.escape(conceptDecision.dataset.conceptId)}"]`); }
      finally { if (conceptDecision.isConnected) conceptDecision.disabled = false; }
      return;
    }

    const edgeDecision = event.target.closest("[data-edge-decision]");
    if (edgeDecision) {
      edgeDecision.disabled = true;
      try {
        await decideClassConceptEdgeApi(classId, edgeDecision.dataset.edgeId, edgeDecision.dataset.edgeDecision, Number(edgeDecision.dataset.version));
        state.statusMessage = "Relationship review saved.";
        await loadData({ preserveStatus: true });
        root.querySelector("#concept-graph-title")?.focus();
      } catch (error) { await reportMutationError(error, `[data-edge-id="${CSS.escape(edgeDecision.dataset.edgeId)}"]`); }
      finally { if (edgeDecision.isConnected) edgeDecision.disabled = false; }
      return;
    }

    const aliasDecision = event.target.closest("[data-alias-decision]");
    if (aliasDecision) {
      aliasDecision.disabled = true;
      try {
        await decideClassConceptAliasApi(
          classId,
          aliasDecision.dataset.conceptId,
          aliasDecision.dataset.aliasId,
          aliasDecision.dataset.aliasDecision,
          Number(aliasDecision.dataset.version)
        );
        state.statusMessage = "Alias review saved.";
        await loadData({ preserveStatus: true });
        root.querySelector("#concept-graph-title")?.focus();
      } catch (error) {
        await reportMutationError(error, `[data-alias-id="${CSS.escape(aliasDecision.dataset.aliasId)}"]`);
      } finally {
        if (aliasDecision.isConnected) aliasDecision.disabled = false;
      }
      return;
    }

    const deleteAlias = event.target.closest("[data-delete-alias]");
    if (deleteAlias) {
      const confirmed = await openDialog({ title: "Remove this alias?", message: "This name will no longer be used as an alternate label for the concept.", confirmLabel: "Remove alias", danger: true });
      if (!confirmed) return;
      try {
        await deleteClassConceptAliasApi(classId, deleteAlias.dataset.conceptId, deleteAlias.dataset.deleteAlias, Number(deleteAlias.dataset.version));
        state.statusMessage = "Alias removed.";
        await loadData({ preserveStatus: true });
        root.querySelector("#concept-graph-title")?.focus();
      } catch (error) { await reportMutationError(error, `[data-delete-alias="${CSS.escape(deleteAlias.dataset.deleteAlias)}"]`); }
      return;
    }

    const deleteEdge = event.target.closest("[data-delete-edge]");
    if (deleteEdge) {
      const confirmed = await openDialog({ title: "Remove this relationship?", message: "Removing it from the shared map does not change historical student evidence.", confirmLabel: "Remove relationship", danger: true });
      if (!confirmed) return;
      try {
        await deleteClassConceptEdgeApi(classId, deleteEdge.dataset.deleteEdge, Number(deleteEdge.dataset.version));
        state.statusMessage = "Relationship removed.";
        await loadData({ preserveStatus: true });
        root.querySelector("#concept-graph-title")?.focus();
      } catch (error) { await reportMutationError(error, `[data-delete-edge="${CSS.escape(deleteEdge.dataset.deleteEdge)}"]`); }
    }
  });

  root.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      if (form.id === "create-class-concept") {
        await createClassConceptApi(classId, {
          canonical_name: inputValue(form, "canonical_name"),
          description: inputValue(form, "description"),
          parent_concept_id: inputValue(form, "parent_concept_id") || null,
          source_chunk_revision_ids: selectedValues(form, "source_chunk_revision_ids")
        });
        state.statusMessage = "Concept submitted for review.";
      } else if (form.id === "create-class-concept-edge") {
        const relationType = inputValue(form, "type");
        await createClassConceptEdgeApi(classId, {
          from_concept_id: inputValue(form, "from_concept_id"),
          to_concept_id: inputValue(form, "to_concept_id"),
          type: relationType,
          necessity: relationType === "prerequisite_for" ? inputValue(form, "necessity") || "helpful" : null,
          provenance: inputValue(form, "provenance"),
          explanation: inputValue(form, "explanation"),
          source_chunk_revision_ids: selectedValues(form, "source_chunk_revision_ids")
        });
        state.statusMessage = "Relationship submitted for review.";
      } else if (form.matches("[data-edit-concept]")) {
        await updateClassConceptApi(classId, form.dataset.editConcept, {
          canonical_name: inputValue(form, "canonical_name"),
          description: inputValue(form, "description"),
          parent_concept_id: inputValue(form, "parent_concept_id") || null
        }, Number(form.dataset.version));
        state.statusMessage = "Concept updated.";
      } else if (form.matches("[data-add-alias]")) {
        await addClassConceptAliasApi(classId, form.dataset.addAlias, {
          name: inputValue(form, "name"),
          language: inputValue(form, "language") || "und"
        });
        state.statusMessage = "Alias added.";
      } else if (form.matches("[data-merge-concept]")) {
        const targetId = inputValue(form, "target_concept_id");
        const target = list(state.graph.concepts).find((concept) => String(concept.id) === targetId);
        const source = list(state.graph.concepts).find((concept) => String(concept.id) === form.dataset.mergeConcept);
        const confirmed = await openDialog({
          title: "Review concept merge",
          message: `Merge “${displayName(source)}” into “${displayName(target)}”? Historical source references remain auditable; this cannot be undone from the graph page.`,
          confirmLabel: "Merge concepts",
          danger: true
        });
        if (!confirmed) { if (submit) submit.disabled = false; return; }
        await mergeClassConceptApi(classId, form.dataset.mergeConcept, targetId, Number(form.dataset.version));
        state.statusMessage = "Concepts merged.";
      } else return;
      await loadData({ preserveStatus: true });
      root.querySelector("#concept-graph-title")?.focus();
    } catch (error) {
      if (error?.code === "version_conflict" || error?.status === 409) {
        await reportMutationError(error, "#concept-graph-title");
        return;
      }
      state.statusMessage = safeErrorMessage(error);
      const status = root.querySelector(".concept-graph-status");
      if (status) status.textContent = state.statusMessage;
      const retryTarget = form.id ? `#${CSS.escape(form.id)}` : form.matches("[data-edit-concept]")
        ? `[data-edit-concept="${CSS.escape(form.dataset.editConcept)}"]`
        : form.matches("[data-add-alias]") ? `[data-add-alias="${CSS.escape(form.dataset.addAlias)}"]`
          : form.matches("[data-merge-concept]") ? `[data-merge-concept="${CSS.escape(form.dataset.mergeConcept)}"]` : "#concept-graph-title";
      (root.querySelector(retryTarget) || form)?.querySelector('[type="submit"]')?.focus();
    } finally {
      if (submit?.isConnected) submit.disabled = false;
    }
  });

  await loadData();
}
