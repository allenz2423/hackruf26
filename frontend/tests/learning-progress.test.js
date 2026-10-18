import assert from "node:assert/strict";
import test from "node:test";

import {
  getLearningConceptApi,
  getLearningPreferencesApi,
  getLearningProgressApi,
  getLearningProgressHistoryApi,
  getLearningRecommendationsApi,
  getDueFlashcardDecksApi,
  updateLearningPreferencesApi,
  updateLearningRecommendationStateApi
} from "../src/api.js";
import {
  learningGoalControlMarkup,
  learningConceptDetailMarkup,
  learningHistoryMarkup,
  learningProgressMarkup,
  learningRecommendationsMarkup,
  dueFlashcardDecksMarkup,
  normalizeLearningConceptDetail,
  normalizeLearningHistory,
  normalizeLearningPreferences,
  normalizeLearningProgress,
  normalizeLearningRecommendations,
  normalizeDueFlashcardDecks,
  startLearningRecommendation
} from "../src/progress.js";

function progressFixture(overrides = {}) {
  return {
    algorithm_version: "jot-beta-mastery-v1",
    as_of: "2026-10-14T12:00:00Z",
    total_xp: 135,
    recent_accuracy: {
      window_days: 30, graded_count: 4, correct_count: 3, incorrect_count: 1,
      unanswered_count: 0, skipped_count: 1, pending_count: 0, rate: 0.75
    },
    concepts: [{
      id: "concept-private-1",
      label: "Cell biology",
      status: "provisional",
      latest_demonstrated_level: 3,
      current_level: null,
      estimate: {
        mean: 0.71, lower_90: 0.38, upper_90: 0.94,
        probability_threshold: 0.62, threshold: 0.8, evidence_count: 5
      },
      evidence_sufficiency: { valid_observations: 4, independent_families: 2, effective_weight: 1.8 },
      historical_peak: 2,
      recent_growth: 0.14,
      last_assessed_at: "2026-10-10T12:00:00Z",
      review_due: true
    }],
    ...overrides
  };
}

test("due flashcard summaries are strictly normalized and open only due cards", (t) => {
  const previousLocation = globalThis.location;
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { href: "https://jot.example/progress?window=30#/progress" }
  });
  t.after(() => previousLocation
    ? Object.defineProperty(globalThis, "location", { configurable: true, value: previousLocation })
    : delete globalThis.location);

  const due = normalizeDueFlashcardDecks({
    items: [{
      material_id: "deck-1", title: "Cell membranes", class_id: "class-1", class_name: "Biology",
      due_count: 3, first_due_at: "2026-10-18T12:00:00Z"
    }],
    has_more: false
  });
  const html = dueFlashcardDecksMarkup(due);
  assert.match(html, /3 cards due/);
  assert.match(html, /href="\/progress\?study_material=deck-1&amp;review_due=1"/);
  assert.match(html, /Biology/);

  assert.throws(() => normalizeDueFlashcardDecks({
    items: [{ material_id: "deck-1", title: "Deck", due_count: 0, first_due_at: "bad" }],
    has_more: false
  }), /expected format/);
  const noisyDto = normalizeDueFlashcardDecks({
    items: [{
      material_id: "deck-1", title: "Deck", class_id: null, class_name: null,
      due_count: 1, first_due_at: "2026-10-18T12:00:00Z", cards: ["secret"]
    }],
    has_more: false
  });
  assert.doesNotMatch(dueFlashcardDecksMarkup(noisyDto), /secret/);
});

test("due flashcard API requests the owner-private queue endpoint", async (t) => {
  const previousFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({ items: [], has_more: false }), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  };
  t.after(() => { globalThis.fetch = previousFetch; });

  assert.deepEqual(await getDueFlashcardDecksApi(), { items: [], has_more: false });
  assert.equal(requestedUrl, "/api/learning/flashcards/due");
});

test("learning progress API uses the signed-in session and validates the requested window", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return new Response(JSON.stringify(progressFixture()), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  };

  await getLearningProgressApi(90);
  assert.equal(requests[0].path, "/api/learning/progress?window_days=90");
  assert.equal(requests[0].options.body, undefined);
  assert.equal(new URL(requests[0].path, "https://jot.example").searchParams.has("owner_id"), false);
  await assert.rejects(getLearningProgressApi(6), /between 7 and 90/);
  await assert.rejects(getLearningProgressApi(91), /between 7 and 90/);
  assert.equal(requests.length, 1);
});

