import assert from "node:assert/strict";
import test from "node:test";

import {
  createMasteryLadderApi,
  getClassConceptGraphApi,
  getMasteryLadderApi,
  retryMasteryLadderReasoningApi,
  skipMasteryLadderConceptApi,
  submitMasteryLadderReasoningApi,
  submitMasteryLadderResponseApi
} from "../src/api.js";
import {
  clearMasteryLadderDraft,
  MAX_LADDER_CONCEPT_SELECTIONS,
  masteryLadderConceptPickerState,
  masteryLadderProgressMessage,
  masteryLadderMarkup,
  normalizeLadderReasoningResult,
  normalizeLadderConceptChoices,
  normalizeMasteryLadder,
  readMasteryLadderOperation,
  readMasteryLadderDraft,
  saveMasteryLadderOperation,
  saveMasteryLadderDraft
} from "../src/mastery-ladder.js";

class MemoryStorage {
  #values = new Map();
  get length() { return this.#values.size; }
  key(index) { return [...this.#values.keys()][index] ?? null; }
  getItem(key) { return this.#values.get(String(key)) ?? null; }
  setItem(key, value) { this.#values.set(String(key), String(value)); }
  removeItem(key) { this.#values.delete(String(key)); }
}

function question(overrides = {}) {
  return {
    id: "run-q-opaque",
    prompt: "Which part directs cell activities?",
    level: 1,
    kind: "multiple_choice",
    choices: [{ id: "opaque-a", text: "Nucleus" }, { id: "opaque-b", text: "Ribosome" }],
    interaction: { response_type: "multiple_choice" },
    answer_key: "must not render",
    ...overrides
  };
}

function ladder(overrides = {}) {
  return {
    ladder_id: "ladder-1",
    status: "active",
    grading_policy: "objective_only",
    version: 3,
    concepts: [{
      concept_id: "concept-1", label: "Cell structures", state: "active",
      current_level: 1, recovery_state: "not_offered", current_question: question()
    }],
    attempts: [],
    ...overrides
  };
}

test("mastery ladder API wrappers use owner-scoped same-origin endpoints and opaque IDs", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path: String(path), options });
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  };
  await getClassConceptGraphApi("class/1");
  await createMasteryLadderApi({ operation_id: "op", class_id: "class/1", concept_ids: ["concept-1"], language: "en", grading_policy: "objective_only" });
  await getMasteryLadderApi("ladder/2");
  await submitMasteryLadderResponseApi("ladder/2", "concept/3", { operation_id: "response-op", question_id: "q/4", answer: "option/5", expected_version: 7 });
  await skipMasteryLadderConceptApi("ladder/2", "concept/3", { operation_id: "skip-op", expected_version: 8 });
  await submitMasteryLadderReasoningApi("ladder/2", "concept/3", { operation_id: "reason-op", text: "My approach", expected_version: 9 });
  await retryMasteryLadderReasoningApi("ladder/2", "concept/3", { operation_id: "reason-op", expected_version: 9 });

  assert.deepEqual(requests.map(({ path }) => new URL(path, "https://jot.test").pathname), [
    "/api/classes/class%2F1/concept-graph",
    "/api/mastery-ladders",
    "/api/mastery-ladders/ladder%2F2",
    "/api/mastery-ladders/ladder%2F2/concepts/concept%2F3/response",
    "/api/mastery-ladders/ladder%2F2/concepts/concept%2F3/skip",
    "/api/mastery-ladders/ladder%2F2/concepts/concept%2F3/reasoning",
    "/api/mastery-ladders/ladder%2F2/concepts/concept%2F3/reasoning/retry"
  ]);
  assert.deepEqual(JSON.parse(requests[3].options.body), { operation_id: "response-op", question_id: "q/4", answer: "option/5", expected_version: 7 });
  assert.deepEqual(JSON.parse(requests[4].options.body), { operation_id: "skip-op", expected_version: 8 });
  assert.deepEqual(JSON.parse(requests[5].options.body), { operation_id: "reason-op", text: "My approach", expected_version: 9 });
  assert.deepEqual(JSON.parse(requests[6].options.body), { operation_id: "reason-op", expected_version: 9 });
});

test("ladder DTO projection is safe and drops answer keys, rubric, and untrusted fields", () => {
  const normalized = normalizeMasteryLadder(ladder({
    answer_key: "private",
    concepts: [{
      concept_id: "concept-1", label: "Cell structures", state: "active", current_level: 1,
      recovery_state: "available", current_question: question(), hidden_rubric: "private"
    }],
    attempts: [{ question_id: "run-q-opaque", state: "submitted", answer_status: "saved", correctness: null, version: 1, xp_awarded: 5, reasoning_status: null, answer_text: "do not project" }]
  }));
  assert.equal(normalized.concepts[0].current_question.choices[0].id, "opaque-a");
  assert.equal(normalized.concepts[0].hidden_rubric, undefined);
  assert.equal(normalized.concepts[0].current_question.answer_key, undefined);
  assert.equal(normalized.answer_key, undefined);
  assert.equal(normalized.attempts[0].answer_text, undefined);
  assert.equal(normalized.attempts[0].xp_awarded, 5);
  assert.equal(JSON.stringify(normalized).includes("must not render"), false);
  assert.equal(JSON.stringify(normalized).includes("private"), false);
});

test("only validated concepts are selectable for ladders", () => {
  assert.deepEqual(normalizeLadderConceptChoices({ concepts: [
    { id: "valid", canonical_name: "Validated", status: "validated" },
    { id: "draft", canonical_name: "Draft", status: "proposed" },
    { id: "rejected", canonical_name: "Rejected", state: "rejected" }
  ] }), [{ concept_id: "valid", label: "Validated" }]);
});

test("mastery ladder picker filters case-insensitively and keeps hidden selections counted", () => {
  const concepts = Array.from({ length: 20 }, (_, index) => ({
    concept_id: `concept-${index}`,
    label: index < 3 ? `Amino acid concept ${index}` : `Cell concept ${index}`
  }));
  const state = masteryLadderConceptPickerState(concepts, ["concept-0", "concept-18"], "AMINO");
  assert.deepEqual(state.visibleConcepts.map((concept) => concept.concept_id), ["concept-0", "concept-1", "concept-2"]);
  assert.equal(state.selectedCount, 2);
  assert.equal(state.visibleUnselectedCount, 2);
  assert.equal(state.canSelectVisible, true);
});

test("mastery ladder picker does not bulk-select beyond the server's twelve-concept limit", () => {
  const concepts = Array.from({ length: MAX_LADDER_CONCEPT_SELECTIONS + 1 }, (_, index) => ({
    concept_id: `concept-${index}`,
    label: `Concept ${index}`
  }));
  const allVisible = masteryLadderConceptPickerState(concepts, [], "");
  assert.equal(allVisible.canSelectVisible, false);
  assert.equal(allVisible.visibleUnselectedCount, MAX_LADDER_CONCEPT_SELECTIONS + 1);

  const narrowed = masteryLadderConceptPickerState(concepts, [], "Concept 0");
  assert.equal(narrowed.visibleConcepts.length, 1);
  assert.equal(narrowed.canSelectVisible, true);

  const atLimit = masteryLadderConceptPickerState(concepts, concepts.slice(0, MAX_LADDER_CONCEPT_SELECTIONS).map(({ concept_id }) => concept_id), "Concept 12");
  assert.equal(atLimit.selectedCount, MAX_LADDER_CONCEPT_SELECTIONS);
  assert.equal(atLimit.canSelectVisible, false);
});

test("reasoning-required ladder holds final submission until accepted and escapes prompt", () => {
  const dto = ladder({
    grading_policy: "reasoning_required",
    concepts: [{
      concept_id: "concept-1", label: "Cell structures", state: "active",
      current_level: 1, recovery_state: "not_offered", current_question: question({ prompt: "<img src=x>" })
    }]
  });
  const blocked = masteryLadderMarkup(normalizeMasteryLadder(dto), "concept-1");
  assert.match(blocked, /&lt;img src=x&gt;/);
  assert.match(blocked, /disabled>Submit answer/);
  assert.match(blocked, /separate reasoning-quality check/);
  const accepted = masteryLadderMarkup(normalizeMasteryLadder(dto), "concept-1", {}, "accepted", "My reasoning");
  assert.match(accepted, /type="submit" data-submit-ladder-answer >Submit answer/);
  assert.match(accepted, /Reasoning accepted/);
  assert.doesNotMatch(accepted, /must not render|hidden_rubric/);
});

test("reasoning sufficiency preserves targeted follow-ups independently from rubric results", () => {
  const dto = ladder({
    grading_policy: "reasoning_required",
    attempts: [{
      question_id: "run-q-opaque", state: "completed", answer_status: "saved", correctness: null,
      version: 2, xp_awarded: null, reasoning_status: "accepted", reasoning_follow_up: null,
      rubric_status: "failed", rubric_result: null
    }]
  });
  const normalized = normalizeMasteryLadder(dto);
  assert.equal(normalized.attempts[0].reasoning_status, "accepted");
  assert.equal(normalized.attempts[0].rubric_status, "failed");
  const retryMarkup = masteryLadderMarkup(normalized, "concept-1", {}, null, "", false);
  assert.match(retryMarkup, /reasoning rubric grading failed/);
  assert.match(retryMarkup, /data-retry-ladder-response/);
  assert.doesNotMatch(retryMarkup, /data-retry-ladder-reasoning/);

  const insufficient = normalizeLadderReasoningResult({
    status: "insufficient", follow_up: "What do you think each part does?", version: 4, attempt_version: 2,
    private_rubric: "must not project"
  });
  assert.deepEqual(insufficient, {
    status: "insufficient", follow_up: "What do you think each part does?", version: 4, attempt_version: 2
  });
  const followUpMarkup = masteryLadderMarkup(
    normalizeMasteryLadder(ladder({ grading_policy: "reasoning_required" })),
    "concept-1", {}, insufficient.status, "My attempt", false, insufficient.follow_up
  );
  assert.match(followUpMarkup, /Try this next:/);
  assert.match(followUpMarkup, /What do you think each part does\?/);
  assert.match(followUpMarkup, /Add to my reasoning/);
  assert.doesNotMatch(followUpMarkup, /must not project/);
  assert.throws(() => normalizeLadderReasoningResult({ status: "insufficient", follow_up: " " }), /usable follow-up/);
  assert.throws(() => normalizeMasteryLadder(ladder({
    grading_policy: "reasoning_required",
    attempts: [{ question_id: "run-q-opaque", state: "in_progress", answer_status: "draft", correctness: null,
      version: 0, reasoning_status: "insufficient", reasoning_follow_up: null }]
  })), /attempt could not be safely displayed/);
});

test("pending reasoning locks edits until a server refresh and recovery is identified separately", () => {
  const dto = ladder({
    grading_policy: "reasoning_required",
    attempts: [{ question_id: "run-q-opaque", state: "pending", answer_status: "draft", correctness: null, version: 4, reasoning_status: "pending" }],
    concepts: [{
      concept_id: "concept-1", label: "Cell structures", state: "active", current_level: 1,
      recovery_state: "available", current_question: question()
    }]
  });
  const pending = masteryLadderMarkup(normalizeMasteryLadder(dto), "concept-1", {}, null, "saved explanation", true);
  assert.match(pending, /data-ladder-reasoning rows="3" maxlength="5000" disabled/);
  assert.match(pending, /data-retry-ladder-reasoning disabled/);
  assert.match(pending, /separate recovery question is available for this concept/);
  assert.match(pending, /data-submit-ladder-answer disabled/);
  const retryable = masteryLadderMarkup(normalizeMasteryLadder(dto), "concept-1", {}, null, "saved explanation", false);
  assert.match(retryable, /data-retry-ladder-reasoning >Retry reasoning check/);
  assert.match(retryable, /data-ladder-reasoning rows="3" maxlength="5000" disabled/);
});

test("ladder progress messages only describe allowlisted server progression and XP", () => {
  assert.equal(masteryLadderProgressMessage({ progression: "advanced", xp_awarded: 4 }), "Level passed. Your next level is ready. 4 XP recorded by the server.");
  assert.match(masteryLadderProgressMessage({ progression: "recovery_offered", xp_awarded: "999" }), /separate recovery question is ready/);
  assert.match(masteryLadderProgressMessage({ progression: "unavailable", correctness: "correct", xp_awarded: -5 }), /no additional failure was recorded/);
  assert.doesNotMatch(masteryLadderProgressMessage({ progression: "unexpected", explanation: "secret answer", xp_awarded: 0 }), /secret answer|correct/);
});

test("invalid progression DTOs fail closed", () => {
  assert.throws(() => normalizeMasteryLadder(ladder({ concepts: [{ concept_id: "c", label: "C", state: "active", current_level: 5, recovery_state: "not_offered", current_question: null }] })), /safely displayed/);
  assert.throws(() => normalizeMasteryLadder(ladder({ grading_policy: "client_scored" })), /expected format/);
});

test("ladder drafts persist by user, class, ladder, question, and draft kind until cleared", (t) => {
  const originalStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = new MemoryStorage();
  t.after(() => { globalThis.sessionStorage = originalStorage; });
  const q = question();
  assert.equal(saveMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "answer", "opaque-b"), true);
  assert.equal(saveMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "reasoning", "My explanation"), true);
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "answer"), "opaque-b");
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "reasoning"), "My explanation");
  assert.equal(readMasteryLadderDraft("user-2", "class-1", "ladder-1", q, "answer"), "");
  assert.equal(readMasteryLadderDraft("user-1", "class-2", "ladder-1", q, "answer"), "");
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-2", q, "answer"), "");
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-1", question({ id: "other-question" }), "answer"), "");
  clearMasteryLadderDraft("user-1", "class-1", "ladder-1", q.id, "answer");
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "answer"), "");
  assert.equal(readMasteryLadderDraft("user-1", "class-1", "ladder-1", q, "reasoning"), "My explanation");
});

