import {
  getLearningConceptApi,
  getLearningPreferencesApi,
  getLearningProgressApi,
  getLearningProgressHistoryApi,
  getLearningRecommendationsApi,
  getDueFlashcardDecksApi,
  startLearningReviewApi,
  submitLearningReviewAnswerApi,
  updateLearningPreferencesApi,
  updateLearningRecommendationStateApi
} from "./api.js";
import { bindSourceCitations, sourceCitationHtml } from "./source-citations.js";
import { escapeHtml } from "./ui.js";
import { mathHtml, typesetMath } from "./math.js";

const STATES = new Set(["unassessed", "provisional", "confirmed"]);
const CORRECTNESS_STATES = new Set(["correct", "incorrect", "unanswered", "pending", "unavailable"]);
const RECOMMENDATION_TYPES = new Set(["practice", "review", "diagnostic"]);
const RECOMMENDATION_STATES = new Set(["suggested", "pending", "active", "completed", "dismissed"]);
const LEARNING_GOALS = new Set(["balanced", "build_foundation", "exam_prep", "review"]);
const LEARNING_GOAL_LABELS = {
  balanced: "Understand a new topic",
  build_foundation: "Build foundations",
  exam_prep: "Simulate an exam",
  review: "Remember for next week"
};

export function normalizeDueFlashcardDecks(dto) {
  if (!dto || typeof dto !== "object" || Array.isArray(dto)
    || !Array.isArray(dto.items) || dto.items.length > 25
    || typeof dto.has_more !== "boolean") {
    throw new Error("Due flashcard reviews are not available in the expected format.");
  }
  const seen = new Set();
  const items = dto.items.map((raw) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)
      || typeof raw.material_id !== "string" || !raw.material_id
      || typeof raw.title !== "string" || !raw.title.trim()
      || (raw.class_id !== null && typeof raw.class_id !== "string")
      || (raw.class_name !== null && typeof raw.class_name !== "string")
      || !Number.isSafeInteger(raw.due_count) || raw.due_count < 1
      || !requiredTimestamp(raw.first_due_at)
      || seen.has(raw.material_id)) {
      throw new Error("Due flashcard reviews are not available in the expected format.");
    }
    seen.add(raw.material_id);
    return {
      material_id: raw.material_id,
      title: raw.title.slice(0, 240),
      class_id: raw.class_id,
      class_name: raw.class_name?.slice(0, 160) || null,
      due_count: raw.due_count,
      first_due_at: raw.first_due_at
    };
  });
  return { items, has_more: dto.has_more };
}

function dueFlashcardHref(materialId) {
  const currentUrl = new URL(globalThis.location?.href || "/", "http://jot.local");
  currentUrl.hash = "";
  currentUrl.search = "";
  currentUrl.searchParams.set("study_material", materialId);
  currentUrl.searchParams.set("review_due", "1");
  return `${currentUrl.pathname}${currentUrl.search}`;
}

export function dueFlashcardDecksMarkup(dueDecks) {
  if (!dueDecks.items.length) {
    return `<div class="panel empty-state"><strong>No flashcards due</strong><span>Your saved flashcard schedules will appear here when a review is due.</span></div>`;
  }
  return `<div class="class-grid">${dueDecks.items.map((item) => `<article class="panel progress-recommendation-card due-flashcard-card">
    <span class="permission-badge">${item.due_count} ${item.due_count === 1 ? "card" : "cards"} due</span>
    <h3>${escapeHtml(item.title)}</h3>
    <p>${escapeHtml(item.class_name || "Personal study set")} · First due ${escapeHtml(reviewDateLabel(item.first_due_at))}</p>
    <a class="btn btn-primary btn-small" href="${escapeHtml(dueFlashcardHref(item.material_id))}">Review ${item.due_count} due ${item.due_count === 1 ? "card" : "cards"}</a>
  </article>`).join("")}</div>${dueDecks.has_more ? `<p class="field-hint">This list is capped at 25 entries; more due decks may be available.</p>` : ""}`;
}

export function normalizeLearningPreferences(dto) {
  if (!dto || typeof dto !== "object" || Array.isArray(dto) || !LEARNING_GOALS.has(dto.chosen_goal)) {
    throw new Error("Learning preferences are not available in the expected format.");
  }
  return { chosen_goal: dto.chosen_goal };
}

export function learningGoalControlMarkup({ chosenGoal, available, saving = false, status = "", statusRole = "status" }) {
  if (!available) {
    return `<div class="panel empty-state" role="${statusRole}"><strong>Practice goal unavailable</strong><span>${escapeHtml(status)}</span><button type="button" class="btn btn-secondary btn-small" data-learning-goal-retry>Try again</button></div>`;
  }
  return `<div class="field-block"><label for="learning-goal-select">Choose your practice goal</label><select id="learning-goal-select" data-learning-goal aria-describedby="learning-goal-status" ${saving ? "disabled" : ""}>${[...LEARNING_GOALS].map((goal) => `<option value="${goal}" ${goal === chosenGoal ? "selected" : ""}>${LEARNING_GOAL_LABELS[goal]}</option>`).join("")}</select><p id="learning-goal-status" class="field-hint" role="${statusRole}" aria-live="polite">${escapeHtml(status)}</p></div>`;
}

function nullableLevel(value) {
  return value === null || value === undefined
    ? null
    : Number.isInteger(value) && value >= 1 && value <= 4 ? value : undefined;
}

function nullableProbability(value) {
  return value === null || value === undefined
    ? null
    : typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
      ? value : undefined;
}

function requiredCount(value) {
  if (!Number.isSafeInteger(value) || value < 0) return undefined;
  return value;
}

