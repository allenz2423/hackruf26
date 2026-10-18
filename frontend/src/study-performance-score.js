import {
  clearStudyPerformanceScoresApi,
  createStudyPerformanceScoreApi
} from "./api.js";
import { escapeHtml } from "./ui.js";

const CATEGORY_LABELS = [
  ["quiz_score", "Quizzes", "quiz_weight"],
  ["exam_score", "Practice exams", "exam_weight"],
  ["flashcard_score", "Flashcard practice", "flashcard_weight"]
];

function scoreLabel(value) {
  const score = Number(value);
  return Number.isFinite(score) && score >= 0 && score <= 100
    ? `${Math.round(score)} / 100`
    : "Unavailable";
}

export function studyPerformanceScoreMarkup(score) {
  const categories = CATEGORY_LABELS.filter(([key]) => score?.[key] != null && Number.isFinite(Number(score[key])))
    .map(([key, label, weightKey]) => {
      const weight = Number(score?.[weightKey]);
      const detail = weight > 0
        ? ` · ${Math.round(weight * 100)}% of this composite`
        : key === "flashcard_score" ? " · practice signal only" : "";
      return `<li><span>${label}${detail}</span><strong>${scoreLabel(score[key])}</strong></li>`;
    }).join("");
  const total = score?.overall_score == null
    ? `<strong class="study-performance-score-value">Practice signal</strong><span>No quiz or exam evidence yet, so a composite score is not shown.</span>`
    : `<strong class="study-performance-score-value">${scoreLabel(score.overall_score)}</strong><span>AI study score for this class · advisory only</span>`;
  const evidenceLevel = String(score?.evidence_level || "low");
  const evidence = `${Number(score?.quiz_attempts) || 0} quiz attempts · ${Number(score?.exam_attempts) || 0} exam attempts · ${Number(score?.flashcards_rated) || 0} flashcards rated`;
  const scope = score?.overall_score == null
    ? "Based on your saved flashcard ratings in this class."
    : "Based on recent released quiz/exam results and saved flashcard ratings across this class.";
  return `<div class="study-performance-score-result">
    <div class="study-performance-score-summary" role="status" aria-live="polite">${total}</div>
    ${categories ? `<ul class="study-performance-score-components">${categories}</ul>` : ""}
    <p class="field-hint">${escapeHtml(score?.explanation || "")} ${escapeHtml(scope)} Evidence: ${escapeHtml(evidence)}. Evidence level: ${escapeHtml(evidenceLevel)}. This is not a course grade or mastery estimate.</p>
    <div class="study-performance-score-actions"><button class="btn btn-ghost btn-small" type="button" data-clear-ai-score>Clear saved AI scores for this class</button></div>
  </div>`;
}

export function mountStudyPerformanceScore(container, materialId) {
  if (!container || !materialId) return () => {};
  let disposed = false;

  const renderError = (error) => {
    if (disposed || !container.isConnected) return;
    container.innerHTML = `<p class="field-hint" role="status">${escapeHtml(error?.message || "The AI study score could not be prepared.")}</p><button class="btn btn-secondary btn-small" type="button" data-retry-ai-score>Retry score</button>`;
    container.querySelector("[data-retry-ai-score]")?.addEventListener("click", load);
  };

  const bindClear = () => {
    container.querySelector("[data-clear-ai-score]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      if (!globalThis.confirm?.("Clear all saved AI study scores for this class? Your quizzes, exams, and flashcard review history will remain.")) return;
      button.disabled = true;
      try {
        await clearStudyPerformanceScoresApi(materialId);
        if (!disposed && container.isConnected) {
          container.innerHTML = `<p class="field-hint" role="status">Saved AI study scores for this class were cleared. New activity can create a fresh score.</p>`;
        }
      } catch (error) {
        button.disabled = false;
        renderError(error);
      }
    });
  };

  async function load() {
    if (disposed || !container.isConnected) return;
    container.innerHTML = `<p class="field-hint" role="status" aria-live="polite">Preparing your private AI study score from quiz, exam, and flashcard performance…</p>`;
    try {
      const score = await createStudyPerformanceScoreApi(materialId);
      if (disposed || !container.isConnected) return;
      container.innerHTML = studyPerformanceScoreMarkup(score);
      bindClear();
    } catch (error) {
      renderError(error);
    }
  }

  void load();
  return () => { disposed = true; };
}
