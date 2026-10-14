import {
  createMasteryLadderApi,
  getClassConceptGraphApi,
  getMasteryLadderApi,
  retryMasteryLadderReasoningApi,
  skipMasteryLadderConceptApi,
  submitMasteryLadderReasoningApi,
  submitMasteryLadderResponseApi
} from "./api.js";
import { getBackendClassId } from "./course-sync.js";
import { loadTutorLanguage } from "./tutor-language.js";
import { escapeHtml, openDialog } from "./ui.js";

const LADDER_STATES = new Set(["active", "passed", "failed", "skipped", "unavailable"]);
const RECOVERY_STATES = new Set(["not_offered", "available", "used", "unavailable"]);
const CORRECTNESS = new Set(["correct", "incorrect", "unanswered", "pending", "unavailable"]);
const REASONING_STATUSES = new Set(["accepted", "insufficient", "pending", "failed", "unavailable"]);
const RUBRIC_STATUSES = new Set(["pending", "failed", "available", "passed", "unavailable"]);
const RUBRIC_RESULTS = new Set(["passed", "failed"]);
const POLICIES = new Set(["objective_only", "reasoning_required"]);
const LADDER_PROGRESSION = new Set(["advanced", "recovery_offered", "failed", "complete", "unavailable"]);
const ANSWER_DRAFT_LIMIT = 10_000;
const REASONING_DRAFT_LIMIT = 5_000;
const DRAFT_STORAGE_ENTRY_LIMIT = 24_000;
const DRAFT_ENTRY_COUNT_LIMIT = 100;
const OPERATION_STORAGE_ENTRY_LIMIT = 12_000;
const OPERATION_ENTRY_COUNT_LIMIT = 100;

function draftPrefix(userId) {
  return `jot.mastery-ladder-draft:v1:${encodeURIComponent(String(userId))}:`;
}

function ladderDraftStorageKey(userId, classId, ladderId, questionId, kind) {
  if (userId == null || !String(userId) || String(userId).length > 256
    || ![classId, ladderId, questionId].every((value) => typeof value === "string"
    && value.length > 0 && value.length <= 256)
    || !["answer", "reasoning"].includes(kind)) return null;
  const key = `${draftPrefix(userId)}${[classId, ladderId, questionId, kind].map(encodeURIComponent).join(":")}`;
  return key.length <= 2_048 ? key : null;
}

function validDraftValue(question, kind, value) {
  if (typeof value !== "string") return false;
  if (kind === "reasoning") return value.length <= REASONING_DRAFT_LIMIT;
  if (kind !== "answer" || value.length > ANSWER_DRAFT_LIMIT) return false;
  return !question?.choices?.length || question.choices.some((choice) => choice.id === value);
}

function pruneDraftEntries(userId, keepKey) {
  const prefix = draftPrefix(userId);
  const entries = [];
  try {
    const storage = globalThis.sessionStorage;
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key?.startsWith(prefix)) continue;
      let updatedAt = 0;
      try { updatedAt = Number(JSON.parse(storage.getItem(key) || "{}").updated_at) || 0; } catch { /* Old invalid entries sort first. */ }
      entries.push({ key, updatedAt });
    }
    entries.sort((left, right) => left.updatedAt - right.updatedAt);
    while (entries.length > DRAFT_ENTRY_COUNT_LIMIT) {
      const old = entries.findIndex((entry) => entry.key !== keepKey);
      if (old < 0) break;
      storage.removeItem(entries[old].key);
      entries.splice(old, 1);
    }
  } catch { /* Storage enumeration is best effort. */ }
}

/** Store only a bounded, unsent draft for this user's exact active ladder question. */
export function saveMasteryLadderDraft(userId, classId, ladderId, question, kind, value) {
  const key = ladderDraftStorageKey(userId, classId, ladderId, question?.id, kind);
  if (!key || !question) return false;
  if (!value) {
    clearMasteryLadderDraft(userId, classId, ladderId, question.id, kind);
    return true;
  }
  if (!validDraftValue(question, kind, value)) return false;
  const serialized = JSON.stringify({ version: 1, value, updated_at: Date.now() });
  if (serialized.length > DRAFT_STORAGE_ENTRY_LIMIT) return false;
  try {
    globalThis.sessionStorage.setItem(key, serialized);
    pruneDraftEntries(userId, key);
    return true;
  } catch { return false; }
}

export function readMasteryLadderDraft(userId, classId, ladderId, question, kind) {
  const key = ladderDraftStorageKey(userId, classId, ladderId, question?.id, kind);
  if (!key || !question) return "";
  try {
    const serialized = globalThis.sessionStorage.getItem(key);
    if (!serialized || serialized.length > DRAFT_STORAGE_ENTRY_LIMIT) return "";
    const stored = JSON.parse(serialized);
    return stored?.version === 1 && validDraftValue(question, kind, stored.value) ? stored.value : "";
  } catch { return ""; }
}

export function clearMasteryLadderDraft(userId, classId, ladderId, questionId, kind) {
  const key = ladderDraftStorageKey(userId, classId, ladderId, questionId, kind);
  if (!key) return;
  try { globalThis.sessionStorage.removeItem(key); } catch { /* Best effort after the server confirms the transition. */ }
}

function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("Secure operation IDs are unavailable in this browser.");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function validCount(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function normalizeQuestion(raw) {
  if (raw == null) return null;
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string" || !raw.id || raw.id.length > 256
    || typeof raw.prompt !== "string" || !raw.prompt.trim()
    || !Number.isInteger(raw.level) || raw.level < 1 || raw.level > 4
    || typeof raw.kind !== "string"
    || !raw.interaction || typeof raw.interaction.response_type !== "string"
    || !Array.isArray(raw.choices)) return undefined;
  const choices = raw.choices.map((choice) => {
    if (!choice || typeof choice.id !== "string" || !choice.id || choice.id.length > 256 || typeof choice.text !== "string") return null;
    return { id: choice.id, text: choice.text.slice(0, 1000) };
  });
  if (choices.some((choice) => !choice)) return undefined;
  return {
    id: raw.id,
    prompt: raw.prompt.slice(0, 6000),
    level: raw.level,
    kind: raw.kind.slice(0, 80),
    choices,
    interaction: { response_type: raw.interaction.response_type.slice(0, 80) }
  };
}