test("progress projection accepts only owner-safe status and estimate fields", () => {
  const progress = normalizeLearningProgress(progressFixture({
    concepts: [{
      ...progressFixture().concepts[0],
      owner_id: "must-not-project",
      student_name: "must-not-project"
    }]
  }));
  assert.equal(progress.total_xp, 135);
  assert.equal(progress.recent_accuracy.rate, 0.75);
  assert.equal(progress.concepts[0].latest_demonstrated_level, 3);
  assert.equal(progress.concepts[0].current_level, null);
  assert.deepEqual(progress.concepts[0].evidence_sufficiency, { valid_observations: 4, independent_families: 2 });
  assert.equal(progress.concepts[0].owner_id, undefined);
  assert.equal("effective_weight" in progress.concepts[0].evidence_sufficiency, false);
  assert.equal(JSON.stringify(progress).includes("must-not-project"), false);
});

test("learning progress markup is escaped and distinguishes evidence from confirmed level", () => {
  const dto = progressFixture({ concepts: [{
    ...progressFixture().concepts[0], label: '<img src=x onerror="alert(1)">',
    status: "confirmed", current_level: 2
  }] });
  const html = learningProgressMarkup(normalizeLearningProgress(dto));
  assert.ok(!html.includes('<img src=x'));
  assert.match(html, /&lt;img src=x/);
  assert.match(html, /Latest demonstrated: Level 3/);
  assert.match(html, /Confirmed through Level 2/);
  assert.match(html, /90% estimate range: 38%–94%/);
  assert.match(html, /Recent estimate change: \+14 percentage points over 30 days/);
  assert.match(html, /Evidence sufficiency: 4 valid observations across 2 independent families/);
  assert.match(html, /Descriptive only; it does not establish mastery/);
  assert.match(html, /A review is due\. This does not lower your estimate\./);
  assert.match(html, /Private to your account/);
});

test("malformed progress projections fail closed", () => {
  assert.throws(() => normalizeLearningProgress(progressFixture({ total_xp: -2 })), /expected format/);
  assert.throws(() => normalizeLearningProgress(progressFixture({
    concepts: [{ ...progressFixture().concepts[0], status: "student-mastered" }]
  })), /safely displayed/);
  assert.throws(() => normalizeLearningProgress(progressFixture({
    recent_accuracy: { ...progressFixture().recent_accuracy, rate: 4 }
  })), /expected format/);
  assert.throws(() => normalizeLearningProgress(progressFixture({
    concepts: [{ ...progressFixture().concepts[0], evidence_sufficiency: { valid_observations: -1, independent_families: 2 } }]
  })), /safely displayed/);
});

test("learning-goal preferences are allowlisted and exposed through an accessible control", () => {
  assert.deepEqual(normalizeLearningPreferences({ chosen_goal: "build_foundation", owner_id: "private" }), { chosen_goal: "build_foundation" });
  assert.throws(() => normalizeLearningPreferences({ chosen_goal: "rank_up" }), /expected format/);
  const selector = learningGoalControlMarkup({
    chosenGoal: "exam_prep", available: true, status: "Saved.", statusRole: "status"
  });
  assert.match(selector, /<label for="learning-goal-select">Choose your practice goal<\/label>/);
  assert.match(selector, /aria-describedby="learning-goal-status"/);
  assert.match(selector, /value="exam_prep" selected>Simulate an exam/);
  assert.match(selector, /role="status" aria-live="polite">Saved\./);
  const unavailable = learningGoalControlMarkup({
    chosenGoal: "balanced", available: false, status: "<script>hidden<\/script>", statusRole: "alert"
  });
  assert.match(unavailable, /role="alert"/);
  assert.match(unavailable, /&lt;script&gt;hidden&lt;\/script&gt;/);
  assert.match(unavailable, /data-learning-goal-retry/);
});

test("private learning detail, history, and recommendation wrappers use session-scoped routes", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path: String(path), options });
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  };
  await getLearningConceptApi("concept/1");
  await getLearningPreferencesApi();
  await updateLearningPreferencesApi("exam_prep");
  await getLearningProgressHistoryApi({ days: 90, conceptId: "concept/1" });
  await getLearningRecommendationsApi();
  await updateLearningRecommendationStateApi("suggestion/2", "active");
  assert.deepEqual(requests.map(({ path }) => new URL(path, "https://jot.test").pathname), [
    "/api/learning/concepts/concept%2F1",
    "/api/learning/preferences",
    "/api/learning/preferences",
    "/api/learning/progress/history",
    "/api/learning/recommendations",
    "/api/learning/recommendations/suggestion%2F2/state"
  ]);
  assert.equal(requests[1].options.method, undefined);
  assert.equal(requests[2].options.method, "PUT");
  assert.deepEqual(JSON.parse(requests[2].options.body), { chosen_goal: "exam_prep" });
  assert.equal(new URL(requests[3].path, "https://jot.test").searchParams.get("concept_id"), "concept/1");
  assert.equal(new URL(requests[3].path, "https://jot.test").searchParams.get("days"), "90");
  assert.equal(requests[5].options.method, "PATCH");
  assert.deepEqual(JSON.parse(requests[5].options.body), { state: "active" });
  assert.throws(() => getLearningProgressHistoryApi({ days: 2 }), /between 7 and 365/);
  assert.throws(() => updateLearningPreferencesApi("guess"), /supported learning goal/);
  assert.throws(() => updateLearningRecommendationStateApi("id", "ignored"), /active, completed, or dismissed/);
  assert.equal(requests.length, 6);
});