function normalizeEvidenceSufficiency(value) {
  if (value == null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const validObservations = requiredCount(value.valid_observations);
  const independentFamilies = requiredCount(value.independent_families);
  if (validObservations === undefined || independentFamilies === undefined) return undefined;
  return { valid_observations: validObservations, independent_families: independentFamilies };
}

function evidenceSufficiencyLabel(value) {
  if (!value) return "";
  const familyLabel = value.independent_families === 1 ? "independent family" : "independent families";
  return `<p class="row-meta">Evidence sufficiency: ${value.valid_observations} valid observations across ${value.independent_families} ${familyLabel}. Descriptive only; it does not establish mastery.</p>`;
}

function requiredTimestamp(value) {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return undefined;
  return value;
}

/** Strictly project the owner-private progress endpoint onto display fields. */
export function normalizeLearningProgress(dto) {
  if (!dto || typeof dto !== "object" || Array.isArray(dto)
    || typeof dto.algorithm_version !== "string"
    || !requiredTimestamp(dto.as_of)
    || requiredCount(dto.total_xp) === undefined
    || !dto.recent_accuracy || typeof dto.recent_accuracy !== "object"
    || !Array.isArray(dto.concepts)) {
    throw new Error("Learning progress is not available in the expected format.");
  }
  const accuracy = dto.recent_accuracy;
  if (!Number.isInteger(accuracy.window_days) || accuracy.window_days < 7 || accuracy.window_days > 90
    || ["graded_count", "correct_count", "incorrect_count", "unanswered_count", "skipped_count", "pending_count"]
      .some((key) => requiredCount(accuracy[key]) === undefined)
    || nullableProbability(accuracy.rate) === undefined) {
    throw new Error("Learning progress is not available in the expected format.");
  }
  const concepts = dto.concepts.map((concept) => {
    if (!concept || typeof concept !== "object" || Array.isArray(concept)
      || typeof concept.id !== "string" || !concept.id
      || typeof concept.label !== "string" || !concept.label.trim()
      || !STATES.has(concept.status)
      || !concept.estimate || typeof concept.estimate !== "object") return null;
    const latestLevel = nullableLevel(concept.latest_demonstrated_level);
    const currentLevel = nullableLevel(concept.current_level);
    const historicalPeak = nullableLevel(concept.historical_peak);
    const { estimate } = concept;
    const mean = nullableProbability(estimate.mean);
    const lower = nullableProbability(estimate.lower_90);
    const upper = nullableProbability(estimate.upper_90);
    const probabilityThreshold = nullableProbability(estimate.probability_threshold);
    const threshold = nullableProbability(estimate.threshold);
    const evidenceSufficiency = normalizeEvidenceSufficiency(concept.evidence_sufficiency);
    if (latestLevel === undefined || currentLevel === undefined || historicalPeak === undefined
      || mean === undefined || lower === undefined || upper === undefined || probabilityThreshold === undefined
      || threshold === undefined
      || evidenceSufficiency === undefined
      || requiredCount(estimate.evidence_count) === undefined
      || (concept.status !== "unassessed" && mean === null)
      || (concept.last_assessed_at !== null && concept.last_assessed_at !== undefined
        && !requiredTimestamp(concept.last_assessed_at))
      || typeof concept.review_due !== "boolean"
      || (concept.recent_growth !== null && concept.recent_growth !== undefined
        && (typeof concept.recent_growth !== "number" || !Number.isFinite(concept.recent_growth)
          || concept.recent_growth < -1 || concept.recent_growth > 1))) return null;
    return {
      id: concept.id,
      label: concept.label.slice(0, 240),
      status: concept.status,
      latest_demonstrated_level: latestLevel,
      current_level: currentLevel,
      estimate: {
        mean,
        lower_90: lower,
        upper_90: upper,
        probability_threshold: probabilityThreshold,
        threshold,
        evidence_count: estimate.evidence_count
      },
      evidence_sufficiency: evidenceSufficiency,
      historical_peak: historicalPeak,
      recent_growth: concept.recent_growth ?? null,
      last_assessed_at: concept.last_assessed_at ?? null,
      review_due: concept.review_due
    };
  });
  if (concepts.some((concept) => !concept)) {
    throw new Error("A concept progress record could not be safely displayed.");
  }
  return {
    algorithm_version: dto.algorithm_version,
    as_of: dto.as_of,
    total_xp: dto.total_xp,
    recent_accuracy: {
      window_days: accuracy.window_days,
      graded_count: accuracy.graded_count,
      correct_count: accuracy.correct_count,
      incorrect_count: accuracy.incorrect_count,
      unanswered_count: accuracy.unanswered_count,
      skipped_count: accuracy.skipped_count,
      pending_count: accuracy.pending_count,
      rate: accuracy.rate
    },
    concepts
  };
}

function percent(value) {
  return value === null ? "Not enough evidence" : `${Math.round(value * 100)}%`;
}

function dateLabel(value) {
  if (!value) return "Not assessed yet";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Date unavailable"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function stateLabel(state) {
  return state === "confirmed" ? "Confirmed estimate"
    : state === "provisional" ? "Provisional estimate" : "Not assessed";
}

function growthLabel(value) {
  if (value === null) return "";
  const percentagePoints = Math.round(Math.abs(value) * 100);
  const direction = value > 0 ? "+" : value < 0 ? "−" : "";
  return `<p class="row-meta">Recent estimate change: ${direction}${percentagePoints} percentage points over 30 days</p>`;
}

function conceptCard(concept) {
  const mean = concept.estimate.mean;
  const interval = concept.estimate.lower_90 !== null && concept.estimate.upper_90 !== null
    ? `<p class="row-meta">90% estimate range: ${percent(concept.estimate.lower_90)}–${percent(concept.estimate.upper_90)}</p>`
    : "";
  const demonstrated = concept.latest_demonstrated_level === null
    ? "No level demonstrated"
    : `Latest demonstrated: Level ${concept.latest_demonstrated_level}`;
  const confirmed = concept.current_level === null
    ? "No confirmed level"
    : `Confirmed through Level ${concept.current_level}`;
  const peak = concept.historical_peak === null
    ? "No historical peak"
    : `Historical peak: Level ${concept.historical_peak}`;
  return `
    <article class="panel progress-concept-card">
      <div class="progress-concept-heading">
        <h3>${escapeHtml(concept.label)}</h3>
        <span class="pill no-dot" data-progress-state="${concept.status}">${stateLabel(concept.status)}</span>
      </div>
      <p class="progress-demonstration">${demonstrated}</p>
      <p class="row-meta">${confirmed} · ${peak}</p>
      ${mean === null ? `<p class="row-meta">No proficiency estimate yet.</p>` : `
        <div class="progress-estimate">
          <label for="progress-estimate-${escapeHtml(concept.id)}">Current proficiency estimate: <strong>${percent(mean)}</strong></label>
          <progress id="progress-estimate-${escapeHtml(concept.id)}" max="100" value="${Math.round(mean * 100)}">${Math.round(mean * 100)}%</progress>
        </div>
      `}
      ${interval}
      ${growthLabel(concept.recent_growth)}
      <p class="row-meta">${concept.estimate.evidence_count} evidence observations · last assessed ${dateLabel(concept.last_assessed_at)}</p>
      ${evidenceSufficiencyLabel(concept.evidence_sufficiency)}
      ${concept.review_due ? `<p class="progress-review-due" role="note">A review is due. This does not lower your estimate.</p>` : ""}
      <button class="btn btn-secondary btn-small progress-detail-button" type="button" data-progress-concept="${escapeHtml(concept.id)}" aria-controls="progress-concept-detail">View concept detail</button>
    </article>
  `;
}

export function learningProgressMarkup(progress) {
  const accuracy = progress.recent_accuracy;
  return `
    <section class="learning-progress section-stack" aria-labelledby="learning-progress-title">
      <div class="page-heading">
        <div>
          <h1 id="learning-progress-title">Learning progress</h1>
          <p class="muted">Private to your account. Proficiency values are estimates based on validated assessment evidence.</p>
        </div>
        <label class="progress-window-control">Accuracy window
          <select data-progress-window aria-label="Recent accuracy window">
            ${[7, 30, 90].map((days) => `<option value="${days}" ${accuracy.window_days === days ? "selected" : ""}>Last ${days} days</option>`).join("")}
          </select>
        </label>
      </div>
      <section class="metric-strip progress-summary" aria-label="Your progress summary">
        <div class="metric"><strong>${progress.total_xp.toLocaleString()}</strong><span>Total XP</span></div>
        <div class="metric"><strong>${percent(accuracy.rate)}</strong><span>Recent accuracy</span></div>
        <div class="metric"><strong>${accuracy.graded_count.toLocaleString()}</strong><span>Graded responses</span></div>
      </section>
      <p class="row-meta progress-accuracy-detail" role="note">
        ${accuracy.correct_count} correct · ${accuracy.incorrect_count} incorrect · ${accuracy.unanswered_count} unanswered · ${accuracy.skipped_count} skipped · ${accuracy.pending_count} pending grading
      </p>
      ${progress.concepts.length
        ? `<section aria-labelledby="concept-progress-title"><div class="progress-section-heading"><h2 id="concept-progress-title">Concept progress</h2><p class="muted">Current estimates, demonstrated levels, and review status.</p></div><div class="class-grid progress-concept-grid">${progress.concepts.map(conceptCard).join("")}</div></section>`
        : `<div class="panel empty-state"><strong>No progress to show yet</strong><span>Your validated assessment activity will appear here.</span></div>`}
      <section class="progress-detail-section" aria-labelledby="progress-detail-title" tabindex="-1" hidden>
        <h2 id="progress-detail-title">Concept detail</h2>
        <div id="progress-concept-detail"></div>
        <div id="progress-history-detail"></div>
      </section>
      <section class="progress-due-reviews" aria-labelledby="due-reviews-title">
        <div class="progress-section-heading"><h2 id="due-reviews-title">Review due</h2><p class="muted">Private flashcard schedules. Ratings are planning signals and do not change concept mastery.</p></div>
        <div id="due-flashcard-decks" class="progress-recommendation-list" role="region" aria-live="polite" aria-label="Flashcard decks due for review">
          <p class="panel empty-state" role="status" aria-busy="true">Checking saved flashcard schedules…</p>
        </div>
      </section>
      <section class="progress-recommendations" aria-labelledby="recommendations-title">
        <div class="progress-section-heading"><h2 id="recommendations-title">Practice suggestions</h2><p class="muted">Suggestions are based on your own learning evidence.</p></div>
        <div id="learning-goal-control" class="progress-goal-control" role="region" aria-label="Practice goal">
          <p class="panel empty-state" role="status" aria-live="polite">Loading your practice goal…</p>
        </div>
        <div id="learning-recommendations" class="progress-recommendation-list" role="region" aria-live="polite" aria-label="Your practice suggestions">
          <p class="panel empty-state" role="status">Loading practice suggestions…</p>
        </div>
      </section>
      <p class="row-meta progress-version">Estimate method: ${escapeHtml(progress.algorithm_version)} · Updated ${dateLabel(progress.as_of)}</p>
    </section>
  `;
}

export async function renderLearningProgress({ root, initialWindowDays = 30 }) {
  let requestVersion = 0;
  let detailRequestVersion = 0;
  let goalRevision = 0;
  let chosenGoal = "balanced";
  let goalAvailable = false;
  let goalSaving = false;
  let goalStatus = "Loading your practice goal…";
  let goalStatusRole = "status";
  let evidenceSufficiencyByConcept = new Map();
  let dueDeckRequestVersion = 0;

  function renderGoalControl() {
    const container = root.querySelector("#learning-goal-control");
    if (!container) return;
    container.innerHTML = learningGoalControlMarkup({
      chosenGoal, available: goalAvailable, saving: goalSaving, status: goalStatus, statusRole: goalStatusRole
    });
    if (!goalAvailable) {
      container.querySelector("[data-learning-goal-retry]")?.addEventListener("click", () => void loadLearningGoal());
      return;
    }
    container.querySelector("[data-learning-goal]")?.addEventListener("change", (event) => void saveLearningGoal(event.currentTarget.value));
  }

  async function loadLearningGoal() {
    const revision = goalRevision;
    goalStatus = "Loading your practice goal…";
    goalStatusRole = "status";
    renderGoalControl();
    try {
      const preferences = normalizeLearningPreferences(await getLearningPreferencesApi());
      if (revision !== goalRevision || goalSaving) return;
      chosenGoal = preferences.chosen_goal;
      goalAvailable = true;
      goalStatus = "Your goal shapes the practice suggestions shown below.";
      goalStatusRole = "status";
      renderGoalControl();
    } catch (error) {
      if (revision !== goalRevision || goalSaving) return;
      goalStatus = error.message || "Check your connection and try again.";
      goalStatusRole = "alert";
      renderGoalControl();
    }
  }

  async function saveLearningGoal(nextGoal) {
    if (!LEARNING_GOALS.has(nextGoal) || goalSaving) return;
    const previousGoal = chosenGoal;
    goalRevision += 1;
    const revision = goalRevision;
    goalSaving = true;
    chosenGoal = nextGoal;
    goalStatus = "Saving your practice goal…";
    goalStatusRole = "status";
    renderGoalControl();
    try {
      chosenGoal = normalizeLearningPreferences(await updateLearningPreferencesApi(nextGoal)).chosen_goal;
      if (revision !== goalRevision) return;
      goalAvailable = true;
      goalSaving = false;
      goalStatus = "Practice goal saved. Updating suggestions…";
      renderGoalControl();
      await loadRecommendations();
    } catch (error) {
      if (revision !== goalRevision) return;
      chosenGoal = previousGoal;
      goalAvailable = true;
      goalSaving = false;
      goalStatus = error.message || "The practice goal could not be saved. Your previous choice is unchanged.";
      goalStatusRole = "alert";
      renderGoalControl();
    }
  }

  async function load(windowDays) {
    const version = ++requestVersion;
    if (!goalSaving) goalRevision += 1;
    detailRequestVersion += 1;
    root.innerHTML = `<div class="panel empty-state" role="status" aria-live="polite" aria-busy="true">Loading your learning progress…</div>`;
    try {
      const progress = normalizeLearningProgress(await getLearningProgressApi(windowDays));
      if (version !== requestVersion) return;
      evidenceSufficiencyByConcept = new Map(progress.concepts.map((concept) => [concept.id, concept.evidence_sufficiency]));
      root.innerHTML = learningProgressMarkup(progress);
      root.querySelector("[data-progress-window]")?.addEventListener("change", (event) => {
        const selected = Number(event.currentTarget.value);
        if ([7, 30, 90].includes(selected)) void load(selected);
      });
      root.querySelectorAll("[data-progress-concept]").forEach((button) => {
        button.addEventListener("click", () => void loadConceptDetail(button.dataset.progressConcept));
      });
      void loadLearningGoal();
      void loadRecommendations();
      void loadDueFlashcardDecks();
    } catch (error) {
      if (version !== requestVersion) return;
      root.innerHTML = `
        <div class="panel empty-state progress-load-error" role="alert">
          <strong>Learning progress could not be loaded</strong>
          <span>${escapeHtml(error.message || "Check your connection and try again.")}</span>
          <button class="btn btn-secondary" type="button" data-progress-retry>Try again</button>
        </div>
      `;
      root.querySelector("[data-progress-retry]")?.addEventListener("click", () => void load(windowDays));
    }
  }

  async function loadConceptDetail(conceptId) {
    const version = ++detailRequestVersion;
    const section = root.querySelector(".progress-detail-section");
    const detailRoot = root.querySelector("#progress-concept-detail");
    const historyRoot = root.querySelector("#progress-history-detail");
    if (!section || !detailRoot || !historyRoot || !conceptId) return;
    section.hidden = false;
    detailRoot.innerHTML = `<p class="panel empty-state" role="status" aria-busy="true">Loading concept detail…</p>`;
    historyRoot.innerHTML = "";
    try {
      const detailDto = await getLearningConceptApi(conceptId);
      if (version !== detailRequestVersion) return;
      const detail = normalizeLearningConceptDetail(detailDto, conceptId);
      // The progress projection is the authoritative source for sufficiency counts;
      // preserve them in detail even if an older detail DTO omits the optional field.
      if (!detail.concept.evidence_sufficiency) {
        detail.concept.evidence_sufficiency = evidenceSufficiencyByConcept.get(conceptId) || null;
      }
      detailRoot.innerHTML = learningConceptDetailMarkup(detail);
      bindSourceCitations(detailRoot, { classId: null });
      detailRoot.querySelector("[data-close-progress-detail]")?.addEventListener("click", () => {
        section.hidden = true;
        [...root.querySelectorAll("[data-progress-concept]")]
          .find((button) => button.dataset.progressConcept === conceptId)?.focus();
      });
      section.scrollIntoView?.({ block: "start" });
      section.focus({ preventScroll: true });
    } catch (error) {
      if (version !== detailRequestVersion) return;
      detailRoot.innerHTML = `<div class="panel empty-state" role="alert"><strong>Concept detail could not be loaded</strong><span>${escapeHtml(error.message || "Try again.")}</span><button type="button" class="btn btn-secondary btn-small" data-concept-detail-retry>Try again</button></div>`;
      detailRoot.querySelector("[data-concept-detail-retry]")?.addEventListener("click", () => void loadConceptDetail(conceptId));
      return;
    }
    try {
      const historyDto = await getLearningProgressHistoryApi({ days: 90, conceptId });
      if (version !== detailRequestVersion) return;
      historyRoot.innerHTML = learningHistoryMarkup(normalizeLearningHistory(historyDto, conceptId));
    } catch (error) {
      if (version !== detailRequestVersion) return;
      historyRoot.innerHTML = `<div class="panel empty-state" role="alert"><strong>Dated history could not be loaded</strong><span>${escapeHtml(error.message || "The current concept estimate is still available.")}</span><button type="button" class="btn btn-secondary btn-small" data-history-retry>Retry history</button></div>`;
      historyRoot.querySelector("[data-history-retry]")?.addEventListener("click", async () => {
        historyRoot.innerHTML = `<p class="panel empty-state" role="status" aria-busy="true">Loading dated history…</p>`;
        try {
          const historyDto = await getLearningProgressHistoryApi({ days: 90, conceptId });
          if (version === detailRequestVersion) historyRoot.innerHTML = learningHistoryMarkup(normalizeLearningHistory(historyDto, conceptId));
        } catch (retryError) {
          if (version === detailRequestVersion) historyRoot.innerHTML = `<p class="panel empty-state" role="alert">${escapeHtml(retryError.message || "History remains unavailable.")}</p>`;
        }
      });
    }
  }

  async function loadRecommendations() {
    const container = root.querySelector("#learning-recommendations");
    if (!container) return;
    const revision = goalRevision;
    try {
      const recommendations = normalizeLearningRecommendations(await getLearningRecommendationsApi());
      if (revision !== goalRevision) return;
      if (!goalSaving && recommendations.chosen_goal !== chosenGoal) {
        // The recommendations response carries the goal used to build this list.
        // Prefer it over a concurrently completed preferences read.
        goalRevision += 1;
        chosenGoal = recommendations.chosen_goal;
        goalAvailable = true;
        goalStatus = "Your goal shapes the practice suggestions shown below.";
        goalStatusRole = "status";
        renderGoalControl();
      } else if (!goalSaving && !goalAvailable) {
        chosenGoal = recommendations.chosen_goal;
        goalAvailable = true;
        goalStatus = "Your goal shapes the practice suggestions shown below.";
        goalStatusRole = "status";
        renderGoalControl();
      }
      renderLearningRecommendations(container, recommendations, loadRecommendations);
    } catch (error) {
      if (revision !== goalRevision) return;
      container.innerHTML = `<div class="panel empty-state" role="alert"><strong>Practice suggestions could not be loaded</strong><span>${escapeHtml(error.message || "Try again.")}</span><button type="button" class="btn btn-secondary btn-small" data-recommendations-retry>Try again</button></div>`;
      container.querySelector("[data-recommendations-retry]")?.addEventListener("click", () => void loadRecommendations());
    }
  }

  async function loadDueFlashcardDecks() {
    const container = root.querySelector("#due-flashcard-decks");
    if (!container) return;
    const revision = ++dueDeckRequestVersion;
    container.innerHTML = `<p class="panel empty-state" role="status" aria-live="polite" aria-busy="true">Checking saved flashcard schedules…</p>`;
    try {
      const dueDecks = normalizeDueFlashcardDecks(await getDueFlashcardDecksApi());
      if (revision !== dueDeckRequestVersion || !container.isConnected) return;
      container.innerHTML = dueFlashcardDecksMarkup(dueDecks);
    } catch (error) {
      if (revision !== dueDeckRequestVersion || !container.isConnected) return;
      container.innerHTML = `<div class="panel empty-state" role="alert"><strong>Due flashcard reviews could not be loaded</strong><span>${escapeHtml(error.message || "Try again.")}</span><button class="btn btn-secondary btn-small" type="button" data-due-flashcards-retry>Try again</button></div>`;
      container.querySelector("[data-due-flashcards-retry]")?.addEventListener("click", () => void loadDueFlashcardDecks());
    }
  }

  await load(initialWindowDays);
}

function validISODate(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function optionalLevel(value) {
  return value == null ? null : nullableLevel(value);
}

function optionalProbability(value) {
  return value == null ? null : nullableProbability(value);
}

function normalizeCitation(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const materialId = typeof source.material_id === "string" ? source.material_id : "";
  if (!materialId) return null;
  const locator = source.locator && typeof source.locator === "object" && !Array.isArray(source.locator)
    ? source.locator : {};
  return {
    upload_id: materialId,
    material_id: materialId,
    name: typeof source.title === "string" ? source.title.slice(0, 240) : "Course source",
    ...(Number.isInteger(locator.page) && locator.page > 0 ? { page: locator.page } : {}),
    ...(Number.isInteger(locator.slide) && locator.slide > 0 ? { slide: locator.slide } : {}),
    ...(Number.isFinite(locator.timestamp_seconds) && locator.timestamp_seconds >= 0 ? { timestamp_seconds: locator.timestamp_seconds } : {}),
    ...(Number.isFinite(locator.timestamp_end_seconds) && locator.timestamp_end_seconds >= 0 ? { timestamp_end_seconds: locator.timestamp_end_seconds } : {}),
    ...(Number.isInteger(locator.paragraph) && locator.paragraph > 0 ? { paragraph: locator.paragraph } : {}),
    ...(Number.isInteger(locator.block_order_start) ? { block_order_start: locator.block_order_start } : {}),
    ...(Number.isInteger(locator.block_order_end) ? { block_order_end: locator.block_order_end } : {})
  };
}

function safeRecommendationHref(href, classId) {
  if (typeof href !== "string" || typeof classId !== "string" || !classId) return null;
  const match = href.match(/^#\/classes\/([^/?#]+)\/study$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]) === classId ? href : null;
  } catch {
    return null;
  }
}

export function normalizeLearningConceptDetail(dto, expectedConceptId = null) {
  const raw = dto?.concept;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)
    || typeof raw.id !== "string" || !raw.id
    || (expectedConceptId && raw.id !== expectedConceptId)
    || typeof raw.label !== "string" || !raw.label.trim()
    || !STATES.has(raw.status)
    || typeof raw.review_due !== "boolean"
    || !raw.estimate || typeof raw.estimate !== "object") {
    throw new Error("Concept detail is not available in the expected format.");
  }
  const estimate = raw.estimate;
  const concept = {
    id: raw.id,
    label: raw.label.slice(0, 240),
    description: typeof raw.description === "string" ? raw.description.slice(0, 2000) : "",
    status: raw.status,
    current_level: optionalLevel(raw.current_level),
    latest_demonstrated_level: optionalLevel(raw.latest_demonstrated_level),
    estimate: {
      mean: optionalProbability(estimate.mean),
      lower_90: optionalProbability(estimate.lower_90),
      upper_90: optionalProbability(estimate.upper_90),
      threshold: optionalProbability(estimate.threshold),
      probability_threshold: optionalProbability(estimate.probability_threshold),
      evidence_count: requiredCount(estimate.evidence_count)
    },
    evidence_sufficiency: normalizeEvidenceSufficiency(raw.evidence_sufficiency),
    historical_peak: optionalLevel(raw.historical_peak),
    recent_growth: raw.recent_growth == null ? null : raw.recent_growth,
    last_assessed_at: raw.last_assessed_at ?? null,
    review_due: raw.review_due === true
  };
  if ([concept.current_level, concept.latest_demonstrated_level, concept.historical_peak,
    concept.estimate.mean, concept.estimate.lower_90, concept.estimate.upper_90,
    concept.estimate.threshold, concept.estimate.probability_threshold].includes(undefined)
    || concept.estimate.evidence_count === undefined
    || concept.evidence_sufficiency === undefined
    || (concept.recent_growth !== null && (typeof concept.recent_growth !== "number"
      || !Number.isFinite(concept.recent_growth) || concept.recent_growth < -1 || concept.recent_growth > 1))
    || (concept.last_assessed_at !== null && !validISODate(concept.last_assessed_at))) {
    throw new Error("Concept estimate could not be safely displayed.");
  }
  const evidence = Array.isArray(dto.recent_evidence) ? dto.recent_evidence.map((item) => {
    if (!item || typeof item !== "object" || typeof item.id !== "string" || !validISODate(item.assessed_at)
      || !Number.isInteger(item.level) || item.level < 1 || item.level > 4
      || (item.correctness != null && !CORRECTNESS_STATES.has(item.correctness))
      || (item.reasoning_result != null && !["accepted", "rejected", "pending", "failed", "unavailable"].includes(item.reasoning_result))) return null;
    return {
      id: item.id,
      assessed_at: item.assessed_at,
      level: item.level,
      correctness: item.correctness ?? null,
      reasoning_result: item.reasoning_result ?? null,
      grading_policy: typeof item.grading_policy === "string" ? item.grading_policy.slice(0, 80) : "",
      weight: typeof item.weight === "number" && Number.isFinite(item.weight) ? item.weight : null,
      question_label: typeof item.question_label === "string" ? item.question_label.slice(0, 240) : "Assessment response",
      source_citations: (Array.isArray(item.source_citations) ? item.source_citations : []).map(normalizeCitation).filter(Boolean)
    };
  }) : [];
  if (evidence.some((item) => !item)) throw new Error("A learning evidence record could not be safely displayed.");
  const related = (Array.isArray(dto.related) ? dto.related : []).map((item) => {
    if (!item || typeof item.id !== "string" || typeof item.label !== "string"
      || !["part_of", "prerequisite_for", "related_to", "applied_in"].includes(item.type)
      || !["incoming", "outgoing"].includes(item.direction)) return null;
    return {
      id: item.id,
      label: item.label.slice(0, 240),
      type: item.type,
      direction: item.direction,
      necessity: typeof item.necessity === "string" ? item.necessity.slice(0, 80) : ""
    };
  });
  if (related.some((item) => !item)) throw new Error("Related concepts could not be safely displayed.");
  const availablePractice = (Array.isArray(dto.available_practice) ? dto.available_practice : []).map((item) => {
    if (!item || !["quiz", "ladder"].includes(item.type) || typeof item.label !== "string") return null;
    const href = typeof item.href === "string" && /^#\/(classes\/[^\s"'<>]+\/study|study)$/.test(item.href) ? item.href : null;
    return { type: item.type, label: item.label.slice(0, 240), href };
  });
  if (availablePractice.some((item) => !item)) throw new Error("Practice links could not be safely displayed.");
  return { concept, recent_evidence: evidence, related, available_practice: availablePractice };
}

