import assert from "node:assert/strict";
import test from "node:test";

import { LiveSessionController } from "../src/live-session.js";
import { MaterialStage, MAX_PAGE_DIMENSION } from "../src/material-stage.js";
import { citedPdfUrl, uploadDocumentSectionsHtml } from "../src/file-preview.js";
import { quizTutorContext, readQuizTutorContext } from "../src/quiz-tutor-context.js";
import { renderQuestionInput, renderQuizPlayer } from "../src/quiz-player.js";
import { sourceCitationHtml, sourceWarningsHtml } from "../src/source-citations.js";
import { submitReasoningApi } from "../src/api.js";
import { defaultPracticeTestTimeLimit } from "../src/study.js";

function globals(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
}

test("Reasoning Mode API mutations require and send the current attempt version", async (t) => {
  const requests = [];
  globals(t, {
    fetch: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 200, text: async () => "{}" };
    }
  });

  await assert.rejects(
    submitReasoningApi("material", "run", "question", {
      operationId: "operation", text: "A thought", expectedVersion: undefined
    }),
    /current question attempt version is required/
  );
  assert.equal(requests.length, 0);

  await submitReasoningApi("material", "run", "question", {
    operationId: "operation", text: "A thought", expectedVersion: 7
  });
  assert.equal(requests[0].body.expected_version, 7);
});

test("free-response run questions render an editable text answer", () => {
  const html = renderQuestionInput({ kind: "free_response" }, 3);
  assert.match(html, /<textarea/);
  assert.match(html, /name="question-3"/);
  assert.match(html, /data-answer-text/);
});

test("congested frame samples are dropped and the next current sample can send", (t) => {
  globals(t, { WebSocket: { OPEN: 1 } });
  const sent = [];
  const controller = new LiveSessionController();
  controller.socket = { readyState: 1, bufferedAmount: 4_000_001, send: (data) => sent.push(JSON.parse(data)) };
  const frame = { frame_id: "fixture", jpeg: "data:image/jpeg;base64,AAAA" };
  assert.equal(controller.sendFrame(frame), false);
  controller.socket.bufferedAmount = 3_999_999;
  assert.equal(controller.sendFrame(frame), false);
  assert.deepEqual(sent, []);
  controller.socket.bufferedAmount = 0;
  assert.equal(controller.sendFrame(frame), true);
  assert.deepEqual(sent, [{ type: "frame", frame_id: "fixture", data: "AAAA" }]);
  assert.equal(controller.failed, false);
});

test("only unique finalized student transcript events are forwarded", () => {
  const received = [];
  const controller = new LiveSessionController({ onStudentTranscript: (turn) => received.push(turn) });
  const turn = {
    type: "student_transcript",
    transcript_id: "91ebed9c-d73a-45fd-9aa4-e1dcf52b99e0",
    text: "I think the kernel handles hardware."
  };
  controller.handleMessage(JSON.stringify({ type: "interim_student_transcript", ...turn }));
  controller.handleMessage(JSON.stringify({ type: "assistant", text: "The kernel is...", turn_complete: true }));
  controller.handleMessage(JSON.stringify(turn));
  controller.handleMessage(JSON.stringify(turn));
  assert.deepEqual(received, [{
    transcriptId: turn.transcript_id,
    text: turn.text
  }]);
});

test("the material stage renders PDF pages and images at 1920", async (t) => {
  const context = { fillRect() {}, drawImage() {} };
  globals(t, { document: { createElement: () => ({ width: 0, height: 0, getContext: () => context }) } });
  const stage = new MaterialStage({ name: "Lecture.pdf" });
  stage.pdf = { getPage: async () => ({
    getViewport: ({ scale }) => ({ width: 612 * scale, height: 792 * scale }),
    render: () => ({ promise: Promise.resolve() }), cleanup() {}
  }) };
  await stage.showPage(0);
  assert.equal(Math.max(stage.canvas.width, stage.canvas.height), 1920);
  stage.pdf = null;
  stage.image = { width: 3840, height: 2160 };
  await stage.showPage(0);
  assert.deepEqual([stage.canvas.width, stage.canvas.height], [1920, 1080]);
  assert.equal(MAX_PAGE_DIMENSION, 1920);
});