test("ladder draft persistence enforces bounded values and valid opaque answer choices", (t) => {
  const originalStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = new MemoryStorage();
  t.after(() => { globalThis.sessionStorage = originalStorage; });
  const q = question();
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "answer", "not-an-option"), false);
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "answer", "a".repeat(10_001)), false);
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "reasoning", "r".repeat(5_001)), false);
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "unknown", "value"), false);
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "answer", "opaque-a"), true);
  assert.equal(readMasteryLadderDraft("user", "class", "ladder", q, "answer"), "opaque-a");
  assert.equal(saveMasteryLadderDraft("user", "class", "ladder", q, "reasoning", "x".repeat(5_000)), true);
  assert.equal(readMasteryLadderDraft("user", "class", "ladder", q, "reasoning").length, 5_000);
});

test("restored answer and reasoning drafts render safely without triggering submission", (t) => {
  const originalStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = new MemoryStorage();
  t.after(() => { globalThis.sessionStorage = originalStorage; });
  const q = question();
  saveMasteryLadderDraft("user", "class", "ladder", q, "answer", "opaque-a");
  saveMasteryLadderDraft("user", "class", "ladder", q, "reasoning", "<script>draft</script>");
  const markup = masteryLadderMarkup(normalizeMasteryLadder(ladder({ grading_policy: "reasoning_required" })), "concept-1", {
    [q.id]: readMasteryLadderDraft("user", "class", "ladder", q, "answer")
  }, null, readMasteryLadderDraft("user", "class", "ladder", q, "reasoning"));
  assert.match(markup, /value="opaque-a" checked/);
  assert.match(markup, /&lt;script&gt;draft&lt;\/script&gt;/);
  assert.match(markup, /disabled>Submit answer/);
  assert.doesNotMatch(markup, /<script>draft<\/script>/);
});