export function normalizeLearningHistory(dto, expectedConceptId, requestedDays = 90) {
  if (!dto || typeof dto !== "object" || Array.isArray(dto)
    || (dto.as_of != null && !validISODate(dto.as_of))
    || (dto.concept_id != null && dto.concept_id !== expectedConceptId)
    || (dto.days != null && (!Number.isInteger(dto.days) || dto.days < 7 || dto.days > 365))
    || !Number.isInteger(requestedDays) || requestedDays < 7 || requestedDays > 365
    || !Array.isArray(dto.snapshots)) {
    throw new Error("Learning history is not available in the expected format.");
  }
  const snapshots = dto.snapshots.filter((item) => item?.concept_id == null || item.concept_id === expectedConceptId).map((item) => {
    const date = item?.as_of || item?.date;
    const currentLevel = item?.current_level ?? null;
    const evidenceLevel = item?.level ?? null;
    const status = item?.status == null ? null : item.status;
    if (!item || !validISODate(date) || (status !== null && !STATES.has(status))
      || optionalLevel(currentLevel) === undefined || optionalLevel(currentLevel) !== null && currentLevel > 4
      || optionalLevel(evidenceLevel) === undefined || optionalLevel(evidenceLevel) !== null && evidenceLevel > 4
      || optionalProbability(item.mean) === undefined || optionalProbability(item.lower_90) === undefined
      || optionalProbability(item.upper_90) === undefined || requiredCount(item.evidence_count) === undefined) return null;
    return {
      date, concept_id: expectedConceptId, mean: item.mean ?? null,
      lower_90: item.lower_90 ?? null, upper_90: item.upper_90 ?? null,
      current_level: currentLevel, level: evidenceLevel, status, evidence_count: item.evidence_count
    };
  });
  if (snapshots.some((item) => !item)) throw new Error("A learning history point could not be safely displayed.");
  return { as_of: dto.as_of || snapshots.at(-1)?.date || null, days: dto.days ?? requestedDays, snapshots };
}

