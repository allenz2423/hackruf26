import test from "node:test";
import assert from "node:assert/strict";

import { clearStudyPerformanceScoresApi, createStudyPerformanceScoreApi } from "../src/api.js";
import { studyPerformanceScoreMarkup } from "../src/study-performance-score.js";

test("performance score routes are scoped to the completed study material", async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ overall_score: 80 }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await createStudyPerformanceScoreApi("material/id");
  await clearStudyPerformanceScoresApi("material/id");

  assert.equal(calls[0].url, "/api/study-materials/material%2Fid/performance-score");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[1].options.method, "DELETE");
});

test("score view labels the composite as advisory and makes flashcard weight visible", () => {
  const html = studyPerformanceScoreMarkup({
    overall_score: 84,
    quiz_score: 80,
    exam_score: 90,
    flashcard_score: 50,
    quiz_weight: 0.45,
    exam_weight: 0.45,
    flashcard_weight: 0.1,
    evidence_level: "moderate",
    explanation: "Recent performance is consistent.",
    quiz_attempts: 2,
    exam_attempts: 1,
    flashcards_rated: 12
  });
  assert.match(html, /AI study score for this class · advisory only/);
  assert.match(html, /across this class/);
  assert.match(html, /Flashcard practice · 10% of this composite/);
  assert.match(html, /not a course grade or mastery estimate/);
  assert.match(html, /Recent performance is consistent\./);
});

test("flashcard-only output shows a practice signal without inventing a composite", () => {
  const html = studyPerformanceScoreMarkup({
    overall_score: null,
    quiz_score: null,
    exam_score: null,
    flashcard_score: 50,
    quiz_weight: 0,
    exam_weight: 0,
    flashcard_weight: 0,
    evidence_level: "low",
    explanation: "Cards are a practice signal.",
    quiz_attempts: 0,
    exam_attempts: 0,
    flashcards_rated: 2
  });
  assert.match(html, /Practice signal/);
  assert.match(html, /No quiz or exam evidence yet/);
  assert.match(html, /practice signal only/);
  assert.doesNotMatch(html, /<script>/);
});
