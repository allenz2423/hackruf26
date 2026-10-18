import { icon } from "./icons.js";
import { mathHtml, typesetMath } from "./math.js";
import { escapeHtml, focusStudyPlayer } from "./ui.js";
import { generatedStudyQualityNote } from "./study-quality-note.js";
import {
  clearFlashcardReviewStateApi,
  getFlashcardReviewStateApi,
  updateFlashcardReviewStateApi
} from "./api.js";

import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
import { mountStudyPerformanceScore } from "./study-performance-score.js";
export { formatSourceCitation } from "./source-citations.js";

/**
 * Normalizes flashcard data from backend content.cards, sample materials,
 * or question banks into a standard array of { id, front, back, sources }.
 */
export function normalizeCards(material) {
  if (!material) return [];

  let rawList = [];
  if (Array.isArray(material.cards) && material.cards.length > 0) {
    rawList = material.cards;
  } else if (Array.isArray(material.content?.cards) && material.content.cards.length > 0) {
    rawList = material.content.cards;
  } else if (Array.isArray(material.content) && material.content.length > 0) {
    rawList = material.content;
  } else if (Array.isArray(material.items) && material.items.length > 0) {
    rawList = material.items.map((item, i) => ({
      id: item.id || `item-${i + 1}`,
      front: item.prompt || item.front || item.term || `Concept ${i + 1}`,
      back: item.answer || item.back || item.definition || item.explanation || "Review course material.",
      sources: item.sources || []
    }));
  } else if (Array.isArray(material.questions) && material.questions.length > 0) {
    rawList = material.questions.map((q, i) => {
      if (typeof q === "object" && q !== null) {
        return {
          id: q.id || `q-${i + 1}`,
          front: q.front || q.prompt || `Card ${i + 1}`,
          back: q.back || q.answer || q.explanation || "Review course material.",
          sources: q.sources || []
        };
      }
      return {
        id: `q-${i + 1}`,
        front: String(q),
        back: `Review this concept in ${material.sources?.[0] || "your course notes"}.`,
        sources: material.sources || []
      };
    });
  }

  return rawList.map((card, idx) => {
    const id = card.id || `card-${idx + 1}`;
    const front = card.front || card.prompt || card.term || card.question || `Card ${idx + 1}`;
    const back = card.back || card.answer || card.definition || card.explanation || "No answer provided.";
    let sources = [];
    if (Array.isArray(card.sources)) {
      sources = card.sources;
    } else if (card.sources) {
      sources = [card.sources];
    } else if (Array.isArray(material.sources)) {
      sources = material.sources;
    }
    return { id, front, back, sources };
  });
}

const MAX_REFLECTION_CHARS = 1000;

export function flashcardReflectionHtml(idPrefix, reflection = {}) {
  const safePrefix = String(idPrefix || "flashcard")
    .replace(/[^a-zA-Z0-9_-]/gu, "-")
    .slice(0, 80);
  const field = (key, label, hint) => {
    const id = `${safePrefix}-${key}`;
    const value = String(reflection[key] || "").slice(0, MAX_REFLECTION_CHARS);
    return `
      <div class="flashcard-reflection-field">
        <label for="${id}">${label}</label>
        <textarea id="${id}" data-reflection-field="${key}" maxlength="${MAX_REFLECTION_CHARS}" rows="3" aria-describedby="${id}-hint ${id}-count">${escapeHtml(value)}</textarea>
        <div class="flashcard-reflection-field-meta">
          <span id="${id}-hint" class="field-hint">${hint}</span>
          <span id="${id}-count" class="flashcard-reflection-count" data-reflection-count>${value.length}/${MAX_REFLECTION_CHARS}</span>
        </div>
      </div>`;
  };

  return `
    <section class="flashcard-reflection" aria-labelledby="${safePrefix}-title">
      <div>
        <h3 id="${safePrefix}-title">Pause and reflect</h3>
        <p class="muted">A quick note can help you decide what to revisit. Reflections stay in this open study session; they are not sent or saved.</p>
      </div>
      ${field("knew", "What did you already know?", "Name the idea you could recall before revealing the answer.")}
      ${field("unclear", "What was unclear or missing?", "Note a gap, question, or detail to review later.")}
    </section>`;
}

function shuffleArray(items) {
  const clone = [...items];
  for (let i = clone.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [clone[i], clone[j]] = [clone[j], clone[i]];
  }
  return clone;
}