export function normalizeLearningRecommendations(dto) {
  if (!dto || typeof dto !== "object" || !LEARNING_GOALS.has(dto.chosen_goal) || !Array.isArray(dto.items)) {
    throw new Error("Practice suggestions are not available in the expected format.");
  }
  const items = dto.items.map((item) => {
    if (!item || typeof item !== "object" || typeof item.id !== "string"
      || !RECOMMENDATION_TYPES.has(item.type) || typeof item.label !== "string"
      || typeof item.reason !== "string" || !RECOMMENDATION_STATES.has(item.state)
      || !validISODate(item.created_at)
      || (item.concept_id != null && typeof item.concept_id !== "string")
      || typeof item.class_id !== "string" || !item.class_id
      || !safeRecommendationHref(item.href, item.class_id)
      || (item.resource != null && (typeof item.resource !== "object" || Array.isArray(item.resource)
        || typeof item.resource.title !== "string"
        || (item.resource.type != null && typeof item.resource.type !== "string")
        || (item.resource.id != null && typeof item.resource.id !== "string")))) return null;
    return {
      id: item.id,
      class_id: item.class_id,
      href: safeRecommendationHref(item.href, item.class_id),
      type: item.type,
      concept_id: typeof item.concept_id === "string" ? item.concept_id : null,
      label: item.label.slice(0, 240),
      reason: item.reason.slice(0, 800),
      resource: item.resource ? {
        type: typeof item.resource.type === "string" ? item.resource.type.slice(0, 80) : "",
        id: typeof item.resource.id === "string" ? item.resource.id : null,
        title: item.resource.title.slice(0, 240)
      } : null,
      state: item.state,
      created_at: item.created_at
    };
  });
  if (items.some((item) => !item)) throw new Error("A practice suggestion could not be safely displayed.");
  return { chosen_goal: dto.chosen_goal, items };
}