function normalizeLadderConcept(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.concept_id !== "string" || !raw.concept_id
    || typeof raw.label !== "string" || !raw.label.trim() || !LADDER_STATES.has(raw.state)
    || !Number.isInteger(raw.current_level) || raw.current_level < 1 || raw.current_level > 4
    || !RECOVERY_STATES.has(raw.recovery_state)) return null;
  const question = normalizeQuestion(raw.current_question);
  if (question === undefined) return null;
  return {
    concept_id: raw.concept_id,
    label: raw.label.slice(0, 240),
    state: raw.state,
    current_level: raw.current_level,
    recovery_state: raw.recovery_state,
    current_question: question
  };
}

/** Allowlist the server-owned ladder snapshot; hidden key/rubric fields are discarded. */
export function normalizeMasteryLadder(dto) {
  if (!dto || typeof dto !== "object" || typeof dto.ladder_id !== "string" || !dto.ladder_id
    || !["active", "completed", "failed", "unavailable"].includes(dto.status)
    || !POLICIES.has(dto.grading_policy)
    || !Number.isSafeInteger(dto.version) || dto.version < 0 || !Array.isArray(dto.concepts)) {
    throw new Error("Mastery Ladder is not available in the expected format.");
  }
  const concepts = dto.concepts.map(normalizeLadderConcept);
  if (concepts.some((concept) => !concept)) throw new Error("A ladder concept could not be safely displayed.");
  const attempts = (Array.isArray(dto.attempts) ? dto.attempts : []).map((attempt) => {
    const followUp = attempt?.reasoning_follow_up ?? attempt?.follow_up ?? "";
    if (!attempt || typeof attempt !== "object" || typeof attempt.question_id !== "string"
      || typeof attempt.state !== "string" || typeof attempt.answer_status !== "string"
      || (attempt.correctness != null && !CORRECTNESS.has(attempt.correctness))
      || !Number.isSafeInteger(attempt.version) || attempt.version < 0
      || (attempt.xp_awarded != null && !validCount(attempt.xp_awarded))
      || (attempt.reasoning_status != null && !REASONING_STATUSES.has(attempt.reasoning_status))
      || (attempt.rubric_status != null && !RUBRIC_STATUSES.has(attempt.rubric_status))
      || (attempt.rubric_result != null && !RUBRIC_RESULTS.has(attempt.rubric_result))
      || (followUp !== "" && (typeof followUp !== "string" || followUp.length > 600))
      || (attempt.reasoning_status === "insufficient" && (typeof followUp !== "string" || !followUp.trim()))) return null;
    return {
      question_id: attempt.question_id,
      state: attempt.state.slice(0, 80),
      answer_status: attempt.answer_status.slice(0, 80),
      correctness: attempt.correctness ?? null,
      version: attempt.version,
      xp_awarded: attempt.xp_awarded ?? null,
      reasoning_status: attempt.reasoning_status ?? null,
      rubric_status: attempt.rubric_status ?? null,
      rubric_result: attempt.rubric_result ?? null,
      reasoning_follow_up: followUp.slice(0, 600)
    };
  });
  if (attempts.some((attempt) => !attempt)) throw new Error("A ladder attempt could not be safely displayed.");
  return {
    ladder_id: dto.ladder_id,
    status: dto.status,
    grading_policy: dto.grading_policy,
    version: dto.version,
    concepts,
    attempts
  };
}

export function normalizeLadderReasoningResult(dto) {
  const status = dto?.status ?? dto?.reasoning_status;
  if (!dto || typeof dto !== "object" || !REASONING_STATUSES.has(status)) {
    throw new Error("The reasoning check returned an unexpected status.");
  }
  if (dto.follow_up != null && (typeof dto.follow_up !== "string" || dto.follow_up.length > 600)) {
    throw new Error("The reasoning follow-up could not be safely displayed.");
  }
  if (status === "insufficient" && (typeof dto.follow_up !== "string" || !dto.follow_up.trim())) {
    throw new Error("The reasoning check did not include a usable follow-up.");
  }
  return {
    status,
    follow_up: typeof dto.follow_up === "string" ? dto.follow_up.slice(0, 600) : "",
    version: Number.isSafeInteger(dto.version) && dto.version >= 0 ? dto.version : null,
    attempt_version: Number.isSafeInteger(dto.attempt_version) && dto.attempt_version >= 0 ? dto.attempt_version : null
  };
}

export function normalizeLadderConceptChoices(dto) {
  if (!dto || !Array.isArray(dto.concepts)) throw new Error("The class concept list is unavailable.");
  return dto.concepts.flatMap((concept) => {
    const state = concept?.state || concept?.status;
    const label = concept?.canonical_name || concept?.label;
    if (state !== "validated" || typeof concept.id !== "string" || !concept.id
      || typeof label !== "string" || !label.trim()) return [];
    return [{ concept_id: concept.id, label: label.slice(0, 240) }];
  });
}

function stateLabel(state) {
  return ({ active: "In progress", passed: "Passed", failed: "Finished", skipped: "Skipped", unavailable: "Temporarily unavailable" })[state] || state;
}

function correctnessLabel(value) {
  return ({ correct: "Correct", incorrect: "Incorrect", unanswered: "Unanswered", pending: "Grading pending", unavailable: "Result unavailable" })[value] || "Submitted";
}

export function masteryLadderProgressMessage(result) {
  const progression = LADDER_PROGRESSION.has(result?.progression) ? result.progression : null;
  const message = progression === "advanced" ? "Level passed. Your next level is ready."
    : progression === "recovery_offered" ? "A separate recovery question is ready at this level."
      : progression === "failed" ? "This concept's ladder has ended. Other selected concepts can continue."
        : progression === "complete" ? "You completed all four levels for this concept."
          : progression === "unavailable" ? "No independent question is available right now; no additional failure was recorded."
            : "Response saved. Your progression has been updated.";
  return validCount(result?.xp_awarded) && result.xp_awarded > 0
    ? `${message} ${result.xp_awarded} XP recorded by the server.`
    : message;
}