/**
 * Interactive flashcard player. Ratings are learner self-reports, never mastery
 * evidence; signed-in remote sets may persist only the rating and review date.
 */
export function renderFlashcards(root, material, { onBack, onAttemptCompleted } = {}) {
  const deck = normalizeCards(material);
  let activeCards = [...deck];
  let currentIndex = 0;
  let isFlipped = false;
  let isCompleted = false;
  let isSaving = false;
  let flashcardError = "";
  let privateStateStatus = "loading";
  const reviewState = new Map();
  const sessionRatings = new Map();
  const reflections = new Map();
  const pendingOperations = new Map();
  let hydrationVersion = 0;
  let clearedHistoryMessage = "";
  let scoreCleanup = null;
  const canPersist = Boolean(material?.isRemote && material?.id);

  function teardown() {
    hydrationVersion += 1;
    document.removeEventListener("keydown", handleKeyNavigation);
    reflections.clear();
    reflectionObserver?.disconnect();
    scoreCleanup?.();
  }

  let reflectionObserver = null;

  function handleKeyNavigation(event) {
    if (!root.isConnected || !root.querySelector(".flashcard-player")) {
      teardown();
      return;
    }
    const tag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || isCompleted || isSaving) return;
    if ((event.code === "Space" || event.key === " ") && tag !== "BUTTON") {
      event.preventDefault();
      flipCard();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      prevCard();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      nextCard();
    } else if (isFlipped && event.key === "1") {
      event.preventDefault();
      void rateCard("missed");
    } else if (isFlipped && event.key === "2") {
      event.preventDefault();
      void rateCard("recalled");
    } else if (isFlipped && event.key === "3") {
      event.preventDefault();
      void rateCard("unsure");
    }
  }

  document.addEventListener("keydown", handleKeyNavigation);
  if (typeof globalThis.MutationObserver === "function") {
    reflectionObserver = new MutationObserver(() => {
      if (!root.isConnected || !root.querySelector(".flashcard-player")) teardown();
    });
    reflectionObserver.observe(root, { childList: true, subtree: true });
  }
  root.closest?.("dialog")?.addEventListener("close", teardown, { once: true });

  function dueCards() {
    const now = Date.now();
    return deck.filter((card) => {
      const date = Date.parse(reviewState.get(card.id)?.next_due_at || "");
      return Number.isFinite(date) && date <= now;
    });
  }

  function flipCard() {
    if (isSaving || !activeCards[currentIndex]) return;
    isFlipped = !isFlipped;
    render();
  }

  function prevCard() {
    if (isSaving || currentIndex <= 0) return;
    currentIndex -= 1;
    isFlipped = false;
    render();
  }

  function nextCard() {
    if (isSaving) return;
    if (currentIndex < activeCards.length - 1) {
      currentIndex += 1;
      isFlipped = false;
      flashcardError = "";
      render();
    } else {
      finishDeck();
    }
  }

  function operationId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `flashcard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  async function rateCard(rating) {
    const card = activeCards[currentIndex];
    if (!card || !isFlipped || isSaving || !["recalled", "unsure", "missed"].includes(rating)) return;
    const pending = pendingOperations.get(card.id);
    if (pending && pending.action !== rating) {
      flashcardError = "Retry the same rating to confirm whether it was saved.";
      render();
      return;
    }
    isSaving = true;
    flashcardError = "";
    render();
    try {
      let updated = null;
      if (canPersist) {
        const operation = pending?.operation_id || operationId();
        pendingOperations.set(card.id, { operation_id: operation, action: rating });
        updated = await updateFlashcardReviewStateApi(material.id, {
          operation_id: operation,
          card_id: card.id,
          rating
        });
        pendingOperations.delete(card.id);
        reviewState.set(card.id, updated);
      }
      sessionRatings.set(card.id, rating);
      isSaving = false;
      nextCard();
    } catch (error) {
      isSaving = false;
      flashcardError = error.message || "This rating could not be saved. Try again.";
      render();
    }
  }

  async function adjustSchedule(action) {
    const card = activeCards[currentIndex];
    if (!card || !canPersist || isSaving || !["snooze", "reset"].includes(action)) return;
    const pending = pendingOperations.get(card.id);
    if (pending && pending.action !== action) {
      flashcardError = "Retry the same schedule change to confirm whether it was saved.";
      render();
      return;
    }
    isSaving = true;
    flashcardError = "";
    render();
    try {
      const operation = pending?.operation_id || operationId();
      pendingOperations.set(card.id, { operation_id: operation, action });
      const updated = await updateFlashcardReviewStateApi(material.id, {
        operation_id: operation,
        card_id: card.id,
        rating: action
      });
      pendingOperations.delete(card.id);
      reviewState.set(card.id, updated);
      isSaving = false;
      render();
    } catch (error) {
      isSaving = false;
      flashcardError = error.message || "The review date could not be changed. Try again.";
      render();
    }
  }

  function shuffleCards() {
    if (isSaving) return;
    activeCards = shuffleArray(activeCards);
    currentIndex = 0;
    isFlipped = false;
    render();
  }

  function startDueReview() {
    const due = dueCards();
    if (!due.length) return;
    activeCards = due;
    currentIndex = 0;
    isFlipped = false;
    isCompleted = false;
    sessionRatings.clear();
    render();
  }

  function finishDeck() {
    isCompleted = true;
    const counts = { recalled: 0, unsure: 0, missed: 0 };
    for (const rating of sessionRatings.values()) counts[rating] += 1;
    onAttemptCompleted?.({
      ...counts,
      ratedCount: sessionRatings.size,
      totalCount: activeCards.length,
      completedAt: new Date().toISOString(),
      masteryUpdated: false
    });
    render();
  }

  function restartAll() {
    activeCards = [...deck];
    currentIndex = 0;
    isFlipped = false;
    isCompleted = false;
    sessionRatings.clear();
    flashcardError = "";
    render();
  }

  function reviewMarkedPile() {
    const marked = deck.filter((card) => ["unsure", "missed"].includes(sessionRatings.get(card.id)));
    if (!marked.length) return;
    activeCards = marked;
    currentIndex = 0;
    isFlipped = false;
    isCompleted = false;
    sessionRatings.clear();
    render();
  }

  function formatDate(value) {
    const date = Date.parse(value || "");
    return Number.isFinite(date)
      ? new Date(date).toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : "not scheduled";
  }

  async function clearPrivateHistory(button) {
    if (!canPersist || isSaving) return;
    if (!globalThis.confirm?.("Clear your private review schedule for this flashcard set? This does not delete the cards.")) return;
    button.disabled = true;
    try {
      await clearFlashcardReviewStateApi(material.id);
      hydrationVersion += 1;
      reviewState.clear();
      sessionRatings.clear();
      pendingOperations.clear();
      clearedHistoryMessage = "Your private review schedule for this set was cleared.";
      render();
    } catch (error) {
      button.disabled = false;
      flashcardError = error.message || "The review schedule could not be cleared.";
      render();
    }
  }

  function renderCompletedSummary() {
    const total = activeCards.length;
    const counts = { recalled: 0, unsure: 0, missed: 0 };
    for (const rating of sessionRatings.values()) counts[rating] += 1;
    const marked = deck.filter((card) => ["unsure", "missed"].includes(sessionRatings.get(card.id)));
    const reflected = deck.flatMap((card) => {
      const note = reflections.get(card.id);
      if (!note || (!note.knew?.trim() && !note.unclear?.trim())) return [];
      return [{ card, note }];
    });
    scoreCleanup?.();
    scoreCleanup = null;
    const nextDue = [...reviewState.values()]
      .map((item) => item.next_due_at)
      .filter(Boolean)
      .sort((left, right) => Date.parse(left) - Date.parse(right))[0];
    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${generatedStudyQualityNote(material, "flashcards")}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}
        <div class="session-complete">
          <span class="kind-icon" style="color: var(--accent);">${icon("cards", { size: 36 })}</span>
          <h3>Review complete</h3>
          <p class="muted">You rated ${sessionRatings.size} of ${total} ${total === 1 ? "card" : "cards"}. These are planning signals, not a mastery score.</p>
          <div class="metric-strip" style="margin: 1rem 0; width: min(560px, 100%);">
            <div class="metric"><strong>${counts.recalled}</strong><span>Recalled</span></div>
            <div class="metric"><strong>${counts.unsure}</strong><span>Unsure</span></div>
            <div class="metric"><strong>${counts.missed}</strong><span>Missed</span></div>
          </div>
          ${canPersist && sessionRatings.size ? `<section class="panel study-performance-score" data-ai-study-score aria-label="AI study performance score"></section><p class="field-hint">Only your saved recalled, unsure, and missed ratings are included. Reflection notes stay in this browser session and are not sent.</p>` : ""}
          ${marked.length ? `<div class="flashcard-review-pile-list"><h4>Worth another look</h4><ul class="preview-list">${marked.map((card) => `<li><strong>${mathHtml(card.front)}</strong> <span class="muted">· ${sessionRatings.get(card.id)}</span></li>`).join("")}</ul></div>` : ""}
          ${reflected.length ? `<section class="flashcard-reflection-summary" aria-labelledby="flashcard-reflection-summary-title"><h4 id="flashcard-reflection-summary-title">Your reflections from this session</h4><ul>${reflected.map(({ card, note }) => `<li><strong>${mathHtml(card.front)}</strong>${note.knew?.trim() ? `<p><span>Already knew:</span> ${escapeHtml(note.knew.trim())}</p>` : ""}${note.unclear?.trim() ? `<p><span>Still unclear:</span> ${escapeHtml(note.unclear.trim())}</p>` : ""}</li>`).join("")}</ul></section>` : ""}
          ${canPersist && nextDue ? `<p class="field-hint">Next scheduled card review: ${escapeHtml(formatDate(nextDue))}. The starter intervals are simple planning defaults, not a personalized or scientifically optimal schedule.</p>` : ""}
          ${!canPersist ? `<p class="field-hint">This is a local set, so its ratings are only kept during this session.</p>` : `<p class="field-hint">Your account stores your rating and next review date for this set; it does not store your attempted answers or update concept mastery.</p>`}
          ${clearedHistoryMessage ? `<p class="field-hint" role="status">${escapeHtml(clearedHistoryMessage)}</p>` : ""}
          <div class="study-actions" style="justify-content:center;gap:.75rem;">
            ${marked.length ? `<button class="btn btn-primary" type="button" data-review-marked>${icon("refresh", { size: 16 })} Review ${marked.length} marked ${marked.length === 1 ? "card" : "cards"} now</button>` : ""}
            ${dueCards().length ? `<button class="btn btn-secondary" type="button" data-review-due>${icon("refresh", { size: 16 })} Review ${dueCards().length} due ${dueCards().length === 1 ? "card" : "cards"}</button>` : ""}
            <button class="btn ${marked.length ? "btn-secondary" : "btn-primary"}" type="button" data-restart-all>${icon("refresh", { size: 16 })} Review full set</button>
            ${canPersist ? `<button class="btn btn-ghost" type="button" data-clear-history>Clear my review history</button>` : ""}
            ${onBack ? `<button class="btn btn-ghost" type="button" data-back>Back to study materials</button>` : ""}
          </div>
        </div>
      </section>`;
    if (canPersist && sessionRatings.size) {
      scoreCleanup = mountStudyPerformanceScore(root.querySelector("[data-ai-study-score]"), material.id);
    }
    void typesetMath(root);
    bindSourceCitations(root, material);
    root.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => {
      teardown();
      onBack?.();
    }));
    root.querySelector("[data-restart-all]")?.addEventListener("click", restartAll);
    root.querySelector("[data-review-marked]")?.addEventListener("click", reviewMarkedPile);
    root.querySelector("[data-review-due]")?.addEventListener("click", startDueReview);
    root.querySelector("[data-clear-history]")?.addEventListener("click", (event) => void clearPrivateHistory(event.currentTarget));
  }

  function renderEmptyState() {
    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${generatedStudyQualityNote(material, "flashcards")}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}
        <div class="session-complete">
          <span class="kind-icon">${icon("cards", { size: 36 })}</span>
          <h3>No flashcards available</h3>
          <p class="muted">This study material does not have any cards to review.</p>
          ${onBack ? `<button class="btn btn-primary" type="button" data-back>Back to study materials</button>` : ""}
        </div>
      </section>`;
    bindSourceCitations(root, material);
    root.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => {
      teardown();
      onBack?.();
    }));
  }

  function render() {
    scoreCleanup?.();
    scoreCleanup = null;
    if (!activeCards.length) {
      renderEmptyState();
      return;
    }
    if (isCompleted) {
      renderCompletedSummary();
      return;
    }

    const card = activeCards[currentIndex];
    const total = activeCards.length;
    const state = reviewState.get(card.id);
    const rating = sessionRatings.get(card.id);
    const reflection = reflections.get(card.id) || { knew: "", unclear: "" };
    const pendingAction = pendingOperations.get(card.id)?.action;
    const dueCount = dueCards().length;
    const dueNow = Number.isFinite(Date.parse(state?.next_due_at || ""))
      && Date.parse(state.next_due_at) <= Date.now();
    const nextDueLabel = state?.next_due_at ? formatDate(state.next_due_at) : "not scheduled";
    const ratingLabel = { recalled: "Recalled", unsure: "Unsure", missed: "Missed" };
    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${generatedStudyQualityNote(material, "flashcards")}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}
        <header class="flashcard-header">
          <div class="study-progress">
            <span>Card ${currentIndex + 1} of ${total}</span>
            <progress max="${total}" value="${currentIndex + 1}" aria-label="Flashcard progress"></progress>
          </div>
          <div class="flashcard-header-actions">
            <div class="flashcard-piles-indicator" aria-label="Session ratings">
              <span class="pill"><strong>${[...sessionRatings.values()].filter((item) => item === "recalled").length}</strong> Recalled</span>
              <span class="pill"><strong>${[...sessionRatings.values()].filter((item) => item === "unsure").length}</strong> Unsure</span>
              <span class="pill"><strong>${[...sessionRatings.values()].filter((item) => item === "missed").length}</strong> Missed</span>
            </div>
            ${dueCount > 0 && dueCount < activeCards.length ? `<button class="btn btn-secondary btn-small" type="button" data-start-due>Review ${dueCount} due</button>` : ""}
            <button class="btn btn-ghost btn-small" type="button" data-shuffle title="Shuffle cards">${icon("shuffle", { size: 16 })} Shuffle</button>
          </div>
        </header>
        ${privateStateStatus === "loading" && canPersist ? `<p class="field-hint" role="status">Loading your private review schedule…</p>` : ""}
        ${privateStateStatus === "error" ? `<p class="field-hint" role="status">Your review schedule could not be loaded. You can continue; ratings will still be saved when possible.</p>` : ""}
        ${canPersist ? `<p class="field-hint">${dueCount ? `${dueCount} ${dueCount === 1 ? "card is" : "cards are"} due for review.` : "Your review dates are saved privately to your account."} ${state ? `This card is marked ${escapeHtml(ratingLabel[state.last_rating] || "for review")} · next review ${escapeHtml(nextDueLabel)}.` : ""}</p>` : ""}
        <div class="flashcard-stage-interactive">
          <button class="study-flashcard interactive-card ${isFlipped ? "is-flipped" : ""}" type="button" data-card-flip aria-pressed="${isFlipped}" aria-describedby="flashcard-retrieval-hint">
            <span class="flashcard-side-tag">
              <span class="flashcard-side-copy"><strong class="flashcard-face-label">${isFlipped ? "Answer" : "Question"}</strong><span class="flashcard-reveal-hint">${isFlipped ? "Answer shown" : "Try to recall it, then reveal"}</span></span>
              ${rating ? `<span class="pill">${ratingLabel[rating]}</span>` : dueNow ? `<span class="pill" data-tone="busy">Due</span>` : ""}
            </span>
            <span class="flashcard-content"><strong>${mathHtml(isFlipped ? card.back : card.front)}</strong></span>
            <span class="flashcard-reveal-hint">${isFlipped ? "Click to hide the answer" : "Reveal answer"}</span>
          </button>
          ${card.sources?.length ? `<div class="flashcard-card-sources">${card.sources.map(sourceCitationHtml).join("")}</div>` : ""}
        </div>
        ${isFlipped ? flashcardReflectionHtml(`flashcard-reflection-${currentIndex + 1}`, reflection) : ""}
        <p id="flashcard-retrieval-hint" class="field-hint" role="note">Take a moment to answer from memory before revealing. After reveal, choose how well you recalled it.</p>
        ${flashcardError ? `<p class="quiz-error" role="alert">${escapeHtml(flashcardError)}</p>` : ""}
        ${isSaving ? `<p class="field-hint" role="status" aria-live="polite">Saving your rating…</p>` : ""}
        ${isFlipped ? `
          <div class="flashcard-sorting-bar" aria-label="Rate your recall">
            <span class="field-hint">How well did you recall it?</span>
            <div class="flashcard-piles-buttons">
              <button class="btn btn-secondary pile-btn" type="button" data-rating="missed" ${isSaving || (pendingAction && pendingAction !== "missed") ? "disabled" : ""}>${pendingAction === "missed" ? "Retry missed" : "Missed"} <kbd class="kbd-hint">1</kbd></button>
              <button class="btn btn-secondary pile-btn" type="button" data-rating="unsure" ${isSaving || (pendingAction && pendingAction !== "unsure") ? "disabled" : ""}>${pendingAction === "unsure" ? "Retry unsure" : "Unsure"} <kbd class="kbd-hint">3</kbd></button>
              <button class="btn btn-primary pile-btn" type="button" data-rating="recalled" ${isSaving || (pendingAction && pendingAction !== "recalled") ? "disabled" : ""}>${pendingAction === "recalled" ? "Retry recalled" : "Recalled"} <kbd class="kbd-hint">2</kbd></button>
            </div>
            ${canPersist ? `<div class="flashcard-schedule-actions">
              <button class="btn btn-ghost btn-small" type="button" data-schedule="snooze" ${isSaving || (pendingAction && pendingAction !== "snooze") ? "disabled" : ""}>${pendingAction === "snooze" ? "Retry snooze" : "Snooze 1 day"}</button>
              ${state ? `<button class="btn btn-ghost btn-small" type="button" data-schedule="reset" ${isSaving || (pendingAction && pendingAction !== "reset") ? "disabled" : ""}>${pendingAction === "reset" ? "Retry reset" : "Reset this card"}</button>` : ""}
            </div>` : ""}
          </div>
        ` : `<div class="flashcard-sorting-bar"><span class="field-hint">Reveal the answer before rating your recall.</span></div>`}
        <footer class="study-actions flashcard-nav-bar">
          <button class="btn btn-secondary" type="button" data-prev ${currentIndex === 0 || isSaving ? "disabled" : ""}>${icon("arrowLeft", { size: 16 })} Previous <kbd class="kbd-hint">&larr;</kbd></button>
          <button class="btn btn-ghost" type="button" data-flip-btn ${isSaving ? "disabled" : ""}>${isFlipped ? "Hide answer" : "Reveal answer"}</button>
          <button class="btn btn-primary" type="button" data-next ${isSaving ? "disabled" : ""}>${currentIndex === total - 1 ? "Finish" : "Next card"} <kbd class="kbd-hint">&rarr;</kbd></button>
        </footer>
      </section>`;
    void typesetMath(root);
    bindSourceCitations(root, material);
    root.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => {
      teardown();
      onBack?.();
    }));
    root.querySelector("[data-card-flip]")?.addEventListener("click", flipCard);
    root.querySelector("[data-flip-btn]")?.addEventListener("click", flipCard);
    root.querySelector("[data-prev]")?.addEventListener("click", prevCard);
    root.querySelector("[data-next]")?.addEventListener("click", nextCard);
    root.querySelector("[data-shuffle]")?.addEventListener("click", shuffleCards);
    root.querySelector("[data-start-due]")?.addEventListener("click", startDueReview);
    root.querySelectorAll("[data-rating]").forEach((button) => button.addEventListener("click", () => void rateCard(button.dataset.rating)));
    root.querySelectorAll("[data-schedule]").forEach((button) => button.addEventListener("click", () => void adjustSchedule(button.dataset.schedule)));
    root.querySelectorAll("[data-reflection-field]").forEach((textarea) => textarea.addEventListener("input", () => {
      const current = reflections.get(card.id) || { knew: "", unclear: "" };
      const key = textarea.dataset.reflectionField;
      if (key !== "knew" && key !== "unclear") return;
      current[key] = textarea.value.slice(0, MAX_REFLECTION_CHARS);
      reflections.set(card.id, current);
      const counter = textarea.closest(".flashcard-reflection-field")?.querySelector("[data-reflection-count]");
      if (counter) counter.textContent = `${current[key].length}/${MAX_REFLECTION_CHARS}`;
    }));
  }

  function hydratePrivateState() {
    if (!canPersist) return;
    const requestedVersion = ++hydrationVersion;
    getFlashcardReviewStateApi(material.id).then((result) => {
      if (requestedVersion !== hydrationVersion || !root.querySelector(".flashcard-player")) return;
      for (const item of Array.isArray(result?.items) ? result.items : []) {
        if (typeof item?.card_id === "string"
          && deck.some((card) => card.id === item.card_id)
          && !sessionRatings.has(item.card_id)
          && !pendingOperations.has(item.card_id)) {
          reviewState.set(item.card_id, item);
        }
      }
      privateStateStatus = "ready";
      render();
    }).catch(() => {
      if (requestedVersion !== hydrationVersion || !root.querySelector(".flashcard-player")) return;
      privateStateStatus = "error";
      render();
    });
  }

  render();
  focusStudyPlayer(root);
  hydratePrivateState();
  return teardown;
}