test("concept details project only the owner's response evidence and safe released citations", () => {
  const detail = normalizeLearningConceptDetail({
    concept: {
      id: "c1", label: "Cell division", description: "How cells divide", status: "provisional",
      current_level: null, latest_demonstrated_level: 2,
      estimate: { mean: 0.6, lower_90: 0.3, upper_90: 0.8, threshold: 0.8, probability_threshold: 0.55, evidence_count: 2 },
      evidence_sufficiency: { valid_observations: 2, independent_families: 1, effective_weight: 0.7 },
      historical_peak: 2, recent_growth: 0.1, last_assessed_at: "2026-10-13T12:00:00Z", review_due: false,
      student_name: "must not project"
    },
    recent_evidence: [{
      id: "e1", assessed_at: "2026-10-13T12:00:00Z", level: 2, correctness: "incorrect",
      reasoning_result: "accepted", grading_policy: "reasoning_required", weight: 0.5,
      question_label: "Explain mitosis", source_citations: [{ material_id: "m1", title: "Lecture 3", locator: { page: 9 } }],
      other_student: "private"
    }],
    related: [
      { id: "c2", label: "DNA replication", type: "prerequisite_for", direction: "incoming", necessity: "essential", student_id: "private" },
      { id: "c3", label: "Cell division", type: "prerequisite_for", direction: "outgoing", necessity: "helpful" }
    ],
    available_practice: [{ type: "ladder", href: "#/classes/biology/study", label: "Practice cell division" }],
    private_rubric: "do not show"
  }, "c1");
  const markup = learningConceptDetailMarkup(detail);
  assert.match(markup, /Cell division/);
  assert.match(markup, /Explain mitosis/);
  assert.match(markup, /Lecture 3/);
  assert.match(markup, /Open source/);
  assert.match(markup, /DNA replication/);
  assert.match(markup, /Prerequisite of this concept/);
  assert.match(markup, /Prerequisite for/);
  assert.deepEqual(detail.related.map((item) => item.direction), ["incoming", "outgoing"]);
  assert.match(markup, /Practice cell division/);
  assert.match(markup, /Evidence sufficiency: 2 valid observations across 1 independent family/);
  assert.match(markup, /Descriptive only; it does not establish mastery/);
  assert.doesNotMatch(JSON.stringify(detail), /student_name|other_student|private_rubric|student_id/);
  assert.throws(() => normalizeLearningConceptDetail({ concept: { ...detail.concept, id: "other" } }, "c1"), /expected format/);
  assert.throws(() => normalizeLearningConceptDetail({
    ...detail,
    related: [{ ...detail.related[0], direction: "unknown" }]
  }, "c1"), /Related concepts/);
});