function ladderStorageKey(userId, classId) {
  return `jot.mastery-ladder:${String(userId || "account")}:${classId}`;
}

function createStorageKey(userId, classId) {
  return `jot.mastery-ladder-create:${String(userId || "account")}:${classId}`;
}

function operationStorageKey(kind, userId, classId, ladderId, itemId) {
  if (![classId, ladderId, itemId].every((value) => typeof value === "string" && value.length > 0 && value.length <= 256)
    || !["reasoning", "response", "skip"].includes(kind)) return null;
  const key = `jot.mastery-ladder-operation:v1:${encodeURIComponent(String(userId || "account"))}:${kind}:${[classId, ladderId, itemId].map(encodeURIComponent).join(":")}`;
  return key.length <= 2048 ? key : null;
}

function readStoredLadder(userId, classId) {
  try { return sessionStorage.getItem(ladderStorageKey(userId, classId)) || ""; } catch { return ""; }
}

function writeStoredLadder(userId, classId, ladderId) {
  try { sessionStorage.setItem(ladderStorageKey(userId, classId), ladderId); } catch { /* Server remains authoritative. */ }
}

function removeStoredLadder(userId, classId) {
  try { sessionStorage.removeItem(ladderStorageKey(userId, classId)); } catch { /* Best effort. */ }
}

function readPendingCreate(userId, classId) {
  try {
    const raw = sessionStorage.getItem(createStorageKey(userId, classId));
    const pending = raw ? JSON.parse(raw) : null;
    const payload = pending?.payload;
    if (pending?.payloadKey && payload?.class_id === classId
      && typeof payload.operation_id === "string" && Array.isArray(payload.concept_ids)
      && payload.concept_ids.length > 0 && payload.concept_ids.length <= 100
      && typeof payload.language === "string" && POLICIES.has(payload.grading_policy)) return pending;
  } catch { /* A lost client retry record does not change server state. */ }
  return null;
}

function savePendingCreate(userId, classId, pending) {
  try { sessionStorage.setItem(createStorageKey(userId, classId), JSON.stringify(pending)); } catch { /* Same-page retry remains available. */ }
}

function clearPendingCreate(userId, classId) {
  try { sessionStorage.removeItem(createStorageKey(userId, classId)); } catch { /* Best effort. */ }
}

export function readMasteryLadderOperation(kind, userId, classId, ladderId, itemId) {
  const key = operationStorageKey(kind, userId, classId, ladderId, itemId);
  if (!key) return null;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > OPERATION_STORAGE_ENTRY_LIMIT) return null;
    const value = JSON.parse(raw);
    return value && typeof value.operation_id === "string" && value.operation_id.length <= 80
      && Number.isSafeInteger(value.expected_version) && value.expected_version >= 0
      ? value : null;
  } catch { return null; }
}

function pruneOperationEntries(userId, keepKey) {
  const prefix = `jot.mastery-ladder-operation:v1:${encodeURIComponent(String(userId || "account"))}:`;
  const entries = [];
  try {
    const storage = globalThis.sessionStorage;
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key?.startsWith(prefix)) continue;
      let updatedAt = 0;
      try { updatedAt = Number(JSON.parse(storage.getItem(key) || "{}").updated_at) || 0; } catch { /* Old invalid entries sort first. */ }
      entries.push({ key, updatedAt });
    }
    entries.sort((left, right) => left.updatedAt - right.updatedAt);
    while (entries.length > OPERATION_ENTRY_COUNT_LIMIT) {
      const old = entries.findIndex((entry) => entry.key !== keepKey);
      if (old < 0) break;
      storage.removeItem(entries[old].key);
      entries.splice(old, 1);
    }
  } catch { /* Storage maintenance is best effort. */ }
}

export function saveMasteryLadderOperation(kind, userId, classId, ladderId, itemId, value) {
  const key = operationStorageKey(kind, userId, classId, ladderId, itemId);
  if (!key) return;
  try {
    const serialized = JSON.stringify({ ...value, updated_at: Date.now() });
    if (serialized.length > OPERATION_STORAGE_ENTRY_LIMIT) return;
    sessionStorage.setItem(key, serialized);
    pruneOperationEntries(userId, key);
  } catch { /* Same-page retries still work. */ }
}

export function clearMasteryLadderOperation(kind, userId, classId, ladderId, itemId) {
  const key = operationStorageKey(kind, userId, classId, ladderId, itemId);
  if (!key) return;
  try { sessionStorage.removeItem(key); } catch { /* Best effort. */ }
}

function readReasoningOperation(userId, classId, ladderId, questionId) {
  return readMasteryLadderOperation("reasoning", userId, classId, ladderId, questionId);
}

function saveReasoningOperation(userId, classId, ladderId, questionId, value) {
  saveMasteryLadderOperation("reasoning", userId, classId, ladderId, questionId, value);
}

function clearReasoningOperation(userId, classId, ladderId, questionId) {
  clearMasteryLadderOperation("reasoning", userId, classId, ladderId, questionId);
}

function conceptSummaryMarkup(ladder) {
  return `<ul class="mastery-ladder-summary">${ladder.concepts.map((concept) => `<li><span><strong>${escapeHtml(concept.label)}</strong><small>Level ${concept.current_level} · ${stateLabel(concept.state)}</small></span>${concept.recovery_state === "available" ? `<span class="permission-badge">Recovery available</span>` : ""}</li>`).join("")}</ul>`;
}

function activeConceptSelect(ladder, selectedId, interactionLocked = false) {
  return `<label class="field mastery-ladder-concept-select"><span>Concept to practice</span><select data-ladder-concept-select ${interactionLocked ? "disabled" : ""}>${ladder.concepts.map((concept) => `<option value="${escapeHtml(concept.concept_id)}" ${concept.concept_id === selectedId ? "selected" : ""}>${escapeHtml(concept.label)} · ${stateLabel(concept.state)}</option>`).join("")}</select></label>`;
}