test("quiz handoff includes choices, the attempted answer, and deduplicated source IDs", () => {
  const context = quizTutorContext({ prompt: "Choose a carbohydrate", choices: [
    { id: "a", text: "Glucose" }, { id: "b", text: "Water" }
  ], sources: [{ upload_id: "source-1" }] }, "b", ["source-1", "source-2"]);
  assert.match(context.text, /a: Glucose\nb: Water/);
  assert.match(context.text, /My attempted answer: b/);
  assert.deepEqual(context.uploadIds, ["source-1", "source-2"]);
  assert.deepEqual(readQuizTutorContext(JSON.stringify(context)), context);
  assert.deepEqual(readQuizTutorContext("Old queued question"), { text: "Old queued question", uploadIds: [] });
  assert.equal(readQuizTutorContext(null), null);
});

test("citations are accessible buttons with escaped labels and locator data", () => {
  const html = sourceCitationHtml({ upload_id: "fixture", name: '<img src=x onerror="x">', page: 5 });
  assert.match(html, /<button type="button"/);
  assert.match(html, /data-source-citation=/);
  assert.ok(!html.includes("<img"));
  assert.match(html, /p\. 5/);
  assert.match(sourceCitationHtml("Legacy note"), /<span/);
  assert.equal(citedPdfUrl("blob:fixture", { page: 5 }), "blob:fixture#page=5");
  assert.equal(citedPdfUrl("blob:fixture#old", { page: 2 }), "blob:fixture#page=2");
  assert.equal(citedPdfUrl("blob:fixture", { page: -1 }), "blob:fixture");
});

test("saved upload document previews format math and escape generated text", () => {
  const html = uploadDocumentSectionsHtml([{
    heading: "Carbohydrates $C_6H_{12}O_6$",
    body: "Glucose contains $\\alpha$ bonds.\nA second line.\n\nUnsafe <img src=x onerror=alert(1)>",
    key_terms: ["monosaccharide"],
    sources: []
  }], "BIO 170.pdf");
  assert.match(html, /class="tex-math/);
  assert.match(html, /<div class="guide-section-body"><p>/);
  assert.match(html, /A second line\.<\/p>/);
  assert.match(html, /Key terms:<\/strong> monosaccharide/);
  assert.ok(!html.includes("<img src=x"));
  assert.match(html, /&lt;img src=x/);
});

test("larger practice tests receive proportionally longer default timers", () => {
  assert.equal(defaultPracticeTestTimeLimit(20), 15);
  assert.equal(defaultPracticeTestTimeLimit(100), 75);
});

test("long quiz handoffs stay within the backend's 5000-character text limit", () => {
  const context = quizTutorContext({ prompt: "Q".repeat(5000), choices: Array.from({ length: 6 }, (_, index) => ({ id: String(index), text: "C".repeat(1000) })) }, "A".repeat(2000));
  assert.ok(context.text.length <= 5000);
  assert.match(context.text, /Long question text shortened/);
  assert.match(context.text, /My attempted answer:/);
});

test("saved source warnings distinguish changed, missing, and excerpted uploads", () => {
  const html = sourceWarningsHtml({ sourceWarnings: ["changed", "missing", "truncated"] });
  assert.match(html, /have changed/);
  assert.match(html, /were deleted/);
  assert.match(html, /may not cover every page/);
});

test("remote quizzes start in Standard mode and offer an isolated opt-in Reasoning Mode", async (t) => {
  const start = new Element(), alert = new Element(), retryVoice = new Element();
  alert.hidden = true;
  const selectedMode = { value: "reasoning" };
  const root = {
    innerHTML: "",
    querySelectorAll: () => [],
    querySelector(selector) {
      if (selector === "[data-start-quiz]") return start;
      if (selector === ".quiz-start-error") return alert;
      if (selector === 'input[name="quiz-mode"]:checked') return selectedMode;
      if (selector === "[data-retry-voice]") return retryVoice;
      return new Element();
    }
  };
  const requests = [];
  globals(t, {
    window: {
      sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      addEventListener() {}, removeEventListener() {}, setTimeout() {}, clearTimeout() {},
      confirm: () => false
    },
    fetch: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 201, text: async () => JSON.stringify({
        run_id: "run-fixture", material_id: "material-fixture", mode: "reasoning",
        status: "in_progress",
        feedback_policy: { release: "after_submission", feedback_available: false },
        questions: [{
          id: "run-q1", prompt: "What does the kernel do?", kind: "multiple_choice", level: 1,
          concepts: [{ id: "concept-1", label: "Operating systems" }],
          choices: [{ id: "option-a", text: "Manage hardware" }, { id: "option-b", text: "Display the desktop" }],
          interaction: { response_type: "single_choice" }
        }],
        attempts: [{ question_id: "run-q1", state: "awaiting_reasoning", version: 0 }]
      }) };
    }
  });
  renderQuizPlayer(root, {
    id: "material-fixture", title: "A quiz", type: "Quiz", isRemote: true, questions: []
  });
  assert.match(root.innerHTML, /Standard quiz/);
  assert.match(root.innerHTML, /Reasoning Mode/);
  assert.match(root.innerHTML, /value="standard" checked/);

  await start.events.get("click")();
  assert.equal(requests[0].body.mode, "reasoning");
  assert.match(root.innerHTML, /Explain your thinking/);
  assert.match(root.innerHTML, /value="option-a"/);
  assert.match(root.innerHTML, /type="radio" name="question-0" value="option-a" \/>/);
  assert.match(root.innerHTML, /type="submit" disabled>Submit answer/);
});