test("history is filtered to the requested concept and recommendations escape provider text", async () => {
  const history = normalizeLearningHistory({
    as_of: "2026-10-14T12:00:00Z", days: 90,
    snapshots: [
      { date: "2026-10-10", concept_id: "c1", mean: 0.5, lower_90: 0.2, upper_90: 0.8, current_level: null, status: "provisional", evidence_count: 2 },
      { date: "2026-10-10", concept_id: "other-student-concept", mean: 1, lower_90: 1, upper_90: 1, current_level: 4, status: "confirmed", evidence_count: 80 }
    ]
  }, "c1");
  assert.equal(history.snapshots.length, 1);
  const markup = learningHistoryMarkup(history);
  assert.match(markup, /50%/);
  assert.doesNotMatch(markup, /other-student/);

  const eventHistory = normalizeLearningHistory({
    concept_id: "c1",
    snapshots: [{ as_of: "2026-10-13T14:00:00Z", mean: 0.62, lower_90: 0.4, upper_90: 0.83, evidence_count: 4, level: 3 }]
  }, "c1", 90);
  assert.equal(eventHistory.snapshots[0].level, 3);
  assert.equal(eventHistory.snapshots[0].current_level, null);
  const eventMarkup = learningHistoryMarkup(eventHistory);
  assert.match(eventMarkup, /Evidence stratum: Level 3/);
  assert.doesNotMatch(eventMarkup, /Confirmed through Level 3/);
  assert.throws(() => normalizeLearningHistory({ concept_id: "another-student-concept", snapshots: [] }, "c1"), /expected format/);

  const recommendations = normalizeLearningRecommendations({ chosen_goal: "balanced", items: [{
    id: "r1", type: "practice", concept_id: "c1", label: "Practice cell division",
    reason: '<img src=x onerror="alert(1)">', evidence_ids: ["private"],
    resource: { type: "quiz", id: "q1", title: "Quiz 1", answer_key: "private" },
    state: "suggested", created_at: "2026-10-14T12:00:00Z", owner_id: "private",
    class_id: "class-1", href: "#/classes/class-1/study"
  }] });
  const recommendationMarkup = learningRecommendationsMarkup(recommendations);
  assert.match(recommendationMarkup, /&lt;img src=x/);
  assert.match(recommendationMarkup, /Quiz 1/);
  assert.match(recommendationMarkup, /href="#\/classes\/class-1\/study" data-recommendation-start/);
  assert.match(recommendationMarkup, /data-recommendation-start[^>]*>Start practice/);
  assert.match(recommendationMarkup, /data-recommendation-state="completed"[^>]*>Mark complete/);
  assert.match(recommendationMarkup, /data-recommendation-state="dismissed"[^>]*>Dismiss/);
  assert.equal(recommendations.items[0].resource.id, "q1");
  assert.doesNotMatch(JSON.stringify(recommendations), /evidence_ids|answer_key|owner_id|private/);
  const navigationOrder = [];
  await startLearningRecommendation(recommendations.items[0], {
    recordEngagement: async (id, state) => navigationOrder.push(`engagement:${id}:${state}`),
    navigate: (href) => navigationOrder.push(`navigate:${href}`)
  });
  assert.deepEqual(navigationOrder, [
    "engagement:r1:active",
    "navigate:#/classes/class-1/study"
  ]);
  const reviewMarkup = learningRecommendationsMarkup(normalizeLearningRecommendations({
    chosen_goal: "review",
    items: [{
      id: "r2", type: "review", concept_id: "c1", label: "Review cell division",
      reason: "Try a short source-linked check.", state: "suggested",
      created_at: "2026-10-14T12:00:00Z", class_id: "class-1",
      href: "#/classes/class-1/study"
    }]
  }));
  assert.match(reviewMarkup, /data-review-start="r2">Start short review/);
  assert.match(reviewMarkup, /data-review-session="r2"/);
  assert.doesNotMatch(reviewMarkup, /Mark complete/);
  const activeRecommendation = normalizeLearningRecommendations({ chosen_goal: "review", items: [{
    id: "r-active", type: "practice", label: "Keep practicing", reason: "Continue your current set",
    state: "active", created_at: "2026-10-14T12:00:00Z", class_id: "class-2", href: "#/classes/class-2/study"
  }] });
  assert.match(learningRecommendationsMarkup(activeRecommendation), /data-recommendation-start[^>]*>Continue practice/);
  let activeNavigation = "";
  await startLearningRecommendation(activeRecommendation.items[0], {
    recordEngagement: async () => assert.fail("active practice should already be recorded"),
    navigate: (href) => { activeNavigation = href; }
  });
  assert.equal(activeNavigation, "#/classes/class-2/study");
  assert.throws(() => normalizeLearningRecommendations({ chosen_goal: "balanced", items: [{
    id: "r2", type: "review", label: "Review", reason: "Reason", state: "mastered", created_at: "2026-10-14T12:00:00Z"
  }] }), /safely displayed/);
  assert.throws(() => normalizeLearningRecommendations({ chosen_goal: "unsupported", items: [] }), /expected format/);
  assert.throws(() => normalizeLearningRecommendations({ chosen_goal: "balanced", items: [{
    id: "r3", type: "review", label: "Review", reason: "Reason", state: "active", created_at: "2026-10-14T12:00:00Z",
    class_id: "class-3", href: "#/classes/class-3/study", resource: { id: { unsafe: true }, title: "Resource" }
  }] }), /safely displayed/);
  assert.throws(() => normalizeLearningRecommendations({ chosen_goal: "balanced", items: [{
    id: "r4", type: "review", label: "Review", reason: "Reason", state: "suggested", created_at: "2026-10-14T12:00:00Z",
    class_id: "class-4", href: "#/classes/class-5/study"
  }] }), /safely displayed/);
});