function questionMarkup(concept, ladder, answerDraft, reasoningStatus, reasoningDraft, reasoningFollowUp = "", interactionLocked = false, onRefreshAvailable = false) {
  const question = concept.current_question;
  if (!question) {
    const message = concept.state === "unavailable"
      ? "A distinct question is not available right now. This concept has not been marked incorrect. You can check again later."
      : `This concept is ${stateLabel(concept.state).toLowerCase()}. Other concepts in the ladder can continue independently.`;
    return `<div class="panel empty-state"><strong>${escapeHtml(stateLabel(concept.state))}</strong><span>${escapeHtml(message)}</span>${onRefreshAvailable ? `<button class="btn btn-secondary btn-small" type="button" data-refresh-ladder ${interactionLocked ? "disabled" : ""}>Refresh ladder</button>` : ""}</div>`;
  }
  const attempt = ladder.attempts.filter((item) => item.question_id === question.id).at(-1);
  const attemptSubmitted = ["submitted", "completed", "skipped"].includes(attempt?.state);
  const rubricRetryAvailable = ["submitted", "completed"].includes(attempt?.state)
    && attempt.rubric_status === "failed";
  const rubricPending = attempt?.rubric_status === "pending";
  const responseLocked = interactionLocked || attemptSubmitted;
  const choices = question.choices.length
    ? `<fieldset class="mastery-ladder-answer-choices"><legend>Choose an answer</legend>${question.choices.map((choice, index) => `<label class="mastery-ladder-choice"><input type="radio" name="ladder-answer" value="${escapeHtml(choice.id)}" ${answerDraft === choice.id ? "checked" : ""} ${responseLocked ? "disabled" : ""} required /><span><span class="mastery-ladder-choice-marker">${String.fromCharCode(65 + index)}</span><span>${escapeHtml(choice.text)}</span></span></label>`).join("")}</fieldset>`
    : `<label class="field"><span>Your answer</span><textarea name="ladder-answer" rows="4" maxlength="10000" ${responseLocked ? "disabled" : ""} required>${escapeHtml(answerDraft || "")}</textarea></label>`;
  const isReasoning = ladder.grading_policy === "reasoning_required";
  const currentReasoningStatus = reasoningStatus || attempt?.reasoning_status || null;
  const reasoningAccepted = currentReasoningStatus === "accepted";
  const reasoningPending = currentReasoningStatus === "pending";
  const reasoningInsufficient = currentReasoningStatus === "insufficient";
  const reasoningFailed = ["failed", "unavailable"].includes(currentReasoningStatus);
  const reasoningLocked = reasoningAccepted || reasoningPending || interactionLocked || attemptSubmitted;
  const followUp = reasoningFollowUp || attempt?.reasoning_follow_up || "";
  const reasoningBlock = !isReasoning ? "" : `
    <fieldset class="field-block mastery-ladder-reasoning">
      <legend>Reasoning check</legend>
      <p class="field-hint">Explain your approach. The ladder checks reasoning quality separately from whether the answer is correct.</p>
      ${reasoningInsufficient && followUp ? `<p class="mastery-ladder-follow-up" role="note"><strong>Try this next:</strong> ${escapeHtml(followUp)}</p>` : ""}
      <label class="field"><span>Explain your thinking</span><textarea data-ladder-reasoning rows="3" maxlength="5000" ${reasoningLocked ? "disabled" : ""}>${escapeHtml(reasoningDraft || "")}</textarea></label>
      <div class="mastery-ladder-inline-actions">
        <button class="btn btn-secondary btn-small" type="button" data-submit-ladder-reasoning ${reasoningLocked ? "disabled" : ""}>${reasoningInsufficient ? "Add to my reasoning" : reasoningPending || reasoningFailed ? "Check reasoning" : "Submit reasoning"}</button>
        ${!reasoningAccepted && (reasoningPending || reasoningFailed) ? `<button class="btn btn-ghost btn-small" type="button" data-retry-ladder-reasoning ${interactionLocked || attemptSubmitted ? "disabled" : ""}>Retry reasoning check</button>` : ""}
        <span class="field-hint" role="status" aria-live="polite" data-ladder-reasoning-status>${reasoningAccepted ? (attemptSubmitted ? "Reasoning accepted for this saved answer." : "Reasoning accepted. You can submit your answer.") : reasoningInsufficient ? "Add another explanation; your earlier reasoning is saved." : reasoningPending ? "Reasoning check is pending." : reasoningFailed ? "Reasoning could not be graded. Retry the check; no incorrect result was recorded." : ""}</span>
      </div>
    </fieldset>`;
  const answerDisabled = (isReasoning && !reasoningAccepted) || responseLocked || rubricPending || rubricRetryAvailable;
  const rubricNotice = rubricPending
    ? `<p class="field-hint" role="status">Your answer is saved. Reasoning rubric grading is pending.</p>`
    : rubricRetryAvailable
      ? `<div class="field-block" role="alert"><p>Your answer is saved, but reasoning rubric grading failed. Retry grading this saved answer; your answer and reasoning will not be resubmitted as new work.</p><button class="btn btn-secondary btn-small" type="button" data-retry-ladder-response ${interactionLocked ? "disabled" : ""}>Retry saved answer grading</button></div>`
      : "";
  const history = attempt ? `<p class="row-meta" role="status">Latest response: ${escapeHtml(correctnessLabel(attempt.correctness))}${attempt.xp_awarded !== null ? ` · ${attempt.xp_awarded} XP recorded by the server` : ""}</p>` : "";
  return `<article class="panel mastery-ladder-question">
    <p class="eyebrow">Level ${question.level} · ${escapeHtml(question.kind.replaceAll("_", " "))}</p>
    <h3>${escapeHtml(question.prompt)}</h3>
    ${concept.recovery_state === "available" ? `<p class="field-hint" role="note">A separate recovery question is available for this concept.</p>` : ""}
    ${reasoningBlock}
    ${rubricNotice}
    <form data-ladder-answer-form>${choices}<button class="btn btn-primary" type="submit" data-submit-ladder-answer ${answerDisabled ? "disabled" : ""}>Submit answer</button></form>
    <button class="btn btn-ghost btn-small" type="button" data-skip-ladder-concept ${responseLocked || rubricPending ? "disabled" : ""}>Skip this concept</button>
    ${history}
  </article>`;
}

