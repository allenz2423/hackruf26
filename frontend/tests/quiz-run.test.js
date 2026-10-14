import assert from "node:assert/strict";
import test from "node:test";

import { createQuizRunApi, submitAttemptApi } from "../src/api.js";
import { normalizePublicQuizRun, publicRunReview, quizOutcomeMarkup } from "../src/quiz-run.js";
import { formatRemoteMaterial } from "../src/study.js";

function globals(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
}

function runFixture(overrides = {}) {
  return {
    run_id: "run-1",
    material_id: "material-1",
    mode: "standard",
    status: "in_progress",
    feedback_policy: { release: "after_submission", feedback_available: false },
    questions: [{
      id: "run-question-1",
      prompt: "Which component manages hardware?",
      kind: "multiple_choice",
      level: 1,
      concepts: [{ id: "concept-1", label: "Operating systems" }],
      choices: [
        { id: "option-x7", text: "Kernel", is_correct: true },
        { id: "option-p2", text: "Desktop environment", is_correct: false }
      ],
      interaction: { response_type: "single_choice" },
      answer: "option-x7",
      rubric: "Secret rubric",
      explanation: "Secret explanation",
      sources: [{ upload_id: "secret-source" }],
      stable_item_id: "bank-item-42"
    }],
    attempts: [{
      attempt_id: "attempt-1",
      question_id: "run-question-1",
      state: "awaiting_reasoning",
      version: 0,
      correct: true,
      correct_answer: "Kernel",
      reasoning_turns: [],
      evaluation: { status: "available", follow_up: "Add more detail", internal_reason: "secret" }
    }],
    score_percent: 100,
    correct_count: 1,
    question_results: [{ question_id: "run-question-1", correct: true, correct_answer: "Kernel" }],
    internal_rubric: "secret run rubric",
    ...overrides
  };
}

test("remote run projection strips keys, rubrics, explanations, stable IDs, and unreleased grades", () => {
  const run = normalizePublicQuizRun(runFixture(), { materialId: "material-1", mode: "standard" });
  assert.deepEqual(run.questions, [{
    id: "run-question-1",
    prompt: "Which component manages hardware?",
    kind: "multiple_choice",
    choices: [
      { id: "option-x7", text: "Kernel" },
      { id: "option-p2", text: "Desktop environment" }
    ],
    concepts: [{ label: "Operating systems" }],
    interaction: { response_type: "single_choice" },
    level: 1
  }]);
  assert.equal(run.score_percent, undefined);
  assert.equal(run.question_results, undefined);
  assert.equal(run.attempts[0].correct, undefined);
  assert.equal(run.questions[0].concepts[0].id, undefined);
  assert.equal(run.attempts[0].correct_answer, undefined);
  assert.equal(run.attempts[0].evaluation.internal_reason, undefined);
  assert.equal(JSON.stringify(run).includes("Secret rubric"), false);
  assert.equal(JSON.stringify(run).includes("bank-item-42"), false);
  assert.equal(publicRunReview(run).length, 0);
});

test("remote run projection accepts only backend-released feedback fields", () => {
  const dto = runFixture({
    status: "completed",
    feedback_policy: { release: "after_submission", feedback_available: true },
    attempts: [{
      attempt_id: "attempt-1", question_id: "run-question-1", state: "completed", version: 2,
      final_answer: "option-x7", correct: true, correct_answer: "Kernel",
      explanation: "It coordinates hardware and software.",
      reasoning_feedback: "You correctly identified the kernel's role.",
      sources: [{ upload_id: "upload-1", page: 3, answer_key: "strip this" }]
    }],
    score_percent: 100,
    correct_count: 1
  });
  const run = normalizePublicQuizRun(dto, { materialId: "material-1", mode: "standard" });
  assert.equal(run.score_percent, 100);
  assert.equal(run.attempts[0].correct, true);
  assert.equal(run.attempts[0].correct_answer, "Kernel");
  assert.deepEqual(run.attempts[0].sources, [{ upload_id: "upload-1", page: 3 }]);
  assert.equal(JSON.stringify(run).includes("strip this"), false);
  assert.equal(publicRunReview(run)[0].correct, true);
});