test("reopening a completed remote Standard run renders its released review and offers an explicit retake", async (t) => {
  const start = new Element(), alert = new Element();
  alert.hidden = true;
  const selectedMode = { value: "standard" };
  const stored = new Map();
  const root = {
    innerHTML: "",
    querySelectorAll: () => [],
    querySelector(selector) {
      if (selector === "[data-start-quiz]") return start;
      if (selector === ".quiz-start-error") return alert;
      if (selector === 'input[name="quiz-mode"]:checked') return selectedMode;
      return new Element();
    }
  };
  const requests = [];
  globals(t, {
    window: {
      sessionStorage: {
        getItem: (key) => stored.get(key) || null,
        setItem: (key, value) => stored.set(key, value),
        removeItem: (key) => stored.delete(key)
      },
      addEventListener() {}, removeEventListener() {}, setTimeout() {}, clearTimeout() {}
    },
    fetch: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 201, text: async () => JSON.stringify({
        run_id: "run-complete", material_id: "material-fixture", mode: "standard", status: "completed",
        feedback_policy: { release: "after_completion", feedback_available: true },
        questions: [{
          id: "run-q1", prompt: "Choose one", kind: "multiple_choice", level: 1, concepts: [],
          choices: [{ id: "option-a", text: "A" }, { id: "option-b", text: "B" }],
          interaction: { response_type: "single_choice" }
        }],
        attempts: [{
          question_id: "run-q1", state: "completed", final_answer: "option-b", correct: true,
          correct_answer: "B", explanation: "B is correct."
        }],
        score_percent: 100, correct_count: 1
      }) };
    }
  });
  renderQuizPlayer(root, {
    id: "material-fixture", title: "A quiz", type: "Quiz", isRemote: true, questions: []
  });

  await start.events.get("click")();
  assert.equal(requests.length, 1);
  assert.match(root.innerHTML, /100%/);
  assert.match(root.innerHTML, /Answer key:/);
  assert.match(root.innerHTML, /Retake quiz/);
  assert.doesNotMatch(root.innerHTML, /Flag question/);
});

class Element {
  constructor() { this.events = new Map(); this.disabled = false; this.textContent = ""; }
  addEventListener(name, callback) { this.events.set(name, callback); }
  setAttribute() {}
  append() {}
  remove() {}
}