export function masteryLadderMarkup(ladder, selectedConceptId, drafts = {}, reasoningStatus = null, reasoningDraft = "", interactionLocked = false, reasoningFollowUp = "") {
  const selected = ladder.concepts.find((concept) => concept.concept_id === selectedConceptId) || ladder.concepts[0];
  return `<section class="section-stack mastery-ladder-player" aria-labelledby="mastery-ladder-title">
    <header class="page-heading"><div><p class="eyebrow">Mastery Ladder</p><h2 id="mastery-ladder-title">Your concept progression</h2><p class="muted">Each concept progresses independently. A skipped concept does not count as an incorrect answer.</p></div><button class="btn btn-ghost btn-small" type="button" data-new-ladder ${interactionLocked ? "disabled" : ""}>Start another ladder</button></header>
    <div class="mastery-ladder-policy-note"><strong>${ladder.grading_policy === "reasoning_required" ? "Reasoning-required progression" : "Objective-only progression"}</strong><span>${ladder.grading_policy === "reasoning_required" ? "Both the server-graded answer and a separate reasoning-quality check guide progression." : "The server grades each response and controls progression."}</span></div>
    ${conceptSummaryMarkup(ladder)}
    ${selected ? activeConceptSelect(ladder, selected.concept_id, interactionLocked) : ""}
    ${selected ? questionMarkup(selected, ladder, drafts[selected.current_question?.id] || "", reasoningStatus, reasoningDraft, reasoningFollowUp, interactionLocked, true) : `<p class="panel empty-state">No concepts are available in this ladder.</p>`}
  </section>`;
}