test("quiz outcomes render only allowlisted server progress and hide unreleased correctness", () => {
  const hidden = normalizePublicQuizRun(runFixture({
    outcome: { response_count: 1, answered_count: 1, unanswered_count: 0, correct_count: 1, xp_awarded: 8, secret: "do not copy" },
    attempts: [{ question_id: "run-question-1", state: "completed", outcome: { correctness: "correct", xp_awarded: 8 } }]
  }));
  assert.deepEqual(hidden.outcome, { response_count: 1, answered_count: 1, unanswered_count: 0, xp_awarded: 8 });
  assert.deepEqual(hidden.attempts[0].outcome, { xp_awarded: 8 });
  assert.equal(publicRunReview(hidden)[0].outcome.correctness, undefined);
  assert.equal(JSON.stringify(hidden).includes("secret"), false);

  const released = normalizePublicQuizRun(runFixture({
    feedback_policy: { release: "after_completion", feedback_available: true },
    outcome: { response_count: 1, answered_count: 1, unanswered_count: 0, correct_count: 1, xp_awarded: 8 },
    attempts: [{ question_id: "run-question-1", state: "completed", outcome: { correctness: "correct", xp_awarded: 8 } }]
  }));
  assert.equal(released.outcome.correct_count, 1);
  assert.equal(publicRunReview(released)[0].outcome.correctness, "correct");
  assert.equal(quizOutcomeMarkup(hidden.outcome), '<span class="row-meta">8 XP earned</span>');
  assert.equal(quizOutcomeMarkup(hidden.attempts[0].outcome, { question: true }), '<span class="row-meta">8 XP earned for this question</span>');
  assert.equal(quizOutcomeMarkup({ xp_awarded: "8" }), "");
});

test("invalid or mismatched remote run DTOs fail closed", () => {
  assert.throws(() => normalizePublicQuizRun({ ...runFixture(), questions: [] }), /secure format/);
  assert.throws(() => normalizePublicQuizRun(runFixture(), { materialId: "other-material" }), /secure format/);
  assert.throws(() => normalizePublicQuizRun(runFixture({ questions: [{ id: "q", prompt: "Q", kind: "multiple_choice", choices: [] }] })), /cannot be securely displayed/);
});

test("remote material presentation objects never retain question answer data", () => {
  const material = formatRemoteMaterial({
    id: "material-1",
    kind: "quiz",
    status: "ready",
    item_count: 1,
    content: {
      questions: [{
        id: "stable-bank-id", prompt: "Visible prompt", choices: [{ id: "a", text: "One" }],
        answer: "a", rubric: "private rubric", explanation: "private explanation", sources: [{ upload_id: "key-source" }]
      }],
      answer_key: "private answer key"
    }
  }, "class-1", "Quiz");
  assert.equal(material.questions.length, 0);
  assert.equal(material.content, null);
  assert.equal(JSON.stringify(material).includes("private answer key"), false);
  assert.equal(JSON.stringify(material).includes("private rubric"), false);
  assert.equal(JSON.stringify(material).includes("stable-bank-id"), false);
});

test("run creation and submission wrappers use run-scoped IDs and contain no client grading", async (t) => {
  const requests = [];
  globals(t, {
    fetch: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 201, text: async () => JSON.stringify(runFixture()) };
    }
  });

  await createQuizRunApi("material-1", "op-create", "standard");
  await assert.rejects(submitAttemptApi("material-1", { "run-question-1": "option-x7" }), /run and operation ID/);
  await submitAttemptApi("material-1", { "run-question-1": "option-x7" }, {
    runId: "run-1", operationId: "op-submit"
  });

  assert.equal(requests[0].path, "/api/study-materials/material-1/runs");
  assert.deepEqual(requests[0].body, { operation_id: "op-create", mode: "standard" });
  assert.equal(requests[1].path, "/api/study-materials/material-1/attempts");
  assert.deepEqual(requests[1].body, {
    answers: { "run-question-1": "option-x7" }, run_id: "run-1", operation_id: "op-submit"
  });
  assert.equal("score_percent" in requests[1].body, false);
  assert.equal("correct" in requests[1].body, false);
});