function quizFixture(t, replies) {
  let now = 1000;
  const originalNow = Date.now;
  Date.now = () => now;
  t.after(() => { Date.now = originalNow; });
  const intervals = new Map(), handlers = new Map(), requests = [], attempts = [];
  const input = new Element(), submitButton = new Element(), countdown = new Element();
  const form = new Element();
  form.values = { "question-0": "b" };
  form.querySelector = () => submitButton;
  form.querySelectorAll = () => [input];
  const root = {
    innerHTML: "",
    querySelector: (selector) => selector === "#assessment-form" ? form
      : selector === "#assessment-countdown" ? countdown : new Element(),
    querySelectorAll: () => []
  };
  let nextTimer = 0;
  globals(t, {
    window: {
      setInterval(callback) { const id = ++nextTimer; intervals.set(id, callback); return id; },
      clearInterval(id) { intervals.delete(id); }, setTimeout() {},
      addEventListener(name, callback) { handlers.set(name, callback); },
      removeEventListener(name) { handlers.delete(name); }
    },
    document: { querySelector: () => new Element(), createElement: () => new Element(), body: new Element() },
    FormData: class {
      constructor(target) { this.values = input.disabled ? {} : { ...target.values }; }
      get(name) { return this.values[name] ?? null; }
    },
    fetch: async (path, options) => {
      requests.push({ path, body: options.body ? JSON.parse(options.body) : null });
      const reply = replies.shift() || { ok: false, status: 503, payload: { message: "Unavailable" } };
      return { ok: reply.ok, status: reply.status, text: async () => JSON.stringify(reply.payload) };
    }
  });
  renderQuizPlayer(root, {
    id: "fixture", type: "Practice test", timeLimitMinutes: 1, isRemote: true,
    questions: []
  }, {
    runContext: {
      mode: "standard", runId: "run-fixture", run: {
        run_id: "run-fixture", material_id: "fixture", mode: "standard", status: "in_progress",
        feedback_policy: { release: "after_submission", feedback_available: false },
        questions: [{
          id: "q1", prompt: "Choose one", kind: "multiple_choice", level: 1, concepts: [],
          choices: [{ id: "a", text: "A" }, { id: "b", text: "B" }],
          interaction: { response_type: "single_choice" }
        }],
        attempts: []
      }
    },
    onAttemptCompleted: (attempt) => attempts.push(attempt)
  });
  return {
    form, input, submitButton, countdown, intervals, requests, attempts,
    advance(ms) { now += ms; },
    tick() { [...intervals.values()].forEach((callback) => callback()); },
    submit() { form.events.get("submit")({ preventDefault() {} }); },
    leave() { handlers.get("hashchange")(); }
  };
}

const failed = () => ({ ok: false, status: 503, payload: { message: "Unavailable" } });
const scored = () => ({ ok: true, status: 200, payload: {
  attempt_id: "run-fixture", status: "submitted", feedback_status: "pending"
} });
const completedRun = () => ({ ok: true, status: 200, payload: {
  run_id: "run-fixture", material_id: "fixture", mode: "standard", status: "completed",
  feedback_policy: { release: "after_submission", feedback_available: false },
  questions: [{
    id: "q1", prompt: "Choose one", kind: "multiple_choice", level: 1, concepts: [],
    choices: [{ id: "a", text: "A" }, { id: "b", text: "B" }],
    interaction: { response_type: "single_choice" }
  }],
  attempts: []
} });
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("scoring failure resumes the practice-test timer at its original deadline", async (t) => {
  const f = quizFixture(t, [failed()]);
  f.advance(10_000);
  f.submit();
  await flush();
  assert.equal(f.input.disabled, false);
  assert.equal(f.intervals.size, 1);
  assert.equal(f.countdown.textContent, "00:50");
  f.advance(20_000);
  f.tick();
  assert.equal(f.countdown.textContent, "00:30");
  f.leave();
  assert.equal(f.intervals.size, 0);
});

test("expired scoring failures lock answers and retry the same answer snapshot", async (t) => {
  const f = quizFixture(t, [failed(), scored(), completedRun()]);
  f.advance(60_000);
  f.tick();
  await flush();
  assert.equal(f.input.disabled, true);
  assert.equal(f.intervals.size, 0);
  assert.equal(f.submitButton.textContent, "Retry scoring");
  f.form.values["question-0"] = "a";
  f.submit();
  await flush();
  assert.deepEqual(f.requests.filter((request) => request.body?.answers).map((request) => request.body.answers), [{ q1: "b" }, { q1: "b" }], JSON.stringify(f.requests));
  assert.equal(f.attempts.length, 1);
  assert.equal(f.attempts[0].timeExpired, true);
});

test("navigation does not restart a countdown after a late scoring failure", async (t) => {
  const f = quizFixture(t, [failed()]);
  f.submit();
  f.leave();
  await flush();
  assert.equal(f.intervals.size, 0);
  assert.equal(f.attempts.length, 0);
});