function evidenceLabel(value) {
  return value === "correct" ? "Correct" : value === "incorrect" ? "Incorrect"
    : value === "unanswered" ? "Unanswered" : value === "pending" ? "Grading pending"
      : value === null ? "Result not released" : "Unavailable";
}

export function learningConceptDetailMarkup(detail) {
  const concept = detail.concept;
  const estimate = concept.estimate;
  const evidence = detail.recent_evidence.length ? `<ol class="learning-evidence-list">${detail.recent_evidence.map((item) => `
    <li><article class="panel learning-evidence-card"><div class="progress-concept-heading"><strong>${escapeHtml(item.question_label)}</strong><span class="permission-badge">${evidenceLabel(item.correctness)}</span></div>
      <p class="row-meta">${dateLabel(item.assessed_at)} · Level ${item.level}${item.reasoning_result ? ` · Reasoning ${escapeHtml(item.reasoning_result)}` : ""}${item.grading_policy ? ` · ${escapeHtml(item.grading_policy.replaceAll("_", " "))}` : ""}</p>
      ${item.source_citations.length ? `<div class="source-chips" aria-label="Released source citations">${item.source_citations.map(sourceCitationHtml).join("")}</div>` : ""}
    </article></li>`).join("")}</ol>` : `<p class="muted">No recent assessment evidence is available.</p>`;
  const relationshipLabel = (item) => {
    if (item.type === "prerequisite_for") return item.direction === "outgoing" ? "Prerequisite for" : "Prerequisite of this concept";
    if (item.type === "part_of") return item.direction === "outgoing" ? "Part of" : "Contains this concept";
    if (item.type === "applied_in") return item.direction === "outgoing" ? "Applied in" : "Uses this concept";
    return "Related to";
  };
  const related = detail.related.length ? `<ul class="learning-related-list">${detail.related.map((item) => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(relationshipLabel(item))}${item.necessity ? ` · ${escapeHtml(item.necessity)}` : ""}</span></li>`).join("")}</ul>` : `<p class="muted">No validated related concepts are listed.</p>`;
  const practice = detail.available_practice.length ? `<ul class="learning-practice-list">${detail.available_practice.map((item) => `<li>${item.href ? `<a class="btn btn-secondary btn-small" href="${escapeHtml(item.href)}">${escapeHtml(item.label)}</a>` : `<span>${escapeHtml(item.label)}</span>`}</li>`).join("")}</ul>` : "";
  return `<article class="panel learning-concept-detail-card">
    <div class="progress-concept-heading"><div><p class="eyebrow">Your private estimate</p><h3>${escapeHtml(concept.label)}</h3></div><button class="btn btn-ghost btn-small" type="button" data-close-progress-detail>Close detail</button></div>
    ${concept.description ? `<p>${escapeHtml(concept.description)}</p>` : ""}
    <p>${concept.current_level === null ? "No confirmed level" : `Confirmed through Level ${concept.current_level}`} · ${concept.latest_demonstrated_level === null ? "No level demonstrated yet" : `Latest demonstrated: Level ${concept.latest_demonstrated_level}`}</p>
    <p class="row-meta">${estimate.mean === null ? "No estimate yet" : `Estimate ${percent(estimate.mean)} (${percent(estimate.lower_90)}–${percent(estimate.upper_90)})`} · ${estimate.evidence_count} evidence observations · ${concept.review_due ? "Review due" : "No review due"}</p>
    ${evidenceSufficiencyLabel(concept.evidence_sufficiency)}
    <div class="learning-detail-columns"><section><h4>Recent evidence</h4>${evidence}</section><section><h4>Validated relationships</h4>${related}${practice ? `<h4>Available practice</h4>${practice}` : ""}</section></div>
  </article>`;
}

export function learningHistoryMarkup(history) {
  const rows = history.snapshots.length ? history.snapshots.map((item) => `<li><time datetime="${escapeHtml(item.date)}">${dateLabel(item.date)}</time><span>${item.mean === null ? "No estimate" : `Estimate ${percent(item.mean)}${item.lower_90 !== null && item.upper_90 !== null ? ` · 90% range ${percent(item.lower_90)}–${percent(item.upper_90)}` : ""}`}</span><span>${item.current_level === null ? (item.level === null ? "No confirmed level" : `Evidence stratum: Level ${item.level}`) : `Confirmed through Level ${item.current_level}`}</span><span>${item.evidence_count} observations</span></li>`).join("") : `<li class="muted">No dated history is available yet.</li>`;
  return `<section class="panel learning-history-card" aria-labelledby="learning-history-title"><h3 id="learning-history-title">Dated progress history · last ${history.days} days</h3><ol>${rows}</ol></section>`;
}

export function learningRecommendationsMarkup(recommendations) {
  const active = recommendations.items.filter((item) => !["completed", "dismissed"].includes(item.state));
  if (!active.length) return `<div class="panel empty-state"><strong>No active practice suggestions</strong><span>New suggestions appear as you complete validated assessments.</span></div>`;
  return `<div class="class-grid">${active.map((item) => `<article class="panel progress-recommendation-card" data-recommendation="${escapeHtml(item.id)}">
    <span class="permission-badge">${escapeHtml(item.type)}</span><h3>${escapeHtml(item.label)}</h3><p>${escapeHtml(item.reason)}</p>
    ${item.resource ? `<p class="row-meta">Suggested resource: ${escapeHtml(item.resource.title)}</p>` : ""}
    ${item.type === "review" ? `<div class="material-card-actions"><button class="btn btn-primary btn-small" type="button" data-review-start="${escapeHtml(item.id)}">${item.state === "active" ? "Continue short review" : "Start short review"}</button><button class="btn btn-ghost btn-small" type="button" data-recommendation-state="dismissed" data-recommendation-id="${escapeHtml(item.id)}" aria-label="Dismiss: ${escapeHtml(item.label)}">Dismiss</button></div><div data-review-session="${escapeHtml(item.id)}"></div>` : `<div class="material-card-actions"><a class="btn btn-primary btn-small" href="${escapeHtml(item.href)}" data-recommendation-start data-recommendation-id="${escapeHtml(item.id)}" aria-label="${item.state === "active" ? "Continue practice" : "Start practice"}: ${escapeHtml(item.label)}">${item.state === "active" ? "Continue practice" : "Start practice"}</a><button class="btn btn-secondary btn-small" type="button" data-recommendation-state="completed" data-recommendation-id="${escapeHtml(item.id)}" aria-label="Mark complete: ${escapeHtml(item.label)}">Mark complete</button><button class="btn btn-ghost btn-small" type="button" data-recommendation-state="dismissed" data-recommendation-id="${escapeHtml(item.id)}" aria-label="Dismiss: ${escapeHtml(item.label)}">Dismiss</button></div>`}
  </article>`).join("")}</div>`;
}

function reviewCitation(source) {
  const locator = source?.locator && typeof source.locator === "object" ? source.locator : {};
  return {
    upload_id: source?.material_id || source?.upload_id,
    name: source?.title || source?.name || "Course source",
    ...(Number.isInteger(locator.page) ? { page: locator.page } : {}),
    ...(Number.isInteger(locator.slide) ? { slide: locator.slide } : {}),
    ...(Number.isFinite(locator.timestamp_start_seconds) ? { timestamp_seconds: locator.timestamp_start_seconds } : {}),
    ...(Number.isInteger(locator.paragraph) ? { paragraph: locator.paragraph } : {})
  };
}

function reviewDateLabel(value) {
  const time = Date.parse(value || "");
  return Number.isFinite(time)
    ? new Date(time).toLocaleDateString(undefined, { month: "long", day: "numeric" })
    : "a later date";
}

function renderLearningReview(container, item, session, { onCompleted } = {}) {
  if (!container || !session) return;
  const choices = Array.isArray(session.choices) ? session.choices : [];
  const completed = session.status === "completed";
  const selected = session.selected_choice_id;
  const selectedChoice = choices.find((choice) => choice.id === selected);
  container.innerHTML = `
    <section class="learning-review-session" aria-labelledby="learning-review-${escapeHtml(session.id)}-prompt">
      ${completed ? `<p class="permission-badge">${session.correct ? "Correct" : "Review this idea"}</p>` : `<p class="eyebrow">One-question retrieval check</p>`}
      <h4 id="learning-review-${escapeHtml(session.id)}-prompt">${mathHtml(session.prompt || "")}</h4>
      ${completed
        ? `<p class="review-answer">Your response: <strong>${mathHtml(selectedChoice?.text || "Answer saved")}</strong></p><p class="review-answer">Answer: <strong>${mathHtml(session.correct_answer || "")}</strong></p><p>${mathHtml(session.explanation || "")}</p>
          <div class="source-chips">${(session.citations || []).map((source) => sourceCitationHtml(reviewCitation(source))).join("")}</div>
          <p class="field-hint">A different source-linked check is scheduled for ${escapeHtml(reviewDateLabel(session.next_due_at))}. This result does not change your mastery estimate.</p>`
        : `<form data-learning-review-form="${escapeHtml(session.id)}"><fieldset class="answer-options"><legend>Choose an answer from memory before opening the source</legend>${choices.map((choice, index) => `<label><input type="radio" name="learning-review-choice" value="${escapeHtml(choice.id)}" /><span>${mathHtml(choice.text || "")}</span></label>`).join("")}</fieldset><p class="field-hint" data-review-error role="alert" hidden></p><button class="btn btn-primary btn-small" type="submit" disabled>Check answer</button></form><p class="field-hint">Your response is scored separately from concept mastery. Feedback and the cited course source appear after submission.</p>`}
    </section>`;
  void typesetMath(container);
  if (completed) {
    bindSourceCitations(container, { classId: item.class_id });
    return;
  }
  const form = container.querySelector("[data-learning-review-form]");
  const submit = form?.querySelector("button[type='submit']");
  form?.querySelectorAll("input[name='learning-review-choice']").forEach((input) => input.addEventListener("change", () => {
    submit.disabled = false;
  }));
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const choiceId = new FormData(form).get("learning-review-choice");
    if (typeof choiceId !== "string" || !choiceId) return;
    submit.disabled = true;
    submit.textContent = "Checking…";
    try {
      const result = await submitLearningReviewAnswerApi(session.id, choiceId);
      renderLearningReview(container, item, result, { onCompleted });
      onCompleted?.(result);
    } catch (error) {
      const message = form.querySelector("[data-review-error]");
      message.textContent = error.message || "Your answer could not be checked. Try again.";
      message.hidden = false;
      submit.disabled = false;
      submit.textContent = "Check answer";
    }
  });
}

export async function startLearningRecommendation(item, {
  recordEngagement = updateLearningRecommendationStateApi,
  navigate = (href) => { window.location.hash = href; }
} = {}) {
  const href = safeRecommendationHref(item?.href, item?.class_id);
  if (!href || typeof item?.id !== "string" || !item.id) {
    throw new Error("This practice suggestion does not have a safe class destination.");
  }
  if (item.state !== "active") await recordEngagement(item.id, "active");
  navigate(href);
  return href;
}

function renderLearningRecommendations(container, recommendations, reload) {
  container.innerHTML = learningRecommendationsMarkup(recommendations);
  container.querySelectorAll("[data-review-start]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = recommendations.items.find((candidate) => candidate.id === button.dataset.reviewStart);
      const sessionRoot = button.closest("article")?.querySelector("[data-review-session]");
      if (!item || !sessionRoot || button.disabled) return;
      button.disabled = true;
      button.textContent = "Preparing source-linked question…";
      sessionRoot.innerHTML = `<p class="field-hint" role="status" aria-live="polite">Loading a validated question from your course sources…</p>`;
      try {
        const session = await startLearningReviewApi(item.id);
        button.textContent = session.status === "completed" ? "Review complete" : "Review in progress";
        if (session.status === "completed") button.textContent = "Review complete";
        renderLearningReview(sessionRoot, item, session, {
          onCompleted() {
            button.textContent = "Review complete";
            button.disabled = true;
          }
        });
      } catch (error) {
        button.disabled = false;
        button.textContent = item.state === "active" ? "Continue short review" : "Start short review";
        sessionRoot.innerHTML = `<p class="field-hint" role="alert">${escapeHtml(error.message || "A review question is not available yet. Your course materials are unchanged.")}</p>`;
      }
    });
  });
  container.querySelectorAll("[data-recommendation-start]").forEach((link) => {
    link.addEventListener("click", async (event) => {
      const item = recommendations.items.find((candidate) => candidate.id === link.dataset.recommendationId);
      if (!item || !safeRecommendationHref(item.href, item.class_id)) {
        event.preventDefault();
        return;
      }
      if (item.state === "active") return;
      event.preventDefault();
      if (link.getAttribute("aria-disabled") === "true") return;
      link.setAttribute("aria-disabled", "true");
      link.classList.add("is-loading");
      try {
        await startLearningRecommendation(item);
      } catch (error) {
        link.removeAttribute("aria-disabled");
        link.classList.remove("is-loading");
        const message = document.createElement("p");
        message.className = "field-hint";
        message.setAttribute("role", "alert");
        message.textContent = error.message || "Practice could not be started. Try again.";
        link.closest("article")?.append(message);
      }
    });
  });
  container.querySelectorAll("[data-recommendation-state]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      const sibling = button.parentElement?.querySelectorAll("button");
      sibling?.forEach((item) => { item.disabled = true; });
      try {
        await updateLearningRecommendationStateApi(button.dataset.recommendationId, button.dataset.recommendationState);
        await reload();
      } catch (error) {
        sibling?.forEach((item) => { item.disabled = false; });
        const message = container.querySelector("[data-recommendation-error]") || document.createElement("p");
        message.dataset.recommendationError = "true";
        message.className = "field-hint";
        message.setAttribute("role", "alert");
        message.textContent = error.message || "The suggestion could not be updated. Try again.";
        button.closest("article")?.append(message);
      }
    });
  });
}
