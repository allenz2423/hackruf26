import { icon } from "./icons.js";
import { mathHtml, typesetMath } from "./math.js";

import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
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

function shuffleArray(items) {
  const clone = [...items];
  for (let i = clone.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [clone[i], clone[j]] = [clone[j], clone[i]];
  }
  return clone;
}

/**
 * Interactive flashcard player component.
 * Supports flipping, prev/next navigation, keyboard shortcuts,
 * Got it / Study again piles, shuffling, progress indicator, and completion summary.
 */
export function renderFlashcards(root, material, { onBack, onAttemptCompleted } = {}) {
  let deck = normalizeCards(material);
  let activeCards = [...deck];
  let currentIndex = 0;
  let isFlipped = false;
  const masteredIds = new Set();
  const studyAgainIds = new Set();
  let isCompleted = false;

  function teardown() {
    document.removeEventListener("keydown", handleKeyNavigation);
  }

  function handleKeyNavigation(event) {
    if (!root.isConnected || !root.querySelector(".flashcard-player")) {
      teardown();
      return;
    }

    const tag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(tag)) {
      return;
    }

    if (isCompleted) return;

    if (event.code === "Space" || event.key === " ") {
      event.preventDefault();
      flipCard();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      prevCard();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      nextCard();
    } else if (event.key === "1") {
      event.preventDefault();
      markStudyAgain();
    } else if (event.key === "2") {
      event.preventDefault();
      markGotIt();
    }
  }

  document.addEventListener("keydown", handleKeyNavigation);

  function flipCard() {
    isFlipped = !isFlipped;
    render();
  }

  function prevCard() {
    if (currentIndex > 0) {
      currentIndex -= 1;
      isFlipped = false;
      render();
    }
  }

  function nextCard() {
    if (currentIndex < activeCards.length - 1) {
      currentIndex += 1;
      isFlipped = false;
      render();
    } else {
      finishDeck();
    }
  }

  function markGotIt() {
    if (!activeCards[currentIndex]) return;
    const cardId = activeCards[currentIndex].id;
    masteredIds.add(cardId);
    studyAgainIds.delete(cardId);
    nextCard();
  }

  function markStudyAgain() {
    if (!activeCards[currentIndex]) return;
    const cardId = activeCards[currentIndex].id;
    studyAgainIds.add(cardId);
    masteredIds.delete(cardId);
    nextCard();
  }

  function shuffleCards() {
    activeCards = shuffleArray(activeCards);
    currentIndex = 0;
    isFlipped = false;
    render();
  }

  function finishDeck() {
    isCompleted = true;
    if (onAttemptCompleted) {
      const scorePercent = activeCards.length > 0
        ? Math.round((masteredIds.size / activeCards.length) * 100)
        : 100;
      onAttemptCompleted({
        scorePercent,
        masteredCount: masteredIds.size,
        studyAgainCount: studyAgainIds.size,
        totalCount: activeCards.length,
        completedAt: new Date().toISOString()
      });
    }
    render();
  }

  function restartAll() {
    activeCards = [...deck];
    currentIndex = 0;
    isFlipped = false;
    isCompleted = false;
    masteredIds.clear();
    studyAgainIds.clear();
    render();
  }

  function reviewStudyAgainPile() {
    const subset = deck.filter((c) => studyAgainIds.has(c.id));
    if (!subset.length) return;
    activeCards = subset;
    currentIndex = 0;
    isFlipped = false;
    isCompleted = false;
    masteredIds.clear();
    studyAgainIds.clear();
    render();
  }

  function renderCompletedSummary() {
    const total = activeCards.length;
    const mastered = masteredIds.size;
    const studyAgain = studyAgainIds.size;
    const percent = total > 0 ? Math.round((mastered / total) * 100) : 100;
    const cardsToReview = deck.filter((c) => studyAgainIds.has(c.id));

    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}
        <div class="session-complete">
          <span class="kind-icon" style="color: var(--accent);">${icon("cards", { size: 36 })}</span>
          <h3>Deck review complete</h3>
          <p class="muted">You reviewed ${total} ${total === 1 ? "flashcard" : "flashcards"}.</p>

          <div class="metric-strip" style="margin: 1rem 0; width: min(440px, 100%);">
            <div class="metric">
              <strong style="color: var(--success);">${mastered}</strong>
              <span>Got it (${percent}%)</span>
            </div>
            <div class="metric">
              <strong style="color: ${studyAgain > 0 ? "var(--warning)" : "var(--muted);"}">${studyAgain}</strong>
              <span>Study again</span>
            </div>
          </div>

          ${cardsToReview.length > 0 ? `
            <div class="flashcard-review-pile-list" style="width: min(520px, 100%); margin: 0.5rem 0 1rem; text-align: left;">
              <h4 style="font-size: 0.9375rem; margin-bottom: 0.5rem; color: var(--muted);">Cards to review again:</h4>
              <ul class="preview-list" style="margin: 0;">
                ${cardsToReview.map((c) => `<li><strong>${mathHtml(c.front)}</strong></li>`).join("")}
              </ul>
            </div>
          ` : `
            <p style="color: var(--success); font-weight: 600; margin-bottom: 1rem;">Great work! You mastered every card in this set.</p>
          `}

          <div class="study-actions" style="justify-content: center; gap: 0.75rem;">
            ${cardsToReview.length > 0 ? `
              <button class="btn btn-primary" type="button" data-study-again-pile>
                ${icon("refresh", { size: 16 })} Review ${cardsToReview.length} ${cardsToReview.length === 1 ? "card" : "cards"} to study again
              </button>
            ` : ""}
            <button class="btn ${cardsToReview.length > 0 ? "btn-secondary" : "btn-primary"}" type="button" data-restart-all>
              ${icon("refresh", { size: 16 })} Review again
            </button>
            ${onBack ? `<button class="btn btn-ghost" type="button" data-back>Back to study materials</button>` : ""}
          </div>
        </div>
      </section>
    `;
    void typesetMath(root);
    bindSourceCitations(root, material);

    root.querySelectorAll("[data-back]").forEach((btn) => {
      btn.addEventListener("click", () => {
        teardown();
        onBack?.();
      });
    });

    root.querySelector("[data-restart-all]")?.addEventListener("click", restartAll);
    root.querySelector("[data-study-again-pile]")?.addEventListener("click", reviewStudyAgainPile);
  }

  function renderEmptyState() {
    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}
        <div class="session-complete">
          <span class="kind-icon">${icon("cards", { size: 36 })}</span>
          <h3>No flashcards available</h3>
          <p class="muted">This study material does not have any cards to review.</p>
          ${onBack ? `<button class="btn btn-primary" type="button" data-back>Back to study materials</button>` : ""}
        </div>
      </section>
    `;
    void typesetMath(root);
    bindSourceCitations(root, material);

    root.querySelectorAll("[data-back]").forEach((btn) => {
      btn.addEventListener("click", () => {
        teardown();
        onBack?.();
      });
    });
  }

  function render() {
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
    const isFirst = currentIndex === 0;
    const isLast = currentIndex === total - 1;
    const isCurrentMastered = masteredIds.has(card.id);
    const isCurrentStudyAgain = studyAgainIds.has(card.id);

    root.innerHTML = `
      <section class="flashcard-player assessment-view">
        ${sourceWarningsHtml(material)}
        ${onBack ? `<button class="back-link assessment-back" type="button" data-back>&larr; Back to study materials</button>` : ""}

        <header class="flashcard-header">
          <div class="study-progress">
            <span>Card ${currentIndex + 1} of ${total}</span>
            <progress max="${total}" value="${currentIndex + 1}"></progress>
          </div>

          <div class="flashcard-header-actions">
            <div class="flashcard-piles-indicator" aria-label="Sorting status">
              <span class="pill" data-tone="good" title="Mastered cards">
                <strong>✓ ${masteredIds.size}</strong> Got it
              </span>
              <span class="pill" data-tone="${studyAgainIds.size > 0 ? "busy" : ""}" title="Cards to review">
                <strong>↻ ${studyAgainIds.size}</strong> Study again
              </span>
            </div>
            <button class="btn btn-ghost btn-small" type="button" data-shuffle title="Shuffle cards">
              ${icon("shuffle", { size: 16 })} Shuffle
            </button>
          </div>
        </header>

        <div class="flashcard-stage-interactive">
          <div
            class="study-flashcard interactive-card ${isFlipped ? "is-flipped" : ""}"
            role="button"
            tabindex="0"
            data-card-flip
            aria-label="${isFlipped ? "Answer side shown. Click or press Space to show question." : "Question side shown. Click or press Space to reveal answer."}"
          >
            <div class="flashcard-side-tag">
              <div class="flashcard-side-copy">
                <strong class="flashcard-face-label">${isFlipped ? "Answer" : "Question"}</strong>
                <span class="flashcard-reveal-hint">Click card or press Space to ${isFlipped ? "flip back" : "reveal"}</span>
              </div>
              ${isCurrentMastered ? `<span class="pill" data-tone="good" style="margin-left: auto;">Got it</span>` : ""}
              ${isCurrentStudyAgain ? `<span class="pill" data-tone="busy" style="margin-left: auto;">Needs review</span>` : ""}
            </div>

            <div class="flashcard-content">
              <strong>${mathHtml(isFlipped ? card.back : card.front)}</strong>
            </div>

          </div>
            ${card.sources?.length ? `
              <div class="flashcard-card-sources">
                ${card.sources.map(sourceCitationHtml).join("")}
              </div>
            ` : ""}
        </div>

        <div class="flashcard-sorting-bar">
          <span class="field-hint">Sort card:</span>
          <div class="flashcard-piles-buttons">
            <button
              class="btn ${isCurrentStudyAgain ? "btn-secondary" : "btn-secondary"} pile-btn study-again-btn"
              type="button"
              data-study-again
              title="Mark to study again (Shortcut: 1)"
            >
              ${icon("refresh", { size: 16 })} Study again <kbd class="kbd-hint">1</kbd>
            </button>
            <button
              class="btn ${isCurrentMastered ? "btn-primary" : "btn-primary"} pile-btn got-it-btn"
              type="button"
              data-got-it
              title="Mark as mastered (Shortcut: 2)"
            >
              ${icon("check", { size: 16 })} Got it <kbd class="kbd-hint">2</kbd>
            </button>
          </div>
        </div>

        <footer class="study-actions flashcard-nav-bar">
          <button class="btn btn-secondary" type="button" data-prev ${isFirst ? "disabled" : ""}>
            ${icon("arrowLeft", { size: 16 })} Previous <kbd class="kbd-hint">&larr;</kbd>
          </button>
          <button class="btn btn-ghost" type="button" data-flip-btn>
            Flip card <kbd class="kbd-hint">Space</kbd>
          </button>
          <button class="btn btn-primary" type="button" data-next>
            ${isLast ? "Finish" : "Next card"} <kbd class="kbd-hint">&rarr;</kbd>
          </button>
        </footer>
      </section>
    `;
    void typesetMath(root);
    bindSourceCitations(root, material);

    root.querySelectorAll("[data-back]").forEach((btn) => {
      btn.addEventListener("click", () => {
        teardown();
        onBack?.();
      });
    });

    const cardElement = root.querySelector("[data-card-flip]");
    if (cardElement) {
      cardElement.addEventListener("click", flipCard);
      cardElement.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          flipCard();
        }
      });
    }

    root.querySelector("[data-flip-btn]")?.addEventListener("click", flipCard);
    root.querySelector("[data-prev]")?.addEventListener("click", prevCard);
    root.querySelector("[data-next]")?.addEventListener("click", nextCard);
    root.querySelector("[data-got-it]")?.addEventListener("click", markGotIt);
    root.querySelector("[data-study-again]")?.addEventListener("click", markStudyAgain);
    root.querySelector("[data-shuffle]")?.addEventListener("click", shuffleCards);
  }

  render();
}