test("draft storage prunes old entries above its per-user count bound", (t) => {
  const originalStorage = globalThis.sessionStorage;
  const storage = new MemoryStorage();
  globalThis.sessionStorage = storage;
  t.after(() => { globalThis.sessionStorage = originalStorage; });
  for (let index = 0; index < 105; index += 1) {
    assert.equal(saveMasteryLadderDraft("user", "class", "ladder", question({ id: `q-${index}` }), "answer", "opaque-a"), true);
  }
  assert.equal(storage.length, 100);
  assert.equal(readMasteryLadderDraft("user", "class", "ladder", question({ id: "q-104" }), "answer"), "opaque-a");
  assert.equal(readMasteryLadderDraft("user", "class", "ladder", question({ id: "q-0" }), "answer"), "");
});

test("reasoning, response, and skip operation IDs survive retries with bounded session storage", (t) => {
  const originalStorage = globalThis.sessionStorage;
  const storage = new MemoryStorage();
  globalThis.sessionStorage = storage;
  t.after(() => { globalThis.sessionStorage = originalStorage; });

  saveMasteryLadderOperation("reasoning", "user", "class", "ladder", "q1", { operation_id: "stable-reasoning-op", expected_version: 3 });
  saveMasteryLadderOperation("response", "user", "class", "ladder", "q1", { operation_id: "stable-answer-op", expected_version: 3, question_id: "q1", answer: "choice-a" });
  saveMasteryLadderOperation("skip", "user", "class", "ladder", "concept1", { operation_id: "stable-skip-op", expected_version: 3 });

  assert.equal(readMasteryLadderOperation("reasoning", "user", "class", "ladder", "q1").operation_id, "stable-reasoning-op");
  assert.equal(readMasteryLadderOperation("response", "user", "class", "ladder", "q1").answer, "choice-a");
  assert.equal(readMasteryLadderOperation("skip", "user", "class", "ladder", "concept1").expected_version, 3);
  assert.equal(readMasteryLadderOperation("skip", "another-user", "class", "ladder", "concept1"), null);
  assert.equal(readMasteryLadderOperation("unknown", "user", "class", "ladder", "q1"), null);

  // A fresh GET may advance the expected version without changing the operation ID.
  saveMasteryLadderOperation("reasoning", "user", "class", "ladder", "q1", { operation_id: "stable-reasoning-op", expected_version: 4 });
  assert.deepEqual(
    { operation_id: readMasteryLadderOperation("reasoning", "user", "class", "ladder", "q1").operation_id,
      expected_version: readMasteryLadderOperation("reasoning", "user", "class", "ladder", "q1").expected_version },
    { operation_id: "stable-reasoning-op", expected_version: 4 }
  );
  saveMasteryLadderOperation("response", "user", "class", "ladder", "oversized", { operation_id: "op", expected_version: 0, answer: "a".repeat(13_000) });
  assert.equal(readMasteryLadderOperation("response", "user", "class", "ladder", "oversized"), null);
  assert.ok(storage.length <= 3);
});