export async function renderMasteryLadderPanel({ root, klass, currentUserId = null }) {
  const classId = getBackendClassId(klass);
  if (!classId) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = `<div class="panel empty-state" role="status" aria-busy="true">Loading validated concepts…</div>`;
  const drafts = new Map();
  const reasoningDrafts = new Map();
  const reasoningOperations = new Map();
  let currentLadder = null;
  let selectedConceptId = null;
  let reasoningStatus = null;
  let reasoningFollowUp = "";
  let reasoningDraft = "";
  let pendingResponseOperation = null;
  let pendingSkipOperation = null;
  let pendingCreate = null;
  let statusMessage = "";
  let syncRequired = false;
  let concepts = [];
  pendingCreate = readPendingCreate(currentUserId, classId);

  const currentQuestion = () => currentLadder?.concepts.find((item) => item.concept_id === selectedConceptId)?.current_question;
  const keyForQuestion = (questionId) => `${currentLadder?.ladder_id}:${questionId}`;
  const readReasoningOperationFor = (questionId) => reasoningOperations.get(keyForQuestion(questionId))
    || readReasoningOperation(currentUserId, classId, currentLadder.ladder_id, questionId);
  const saveReasoningOperationFor = (questionId, operation) => {
    reasoningOperations.set(keyForQuestion(questionId), operation);
    saveReasoningOperation(currentUserId, classId, currentLadder.ladder_id, questionId, operation);
  };
  const clearReasoningOperationFor = (questionId) => {
    reasoningOperations.delete(keyForQuestion(questionId));
    clearReasoningOperation(currentUserId, classId, currentLadder.ladder_id, questionId);
  };
  const readResponseOperationFor = (question) => {
    const saved = readMasteryLadderOperation("response", currentUserId, classId, currentLadder.ladder_id, question.id);
    const answer = drafts.get(question.id) ?? saved?.answer;
    return saved && saved.question_id === question.id && saved.answer === answer
      && validDraftValue(question, "answer", saved.answer) ? saved : null;
  };
  const clearResponseOperationFor = (questionId) => {
    if (pendingResponseOperation?.question_id === questionId) pendingResponseOperation = null;
    clearMasteryLadderOperation("response", currentUserId, classId, currentLadder.ladder_id, questionId);
  };

  function renderForm() {
    root.innerHTML = `<section class="panel mastery-ladder-launcher" aria-labelledby="mastery-ladder-launch-title">
      <div class="page-heading"><div><p class="eyebrow">Practice mode</p><h2 id="mastery-ladder-launch-title">Mastery Ladder</h2><p class="muted">Work through four cognitive levels for selected class concepts. Your progress is private to your account.</p></div></div>
      ${concepts.length ? `<form data-mastery-ladder-start>
        <fieldset class="field-block"><legend>Choose concepts</legend><div class="mastery-ladder-concept-options">${concepts.map((concept) => `<label><input type="checkbox" name="concept_id" value="${escapeHtml(concept.concept_id)}" ${pendingCreate?.payload?.concept_ids?.includes(concept.concept_id) ? "checked" : ""}><span>${escapeHtml(concept.label)}</span></label>`).join("")}</div></fieldset>
        <label class="field"><span>Progression policy</span><select name="grading_policy"><option value="objective_only" ${pendingCreate?.payload?.grading_policy === "objective_only" ? "selected" : ""}>Objective-only</option><option value="reasoning_required" ${pendingCreate?.payload?.grading_policy === "reasoning_required" ? "selected" : ""}>Reasoning-required</option></select></label>
        <p class="field-hint">Reasoning-required adds a separate quality rubric. It is distinct from a check that you made a meaningful attempt.</p>
        <button class="btn btn-secondary" type="submit" data-start-ladder>Start Mastery Ladder</button>
        <p class="field-hint" role="status" aria-live="polite" data-ladder-launch-status>${escapeHtml(statusMessage)}</p>
      </form>` : `<div class="empty-state"><strong>No validated concepts yet</strong><span>Concept practice becomes available after this class has validated concepts in its Concepts tab.</span></div>`}
    </section>`;
    const form = root.querySelector("[data-mastery-ladder-start]");
    if (!form) return;
    form.addEventListener("change", () => {
      const selectedIds = [...form.querySelectorAll('input[name="concept_id"]:checked')].map((input) => input.value);
      const payloadKey = JSON.stringify({ selected: selectedIds, policy: form.elements.grading_policy.value });
      if (pendingCreate?.payloadKey !== payloadKey) {
        pendingCreate = null;
        clearPendingCreate(currentUserId, classId);
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = [...form.querySelectorAll('input[name="concept_id"]:checked')].map((input) => input.value);
      const status = form.querySelector("[data-ladder-launch-status]");
      const button = form.querySelector("[data-start-ladder]");
      if (!selected.length) {
        status.textContent = "Choose at least one concept.";
        form.querySelector('input[name="concept_id"]')?.focus();
        return;
      }
      button.disabled = true;
      status.textContent = "Creating your private ladder…";
      try {
        const payloadKey = JSON.stringify({ selected, policy: form.elements.grading_policy.value });
        if (!pendingCreate || pendingCreate.payloadKey !== payloadKey) {
          pendingCreate = {
            payloadKey,
            payload: {
              operation_id: requestId(),
              class_id: classId,
              concept_ids: selected,
              language: loadTutorLanguage(),
              grading_policy: form.elements.grading_policy.value
            }
          };
          savePendingCreate(currentUserId, classId, pendingCreate);
        }
        const created = normalizeMasteryLadder(await createMasteryLadderApi(pendingCreate.payload));
        pendingCreate = null;
        clearPendingCreate(currentUserId, classId);
        writeStoredLadder(currentUserId, classId, created.ladder_id);
        try {
          currentLadder = normalizeMasteryLadder(await getMasteryLadderApi(created.ladder_id));
        } catch {
          currentLadder = created;
          statusMessage = "Your ladder was created. The latest saved state could not be refreshed yet.";
        }
        selectedConceptId = currentLadder.concepts.find((item) => item.state === "active")?.concept_id || currentLadder.concepts[0]?.concept_id || null;
        renderPlayer();
      } catch (error) {
        status.textContent = error.message || "The ladder could not be started. Try again.";
        button.disabled = false;
      }
    });
  }

  function renderPlayer() {
    if (!currentLadder) return;
    const question = currentQuestion();
    if (question && !drafts.has(question.id)) {
      const restored = readMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question, "answer");
      if (restored) drafts.set(question.id, restored);
    }
    if (question && !reasoningDrafts.has(question.id)) {
      reasoningDrafts.set(question.id, readMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question, "reasoning"));
    }
    reasoningDraft = question ? reasoningDrafts.get(question.id) || "" : "";
    root.innerHTML = `<p class="field-hint" role="status" aria-live="polite" data-ladder-status>${escapeHtml(statusMessage)}</p>${syncRequired ? `<button class="btn btn-secondary btn-small" type="button" data-refresh-ladder>Retry refresh</button>` : ""}${masteryLadderMarkup(currentLadder, selectedConceptId, Object.fromEntries(drafts), reasoningStatus, reasoningDraft, syncRequired, reasoningFollowUp)}`;
    root.querySelectorAll("[data-refresh-ladder]").forEach((button) => button.addEventListener("click", () => void refreshLadder()));
    root.querySelector("[data-new-ladder]")?.addEventListener("click", async () => {
      const confirmed = await openDialog({
        title: "Start a new ladder?",
        message: "Your current ladder remains saved. You can start a separate attempt with new concepts.",
        confirmLabel: "Choose concepts"
      });
      if (confirmed) {
        currentLadder = null;
        syncRequired = false;
        statusMessage = "";
        renderForm();
      }
    });
    root.querySelector("[data-ladder-concept-select]")?.addEventListener("change", (event) => {
      selectedConceptId = event.currentTarget.value;
      reasoningStatus = null;
      reasoningFollowUp = "";
      pendingResponseOperation = null;
      renderPlayer();
    });
    const answerForm = root.querySelector("[data-ladder-answer-form]");
    answerForm?.querySelectorAll("input, textarea").forEach((input) => input.addEventListener("input", () => {
      const q = currentQuestion();
      if (q) {
        const value = input.type === "radio" ? input.checked ? input.value : (drafts.get(q.id) || "") : input.value;
        drafts.set(q.id, value);
        saveMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, q, "answer", value);
        clearResponseOperationFor(q.id);
      }
    }));
    answerForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      const q = currentQuestion();
      if (!q || !selectedConceptId) return;
      const answer = q.choices.length
        ? answerForm.querySelector('input[name="ladder-answer"]:checked')?.value || ""
        : answerForm.querySelector('[name="ladder-answer"]')?.value || "";
      void submitLadderAnswer(q, answer);
    });
    root.querySelector("[data-retry-ladder-response]")?.addEventListener("click", () => {
      const q = currentQuestion();
      if (!q) return;
      const operation = readResponseOperationFor(q);
      if (!operation) {
        statusMessage = "The saved answer retry is unavailable in this tab. Your submitted answer remains on the server; refresh the ladder or contact support.";
        renderPlayer();
        return;
      }
      void submitLadderAnswer(q, operation.answer, { retrySaved: true });
    });
    const reasoningField = root.querySelector("[data-ladder-reasoning]");
    reasoningField?.addEventListener("input", () => {
      const q = currentQuestion();
      reasoningDraft = reasoningField.value;
      if (q) {
        reasoningDrafts.set(q.id, reasoningDraft);
        saveMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, q, "reasoning", reasoningDraft);
        // A changed payload must use a fresh id; retries of an unchanged payload retain the saved id.
        clearReasoningOperationFor(q.id);
      }
    });
    root.querySelector("[data-submit-ladder-reasoning]")?.addEventListener("click", () => void submitReasoning(false));
    root.querySelector("[data-retry-ladder-reasoning]")?.addEventListener("click", () => void submitReasoning(true));
    root.querySelector("[data-skip-ladder-concept]")?.addEventListener("click", () => void skipCurrentConcept());
  }

  async function submitLadderAnswer(question, answer, { retrySaved = false } = {}) {
    if (!question || !selectedConceptId || typeof answer !== "string" || !answer.trim()) return;
    const conceptId = selectedConceptId;
    if (!validDraftValue(question, "answer", answer)) return;
    const opKey = keyForQuestion(question.id);
    if (retrySaved) {
      const saved = readResponseOperationFor(question);
      if (!saved || saved.answer !== answer) {
        statusMessage = "The saved answer retry is unavailable in this tab. Your submitted answer remains on the server; refresh the ladder or contact support.";
        renderPlayer();
        return;
      }
      pendingResponseOperation = { ...saved, key: opKey, expected_version: currentLadder.version };
    } else {
      drafts.set(question.id, answer);
      saveMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question, "answer", answer);
      if (!pendingResponseOperation || pendingResponseOperation.key !== opKey || pendingResponseOperation.answer !== answer) {
        const saved = readResponseOperationFor(question);
        pendingResponseOperation = saved && saved.answer === answer
          ? { ...saved, key: opKey }
          : { key: opKey, question_id: question.id, answer, operation_id: requestId(), expected_version: currentLadder.version };
      }
    }
    saveMasteryLadderOperation("response", currentUserId, classId, currentLadder.ladder_id, question.id, pendingResponseOperation);
    statusMessage = retrySaved ? "Retrying grading for your saved answer…" : "Submitting your response…";
    syncRequired = true;
    renderPlayer();
    try {
      const result = await submitMasteryLadderResponseApi(currentLadder.ladder_id, conceptId, {
        operation_id: pendingResponseOperation.operation_id,
        question_id: question.id,
        answer,
        expected_version: pendingResponseOperation.expected_version
      });
      if (drafts.get(question.id) === answer) {
        drafts.delete(question.id);
        clearMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question.id, "answer");
      }
      if (reasoningDrafts.get(question.id) === reasoningDraft) {
        reasoningDrafts.delete(question.id);
        clearMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question.id, "reasoning");
      }
      clearReasoningOperationFor(question.id);
      clearResponseOperationFor(question.id);
      pendingResponseOperation = null;
      statusMessage = masteryLadderProgressMessage(result);
      await refreshLadder(statusMessage);
    } catch (error) {
      statusMessage = error.code === "version_conflict"
        ? "This ladder changed in another request. Your saved answer is preserved; the latest state is loading."
        : error.message || "Your response could not be submitted. Your answer is preserved; try again.";
      syncRequired = true;
      renderPlayer();
      await refreshLadder(statusMessage);
    }
  }

  async function refreshLadder(announcement = "") {
    const ladderId = currentLadder.ladder_id;
    try {
      currentLadder = normalizeMasteryLadder(await getMasteryLadderApi(ladderId));
      const current = currentLadder.concepts.find((item) => item.concept_id === selectedConceptId);
      if (!current || current.state !== "active") {
        selectedConceptId = currentLadder.concepts.find((item) => item.state === "active")?.concept_id || selectedConceptId;
      }
      syncRequired = false;
      const question = currentQuestion();
      reasoningStatus = null;
      reasoningFollowUp = "";
      for (const concept of currentLadder.concepts) {
        if (concept.state !== "active") clearMasteryLadderOperation("skip", currentUserId, classId, currentLadder.ladder_id, concept.concept_id);
      }
      if (question) {
        const operation = readReasoningOperationFor(question.id);
        const attempt = [...currentLadder.attempts].reverse().find((item) => item.question_id === question.id);
        if (attempt?.reasoning_status && REASONING_STATUSES.has(attempt.reasoning_status)) {
          reasoningStatus = attempt.reasoning_status;
          reasoningFollowUp = attempt.reasoning_follow_up || "";
        }
        if (attempt?.reasoning_status === "accepted") clearReasoningOperationFor(question.id);
        else if (operation) saveReasoningOperationFor(question.id, { ...operation, expected_version: currentLadder.version });
        const responseOperation = readMasteryLadderOperation("response", currentUserId, classId, currentLadder.ladder_id, question.id);
        if (["submitted", "completed"].includes(attempt?.state) && attempt.rubric_status === "failed"
          && responseOperation?.question_id === question.id && validDraftValue(question, "answer", responseOperation.answer)) {
          const refreshedOperation = { ...responseOperation, expected_version: currentLadder.version };
          drafts.set(question.id, responseOperation.answer);
          pendingResponseOperation = { ...refreshedOperation, key: keyForQuestion(question.id) };
          saveMasteryLadderOperation("response", currentUserId, classId, currentLadder.ladder_id, question.id, refreshedOperation);
        }
      }
      statusMessage = announcement;
    } catch (error) {
      syncRequired = true;
      statusMessage = error.message || "The latest ladder state could not be loaded.";
    }
    renderPlayer();
  }

  async function skipCurrentConcept() {
    if (!selectedConceptId || !currentLadder) return;
    const confirmed = await openDialog({
      title: "Skip this concept?",
      message: "Skipping ends this concept's ladder without recording an incorrect answer. Other selected concepts can continue.",
      confirmLabel: "Skip concept",
      danger: true
    });
    if (!confirmed) return;
    const button = root.querySelector("[data-skip-ladder-concept]");
    if (button) button.disabled = true;
    const skipKey = `${currentLadder.ladder_id}:${selectedConceptId}`;
    if (!pendingSkipOperation || pendingSkipOperation.key !== skipKey) {
      const saved = readMasteryLadderOperation("skip", currentUserId, classId, currentLadder.ladder_id, selectedConceptId);
      pendingSkipOperation = saved
        ? { ...saved, key: skipKey }
        : { key: skipKey, operation_id: requestId(), expected_version: currentLadder.version };
    }
    saveMasteryLadderOperation("skip", currentUserId, classId, currentLadder.ladder_id, selectedConceptId, pendingSkipOperation);
    statusMessage = "Saving skip…";
    syncRequired = true;
    renderPlayer();
    try {
      await skipMasteryLadderConceptApi(currentLadder.ladder_id, selectedConceptId, {
        operation_id: pendingSkipOperation.operation_id,
        expected_version: pendingSkipOperation.expected_version
      });
      const skippedQuestion = currentQuestion();
      if (skippedQuestion) {
        clearMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, skippedQuestion.id, "answer");
        clearMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, skippedQuestion.id, "reasoning");
        drafts.delete(skippedQuestion.id);
        reasoningDrafts.delete(skippedQuestion.id);
        clearReasoningOperationFor(skippedQuestion.id);
      }
      clearMasteryLadderOperation("skip", currentUserId, classId, currentLadder.ladder_id, selectedConceptId);
      pendingSkipOperation = null;
      statusMessage = "Skip saved. Refreshing your progression…";
      await refreshLadder(statusMessage);
    } catch (error) {
      statusMessage = error.message || "This concept could not be skipped. Try again.";
      if (error.code === "version_conflict") {
        clearMasteryLadderOperation("skip", currentUserId, classId, currentLadder.ladder_id, selectedConceptId);
        pendingSkipOperation = null;
      }
      syncRequired = true;
      renderPlayer();
      await refreshLadder(statusMessage);
    }
  }

  async function submitReasoning(retry) {
    const question = currentQuestion();
    if (!question || !selectedConceptId || currentLadder.grading_policy !== "reasoning_required") return;
    const stored = readReasoningOperationFor(question.id);
    if (retry && !stored) {
      statusMessage = "The retry ID is unavailable. Submit the saved reasoning again to start a fresh check.";
      reasoningStatus = "failed";
      renderPlayer();
      return;
    }
    const op = stored || { operation_id: requestId(), expected_version: currentLadder.version };
    const text = reasoningDraft || root.querySelector("[data-ladder-reasoning]")?.value || "";
    if (!retry && !text.trim()) {
      root.querySelector("[data-ladder-reasoning]")?.focus();
      return;
    }
    if (!retry) saveReasoningOperationFor(question.id, op);
    const button = root.querySelector(retry ? "[data-retry-ladder-reasoning]" : "[data-submit-ladder-reasoning]");
    if (button) button.disabled = true;
    syncRequired = true;
    reasoningStatus = "pending";
    renderPlayer();
    const statusElement = root.querySelector("[data-ladder-reasoning-status]");
    if (statusElement) statusElement.textContent = retry ? "Retrying the reasoning check…" : "Checking your reasoning…";
    try {
      const result = retry
        ? await retryMasteryLadderReasoningApi(currentLadder.ladder_id, selectedConceptId, op)
        : await submitMasteryLadderReasoningApi(currentLadder.ladder_id, selectedConceptId, { ...op, text });
      const reasoningResult = normalizeLadderReasoningResult(result);
      reasoningStatus = reasoningResult.status;
      reasoningFollowUp = reasoningResult.follow_up;
      if (reasoningStatus !== "failed" && reasoningDrafts.get(question.id) === text) {
        clearMasteryLadderDraft(currentUserId, classId, currentLadder.ladder_id, question.id, "reasoning");
        reasoningDrafts.delete(question.id);
        reasoningDraft = "";
      }
      statusMessage = reasoningStatus === "accepted" ? "Reasoning accepted. You can submit your answer."
        : reasoningStatus === "insufficient" ? "Add another explanation to continue."
          : reasoningStatus === "pending" ? "The reasoning check is pending. Retry when ready."
            : "The reasoning check failed. Your answer is unchanged; retry the check.";
      const serverVersion = reasoningResult.version;
      if (Number.isSafeInteger(serverVersion)) {
        currentLadder.version = serverVersion;
        if (["pending", "failed", "unavailable"].includes(reasoningStatus)) {
          saveReasoningOperationFor(question.id, { ...op, expected_version: serverVersion });
        }
      }
      if (["accepted", "insufficient"].includes(reasoningStatus)) clearReasoningOperationFor(question.id);
      await refreshLadder(statusMessage);
    } catch (error) {
      reasoningStatus = "failed";
      statusMessage = error.code === "version_conflict"
        ? "This ladder changed in another request. Refreshing its state; your reasoning draft remains in this page."
        : error.message || "Reasoning could not be checked. Your draft remains available.";
      syncRequired = true;
      renderPlayer();
      await refreshLadder(statusMessage);
    }
  }

  async function loadLadder(ladderId) {
    currentLadder = normalizeMasteryLadder(await getMasteryLadderApi(ladderId));
    selectedConceptId = currentLadder.concepts.find((item) => item.state === "active")?.concept_id || currentLadder.concepts[0]?.concept_id || null;
    renderPlayer();
  }

  async function loadConcepts() {
    try {
      concepts = normalizeLadderConceptChoices(await getClassConceptGraphApi(classId));
      const pending = readPendingCreate(currentUserId, classId);
      if (pending) {
        try {
          const created = normalizeMasteryLadder(await createMasteryLadderApi(pending.payload));
          clearPendingCreate(currentUserId, classId);
          writeStoredLadder(currentUserId, classId, created.ladder_id);
          await loadLadder(created.ladder_id);
          return;
        } catch (error) {
          pendingCreate = pending;
          statusMessage = `Your previous ladder request can be retried: ${error.message || "the server could not confirm it"}`;
        }
      }
      const storedId = readStoredLadder(currentUserId, classId);
      if (storedId) {
        try { await loadLadder(storedId); return; }
        catch (error) {
          statusMessage = `The saved ladder could not be resumed: ${error.message || "the server did not return its current state"}`;
          root.innerHTML = `<div class="panel empty-state" role="alert"><strong>Ladder could not be resumed</strong><span>${escapeHtml(statusMessage)}</span><button type="button" class="btn btn-secondary btn-small" data-ladder-resume-retry>Retry resume</button><button type="button" class="btn btn-ghost btn-small" data-ladder-forget>Start a new ladder</button></div>`;
          root.querySelector("[data-ladder-resume-retry]")?.addEventListener("click", () => void loadLadder(storedId).catch((retryError) => {
            statusMessage = retryError.message || "The ladder could not be resumed.";
            root.querySelector("[role='alert'] span").textContent = statusMessage;
          }));
          root.querySelector("[data-ladder-forget]")?.addEventListener("click", () => {
            removeStoredLadder(currentUserId, classId);
            statusMessage = "The previous ladder remains saved on the server.";
            renderForm();
          });
          return;
        }
      }
      renderForm();
    } catch (error) {
      root.innerHTML = `<div class="panel empty-state" role="alert"><strong>Mastery Ladder unavailable</strong><span>${escapeHtml(error.message || "Validated class concepts could not be loaded.")}</span><button class="btn btn-secondary btn-small" type="button" data-ladder-concepts-retry>Try again</button></div>`;
      root.querySelector("[data-ladder-concepts-retry]")?.addEventListener("click", () => void loadConcepts());
    }
  }

  await loadConcepts();
}
