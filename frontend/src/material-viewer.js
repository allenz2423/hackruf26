import { mathHtml, typesetMath } from "./math.js";
import { escapeHtml, toast } from "./ui.js";
import { icon } from "./icons.js";
import { avatarSvg } from "./avatar.js";
import { renderFlashcards, normalizeCards } from "./flashcards.js";
import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
import {
  clearGuideProgressApi,
  downloadStudyFileApi,
  getGuideProgressApi,
  submitGuideMiniQuizApi
} from "./api.js";
import { normalizeGuideBlocks } from "./study-guide/normalize.js";
import { mountStudyGuideBlocks, renderStudyGuideBlock } from "./study-guide/force-components.js";
import { createGuideChat } from "./guide-chat.js";

function addDialog(title, { resizable = false } = {}) {
  const dialog = document.createElement("dialog");
  dialog.className = `dialog material-dialog${resizable ? " material-dialog-resizable" : ""}`;
  if (resizable) dialog.setAttribute("title", "Drag the lower-right corner to resize this study guide");
  dialog.setAttribute("aria-labelledby", "material-dialog-title");
  dialog.innerHTML = `
    <header class="preview-header">
      <div>
        <h2 id="material-dialog-title">${escapeHtml(title)}</h2>
        <p class="row-meta" id="material-dialog-meta"></p>
      </div>
      <div class="preview-header-actions">
        <button class="btn btn-secondary" type="button" data-fullscreen hidden>Full screen</button>
        <a class="btn btn-secondary" data-open-in-new-tab target="_blank" rel="noopener noreferrer" hidden>Open in new tab</a>
        <button class="btn btn-secondary" type="button" data-close aria-label="Close study material">Close</button>
      </div>
    </header>
    <div class="material-session-layout">
      <div class="material-session" id="material-session"></div>
      <div data-guide-chat-host hidden></div>
    </div>
  `;
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  const fullscreenButton = dialog.querySelector("[data-fullscreen]");
  const updateFullscreenButton = () => {
    if (!fullscreenButton) return;
    const isFullscreen = document.fullscreenElement === dialog
      || document.webkitFullscreenElement === dialog
      || dialog.classList.contains("is-viewport-fullscreen");
    fullscreenButton.textContent = isFullscreen ? "Exit full screen" : "Full screen";
    fullscreenButton.setAttribute("aria-pressed", String(isFullscreen));
  };
  fullscreenButton?.addEventListener("click", async () => {
    const isNativeFullscreen = document.fullscreenElement === dialog
      || document.webkitFullscreenElement === dialog;
    const isViewportFullscreen = dialog.classList.contains("is-viewport-fullscreen");
    try {
      if (isNativeFullscreen) {
        const exit = document.exitFullscreen?.bind(document)
          || document.webkitExitFullscreen?.bind(document);
        if (exit) await exit();
        else dialog.classList.add("is-viewport-fullscreen");
      } else if (isViewportFullscreen) {
        dialog.classList.remove("is-viewport-fullscreen");
      } else {
        const request = dialog.requestFullscreen?.bind(dialog)
          || dialog.webkitRequestFullscreen?.bind(dialog);
        if (request) {
          await request();
        } else {
          dialog.classList.add("is-viewport-fullscreen");
          toast("This browser keeps its toolbar visible; the guide now fills the tab.", { tone: "info" });
        }
      }
    } catch {
      dialog.classList.add("is-viewport-fullscreen");
      toast("Browser fullscreen was blocked; the guide now fills this tab.", { tone: "info" });
    }
    updateFullscreenButton();
  });
  document.addEventListener?.("fullscreenchange", updateFullscreenButton);
  document.addEventListener?.("webkitfullscreenchange", updateFullscreenButton);
  dialog.addEventListener("close", () => {
    document.removeEventListener?.("fullscreenchange", updateFullscreenButton);
    document.removeEventListener?.("webkitfullscreenchange", updateFullscreenButton);
    if (document.fullscreenElement === dialog) void document.exitFullscreen?.();
    else if (document.webkitFullscreenElement === dialog) void document.webkitExitFullscreen?.();
  }, { once: true });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/**
 * Parses study guide content into structured sections:
 * [{ heading, body, key_terms, sources }]
 * Supports objects with sections, raw arrays, JSON strings, markdown headings, or plain text.
 */
export function parseGuideSections(material) {
  let content = material.previewContent || material.content;

  if (!content) {
    if (Array.isArray(material.sections) && material.sections.length > 0) {
      return material.sections;
    }
    return [];
  }

  // Attempt JSON parsing if string looks like JSON
  if (typeof content === "string") {
    const trimmed = content.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object") {
          content = parsed;
        }
      } catch {
        // Fall back to string parsing
      }
    }
  }

  // If content is an object with a sections array
  if (content && typeof content === "object") {
    if (Array.isArray(content.sections) && content.sections.length > 0) {
      return content.sections;
    }
    if (Array.isArray(content) && content.length > 0) {
      return content;
    }
    if (content.heading || content.body) {
      return [content];
    }
  }

  if (Array.isArray(material.sections) && material.sections.length > 0) {
    return material.sections;
  }

  // If content is a string, parse markdown headings (## or #) or numbered headings
  if (typeof content === "string") {
    const headerPattern = /^#{1,4}\s+(.+)$/gm;
    const matches = [...content.matchAll(headerPattern)];
    if (matches.length > 0) {
      const sections = [];
      for (let i = 0; i < matches.length; i++) {
        const match = matches[i];
        const heading = match[1].trim();
        const start = match.index + match[0].length;
        const end = i < matches.length - 1 ? matches[i + 1].index : content.length;
        const body = content.slice(start, end).trim();
        sections.push({
          heading,
          body,
          key_terms: [],
          sources: material.sources || []
        });
      }
      return sections;
    }

    // Numbered sections: "1. Membrane structure\nBody..."
    const numberedPattern = /(?:^|\n\n)(\d+\.\s+[^\n]+)\n([\s\S]*?)(?=(?:\n\n\d+\.\s+[^\n]+)|$)/g;
    const numberedMatches = [...content.matchAll(numberedPattern)];
    if (numberedMatches.length > 1) {
      return numberedMatches.map((m) => ({
        heading: m[1].replace(/^\d+\.\s*/, "").trim(),
        body: m[2].trim(),
        key_terms: [],
        sources: material.sources || []
      }));
    }

    return [{
      heading: material.scope || material.title || "Overview",
      body: content,
      key_terms: [],
      sources: material.sources || []
    }];
  }

  return [];
}

function guideSectionSources(section, blocks) {
  const sources = [...(Array.isArray(section.sources) ? section.sources : []), ...blocks.flatMap((block) => block.sources || [])];
  const seen = new Set();
  return sources.filter((source) => {
    let key;
    try { key = JSON.stringify(source); } catch { return false; }
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function guidePageNavigationHtml(currentPage, sectionCount) {
  if (sectionCount <= 1) return "";
  return `
    <nav class="guide-page-nav" aria-label="Study guide pagination">
      <button class="btn btn-secondary" type="button" data-guide-previous ${currentPage === 0 ? "disabled" : ""}>
        Previous section
      </button>
      <div class="guide-page-position" aria-live="polite">
        <span>Section ${currentPage + 1} of ${sectionCount}</span>
        <progress max="${sectionCount}" value="${currentPage + 1}" aria-label="Study guide progress"></progress>
      </div>
      <button class="btn btn-primary" type="button" data-guide-next ${currentPage === sectionCount - 1 ? "disabled" : ""}>
        Next section
      </button>
    </nav>
  `;
}

function newStudyOperationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("Secure review IDs are unavailable in this browser.");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function sectionProgressMap(payload) {
  const result = new Map();
  for (const item of Array.isArray(payload?.sections) ? payload.sections : []) {
    if (Number.isInteger(item?.section_index) && item.section_index >= 0) result.set(item.section_index, item);
  }
  return result;
}

function guideMiniQuizHtml(quiz, material, section, sectionIndex, anchorPrefix, quizStates, reviewMeta = null, onOpenChat = null) {
  if (!quiz || typeof quiz !== "object" || !Array.isArray(quiz.choices) || quiz.choices.length !== 4) {
    if (!onOpenChat) return "";
    return `
      <section class="guide-mini-quiz guide-mini-quiz-empty" aria-labelledby="guide-check-${sectionIndex + 1}-title">
        <p class="guide-mini-quiz-kicker">Section check</p>
        <h4 id="guide-check-${sectionIndex + 1}-title">No independent check yet</h4>
        <p>This saved section does not have a validated check. You can ask Jot to make a practice question; it will not be scored or added to review progress.</p>
        <button class="btn btn-secondary btn-small" type="button" data-guide-chat-practice>Ask Jot for a practice question</button>
      </section>
    `;
  }
  const quizKey = `${material.id || "local"}:${section.id || sectionIndex}`;
  const state = quizStates.get(quizKey) || { selected: null, feedback: null, operationId: null, error: "", submitting: false };
  const fieldsetId = `${anchorPrefix}-quiz-${sectionIndex + 1}`;
  const correct = state.feedback?.correct === true;
  const choices = quiz.choices.map((choice, index) => `
    <label class="guide-mini-quiz-choice" for="${fieldsetId}-choice-${index}">
      <input id="${fieldsetId}-choice-${index}" type="radio" name="${fieldsetId}" value="${index}"
        ${state.selected === index ? "checked" : ""} ${state.feedback || state.submitting || state.operationId ? "disabled" : ""}>
      <span>${mathHtml(choice)}</span>
    </label>
  `).join("");
  const dueText = state.feedback?.next_due_at || reviewMeta?.next_due_at;
  const dueLabel = dueText && Number.isFinite(Date.parse(dueText))
    ? new Date(dueText).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : null;
  const dueNow = dueText && Number.isFinite(Date.parse(dueText)) && Date.parse(dueText) <= Date.now();

  return `
    <section class="guide-mini-quiz" aria-labelledby="${fieldsetId}-title" data-guide-mini-quiz>
      <p class="guide-mini-quiz-kicker">Quick check</p>
      <h4 id="${fieldsetId}-title">Try this before moving on</h4>
      <p class="guide-mini-quiz-question">${mathHtml(quiz.question || "")}</p>
      <fieldset class="guide-mini-quiz-options" ${state.feedback || state.submitting || state.operationId ? "disabled" : ""}>
        <legend class="visually-hidden">Choose one answer</legend>
        ${choices}
      </fieldset>
      <div class="guide-mini-quiz-feedback${state.feedback ? (correct ? " is-correct" : " is-incorrect") : ""}"
        data-guide-mini-quiz-feedback role="status" aria-live="polite" ${state.feedback || state.error ? "" : "hidden"}>
        ${state.feedback ? `
          <strong>${correct ? "That’s right." : "Not quite yet."}</strong>
          <p>${mathHtml(state.feedback.explanation || "")}</p>
          ${!correct && state.feedback.correct_choice ? `<p class="guide-mini-quiz-answer">A better answer: <strong>${mathHtml(state.feedback.correct_choice)}</strong></p>` : ""}
          ${Number.isInteger(state.feedback.attempt_count) ? `<p class="guide-mini-quiz-due">Guide checks: ${state.feedback.attempt_count}. </p>` : Number.isInteger(reviewMeta?.attempt_count) && reviewMeta.attempt_count > 0 ? `<p class="guide-mini-quiz-due">Guide checks: ${reviewMeta.attempt_count}. </p>` : ""}
          ${dueLabel ? `<p class="guide-mini-quiz-due">Next review ${escapeHtml(dueLabel)} · separate from mastery.</p>` : `<p class="guide-mini-quiz-due">This check updates review timing only, not mastery.</p>`}
        ` : ""}
        ${state.error ? `<p>${escapeHtml(state.error)}</p>` : ""}
      </div>
      ${dueNow && onOpenChat ? `<div class="guide-mini-quiz-due-action"><p>This section is due to revisit. The fresh practice question is unscored and will not reset this schedule.</p><button class="btn btn-secondary btn-small" type="button" data-guide-chat-review>Try a fresh practice question</button></div>` : ""}
      <button class="btn btn-secondary btn-small" type="button" data-guide-mini-quiz-check
        ${state.selected === null || state.feedback || state.submitting ? "disabled" : ""}>
        ${state.submitting ? "Checking…" : state.operationId ? "Retry check" : "Check answer"}
      </button>
      ${state.feedback && !correct && onOpenChat ? `<button class="btn btn-ghost btn-small" type="button" data-guide-chat-new-practice>Ask for a different practice question</button>` : ""}
    </section>
  `;
}

function renderGuide(root, material, onRetry = null, {
  currentPage = 0,
  currentUserAvatar = null,
  quizStates = new Map(),
  reviewProgress = new Map(),
  onNavigate = null,
  onOpenChat = null,
  onReturnToGuide = null,
  onQuizStateChange = null,
  onClearProgress = null
} = {}) {
  const sections = parseGuideSections(material);
  const anchorPrefix = `guide-${String(material.id || "local").replace(/[^a-zA-Z0-9_-]/gu, "-")}`;
  const page = Math.max(0, Math.min(currentPage, Math.max(0, sections.length - 1)));
  const flashcardsAvailable = normalizeCards(material).length > 0;
  const questionsAvailable = (material.items?.length || material.questions?.length || 0) > 0;
  const section = sections[page];
  const blocks = section ? normalizeGuideBlocks(section, page) : [];
  const sources = section ? guideSectionSources(section, blocks) : [];
  const quizKey = section ? `${material.id || "local"}:${section.id || page}` : null;
  if (quizKey && !quizStates.has(quizKey)) quizStates.set(quizKey, { selected: null, feedback: null, operationId: null, error: "", submitting: false });

  root.innerHTML = `
    ${sourceWarningsHtml(material)}
    ${material.isRemote && material.status === "ready" ? `
      <p class="guide-quality-note" role="note">
        <strong>AI-generated · document checks passed.</strong>
        Structure and export were checked; factual claims and diagram meaning are not independently verified. Use the linked course sources to confirm details.
      </p>
    ` : ""}
    ${material.status === "generating" ? `
      <div class="generation-preview-banner" role="status" aria-live="polite">
        <strong>Live preview · not checked yet</strong>
        <span>Sections appear as they are drafted and may change. The saved guide and downloads are available after document validation and export finish.</span>
        <span>Closing this preview leaves generation running. Reopen it from Saved materials.</span>
      </div>
    ` : material.status === "failed" ? `
      <div class="generation-preview-banner generation-preview-error" role="status">
        <strong>Generation stopped before the guide was saved.</strong>
        <span>${sections.length
          ? "This partial preview is not saved or export-checked. Downloads are unavailable."
          : "No preview was saved before generation stopped. Downloads are unavailable."}</span>
        ${onRetry ? `<button class="btn btn-secondary btn-small" type="button" data-retry-guide>Retry generation</button>` : ""}
      </div>
    ` : ""}
      <div class="study-mode-actions">
      ${material.isRemote && material.status === "ready" ? `
        <button class="btn btn-primary" type="button" data-download-guide="pdf">Download PDF</button>
        <button class="btn btn-secondary" type="button" data-download-guide="latex">Download LaTeX</button>
        <span class="field-hint" role="status" data-download-status></span>
      ` : ""}
      ${flashcardsAvailable ? `
        <button class="btn btn-secondary" type="button" data-flashcards>
          ${icon("cards", { size: 16 })} Study flashcards
        </button>
      ` : ""}
        ${questionsAvailable ? `
        <button class="btn btn-secondary" type="button" data-practice>
          ${icon("quiz", { size: 16 })} Practice questions
        </button>
        ` : ""}
        ${material.isRemote && material.status === "ready" ? `<button class="btn btn-ghost btn-small" type="button" data-guide-progress-clear>Clear guide review schedule</button><span class="field-hint" data-guide-progress-status role="status" aria-live="polite"></span>` : ""}
      </div>

    ${sections.length > 1 ? `
      <nav class="guide-toc" aria-label="Study guide sections" data-guide-toc>
        <details>
          <summary><span>Jump to section</span><span class="guide-toc-count">${sections.length} sections</span></summary>
          <ol class="guide-toc-list">
            ${sections.map((section, idx) => `
              <li><button type="button" data-guide-page-target="${idx}" ${idx === page ? 'aria-current="page"' : ""}>
                <span class="guide-toc-number">${idx + 1}</span>
                <span>${escapeHtml(section.heading || `Section ${idx + 1}`)}</span>
              </button></li>
            `).join("")}
          </ol>
        </details>
      </nav>
    ` : ""}

    ${guidePageNavigationHtml(page, sections.length)}
    <article class="guide-content">
      ${section ? `
        <div class="guide-sections-list">
            <section id="${anchorPrefix}-section-${page + 1}" class="guide-section" data-guide-section="${escapeHtml(section.id || `section-${page + 1}`)}" tabindex="-1">
              <p class="guide-section-overline">Section ${page + 1} of ${sections.length}</p>
              <h3 class="guide-section-heading">${mathHtml(section.heading || `Section ${page + 1}`)}</h3>
              ${onOpenChat ? `<div class="guide-section-help"><button class="btn btn-secondary btn-small guide-chat-trigger" type="button" data-guide-chat-open aria-expanded="false"><span class="guide-chat-trigger-avatar" aria-hidden="true">${avatarSvg(currentUserAvatar, { size: 24 })}</span><span>Ask Jot!</span></button><span class="field-hint">Select a passage first to focus the question there; otherwise Jot uses this section.</span></div>` : ""}
              <div class="guide-section-blocks">
                ${blocks.map((block) => `<div class="guide-section-block" data-guide-block="${escapeHtml(block.id)}">${renderStudyGuideBlock(block)}</div>`).join("")}
              </div>
              ${Array.isArray(section.key_terms) && section.key_terms.length > 0 ? `
                <div class="guide-meta-block">
                  <span class="guide-meta-label">Key terms:</span>
                  <div class="guide-chips">
                    ${section.key_terms.map((term) => `<span class="chip key-term-chip">${mathHtml(term)}</span>`).join("")}
                  </div>
                </div>
              ` : ""}
              ${sources.length > 0 ? `
                <div class="guide-meta-block">
                  <span class="guide-meta-label">Sources:</span>
                  <div class="guide-chips">
                    ${sources.map(sourceCitationHtml).join("")}
                  </div>
                </div>
              ` : ""}
              ${guideMiniQuizHtml(section.mini_quiz, material, section, page, anchorPrefix, quizStates, reviewProgress.get(page), onOpenChat)}
            </section>
        </div>
      ` : `
        <p class="muted">${material.status === "generating"
          ? "Preparing the first section preview…"
          : material.status === "failed"
            ? "No complete guide was saved. Generate a new study guide to try again."
            : "This study guide has no written content yet."}</p>
      `}
    </article>
    ${guidePageNavigationHtml(page, sections.length)}
  `;
  root.querySelectorAll("[data-guide-page-target], [data-guide-previous], [data-guide-next]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!onNavigate) return;
      const target = button.hasAttribute("data-guide-page-target")
        ? Number(button.dataset.guidePageTarget)
        : page + (button.hasAttribute("data-guide-next") ? 1 : -1);
      if (!Number.isInteger(target) || target < 0 || target >= sections.length || target === page) return;
      const details = button.closest("details");
      if (details) details.open = false;
      onNavigate(target);
    });
  });

  root.querySelector("[data-guide-chat-open]")?.addEventListener("click", (event) => {
    const selection = globalThis.getSelection?.();
    let blockIndex = null;
    if (selection && !selection.isCollapsed && selection.rangeCount) {
      const node = selection.getRangeAt(0).commonAncestorContainer;
      const element = node.nodeType === 1 ? node : node.parentElement;
      const selectedBlock = element?.closest?.("[data-guide-block]");
      if (selectedBlock) {
        const id = selectedBlock.dataset.guideBlock;
        const foundIndex = blocks.findIndex((block) => String(block.id) === String(id));
        if (foundIndex >= 0) blockIndex = foundIndex;
      }
    }
    onOpenChat?.({ sectionIndex: page, sectionHeading: section?.heading || `Section ${page + 1}`, blockIndex, sourceTrigger: event.currentTarget });
  });

  const quiz = section?.mini_quiz;
  const quizState = quizKey ? (quizStates.get(quizKey) || { selected: null, feedback: null, operationId: null, error: "", submitting: false }) : null;
  if (quizKey && !quizStates.has(quizKey)) quizStates.set(quizKey, quizState);
  const quizFeedback = root.querySelector("[data-guide-mini-quiz-feedback]");
  const quizFieldset = root.querySelector(".guide-mini-quiz-options");
  const quizButton = root.querySelector("[data-guide-mini-quiz-check]");
  root.querySelectorAll("[data-guide-mini-quiz] input[type='radio']").forEach((input) => {
    input.addEventListener("change", () => {
      if (!quizState) return;
      quizState.selected = Number(input.value);
      if (quizState.feedback || quizState.operationId) return;
      if (quizFeedback) {
        quizFeedback.hidden = true;
        quizFeedback.innerHTML = "";
        quizFeedback.className = "guide-mini-quiz-feedback";
      }
      if (quizFieldset) quizFieldset.disabled = false;
      if (quizButton) {
        quizButton.disabled = false;
        quizButton.textContent = "Check answer";
      }
    });
  });
  quizButton?.addEventListener("click", async () => {
    if (!quizState || !quiz || !Number.isInteger(quizState.selected) || quizState.feedback || quizState.submitting) return;
    if (!material.isRemote && Number.isInteger(quiz.correct_index)) {
      const correct = quizState.selected === quiz.correct_index;
      quizState.feedback = {
        correct,
        correct_choice: correct ? "" : String(quiz.choices[quiz.correct_index] || ""),
        explanation: String(quiz.explanation || ""),
        next_due_at: null,
        attempt_count: null
      };
      onQuizStateChange?.();
      return;
    }
    if (!material.isRemote || !material.id) return;
    if (!quizState.operationId) {
      try { quizState.operationId = newStudyOperationId(); }
      catch (error) {
        quizState.error = error.message;
        if (quizFeedback) { quizFeedback.hidden = false; quizFeedback.textContent = error.message; }
        return;
      }
    }
    quizState.submitting = true;
    quizState.error = "";
    quizButton.disabled = true;
    quizButton.textContent = "Checking…";
    if (quizFieldset) quizFieldset.disabled = true;
    if (quizFeedback) {
      quizFeedback.hidden = false;
      quizFeedback.textContent = "Checking your answer…";
      quizFeedback.className = "guide-mini-quiz-feedback";
    }
    try {
      const result = await submitGuideMiniQuizApi(material.id, page, {
        operation_id: quizState.operationId,
        choice_index: quizState.selected
      });
      if (typeof result.correct !== "boolean" || typeof result.explanation !== "string") {
        throw new Error("The server could not confirm this answer. Retry to check the same choice safely.");
      }
      quizState.feedback = {
        correct: result.correct === true,
        correct_choice: typeof result.correct_choice === "string" ? result.correct_choice : "",
        explanation: typeof result.explanation === "string" ? result.explanation : "",
        next_due_at: result.next_due_at || null,
        attempt_count: Number.isInteger(result.attempt_count) ? result.attempt_count : null
      };
      quizState.operationId = null;
      quizState.error = "";
      reviewProgress.set(page, {
        section_index: page,
        attempt_count: quizState.feedback.attempt_count,
        last_correct: quizState.feedback.correct,
        last_attempt_at: result.last_attempt_at || null,
        next_due_at: quizState.feedback.next_due_at
      });
      quizState.submitting = false;
      onQuizStateChange?.();
    } catch (error) {
      quizState.error = error.message || "Your answer could not be checked. Retry to send the same answer safely.";
      if (quizFeedback) {
        quizFeedback.hidden = false;
        quizFeedback.textContent = quizState.error;
        quizFeedback.className = "guide-mini-quiz-feedback is-incorrect";
      }
      quizButton.disabled = false;
      quizButton.textContent = "Retry check";
    } finally {
      quizState.submitting = false;
    }
  });

  root.querySelector("[data-guide-chat-new-practice]")?.addEventListener("click", (event) => {
    onOpenChat?.({
      sectionIndex: page,
      sectionHeading: section?.heading || `Section ${page + 1}`,
      blockIndex: null,
      scope: "section",
      sourceTrigger: event.currentTarget,
      intent: "practice",
      message: "Give me a different application question about this section, without repeating the guide check."
    });
  });

  root.querySelectorAll("[data-guide-chat-practice], [data-guide-chat-review]").forEach((button) => {
    button.addEventListener("click", (event) => {
      onOpenChat?.({
        sectionIndex: page,
        sectionHeading: section?.heading || `Section ${page + 1}`,
        blockIndex: null,
        scope: "section",
        sourceTrigger: event.currentTarget,
        intent: "practice",
        message: "Give me one fresh, source-grounded practice question about this section. Do not repeat its saved check. Tell me this practice is unscored."
      });
    });
  });

  root.querySelector("[data-guide-progress-clear]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!globalThis.confirm?.("Clear this guide’s private review schedule? This does not delete the guide.")) return;
    button.disabled = true;
    try {
      await onClearProgress?.();
      reviewProgress.clear();
      quizStates.clear();
      onQuizStateChange?.();
    } catch (error) {
      const status = root.querySelector("[data-guide-progress-status]");
      if (status) status.textContent = error.message || "Could not clear this guide’s review schedule.";
    } finally {
      button.disabled = false;
    }
  });

  mountStudyGuideBlocks(root);
  void typesetMath(root);
  bindSourceCitations(root, material);

  root.querySelector("[data-retry-guide]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Starting retry…";
    try {
      const result = await onRetry(material);
      if (result?.status === "failed" && root.isConnected) {
        toast("The retry failed too. The saved preview is still provisional.", { tone: "error" });
      }
    } catch (error) {
      if (error?.name === "AbortError" || !root.isConnected) return;
      button.disabled = false;
      button.textContent = "Retry generation";
      toast(error.message || "Could not retry this study guide.", { tone: "error" });
    }
  });

  root.querySelectorAll("[data-download-guide]").forEach((button) => button.addEventListener("click", async () => {
    const buttons = root.querySelectorAll("[data-download-guide]");
    const status = root.querySelector("[data-download-status]");
    buttons.forEach((item) => { item.disabled = true; });
    status.textContent = "Preparing download…";
    try {
      const { blob, filename } = await downloadStudyFileApi(material.id, button.dataset.downloadGuide);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = "Downloaded. This file stays saved with your guide.";
    } catch (error) {
      status.textContent = error.message;
      toast(error.message, { tone: "error" });
    } finally {
      buttons.forEach((item) => { item.disabled = false; });
    }
  }));

  root.querySelector("[data-flashcards]")?.addEventListener("click", () => {
    renderFlashcards(root, material, {
      onBack: onReturnToGuide || (() => renderGuide(root, material, onRetry, { currentPage: page, quizStates, onNavigate }))
    });
  });

  root.querySelector("[data-practice]")?.addEventListener("click", () => {
    renderQuestions(root, material, onReturnToGuide || (() => renderGuide(root, material, onRetry, {
      currentPage: page, quizStates, onNavigate
    })));
  });
}

function renderQuestions(root, material, onReturn = null) {
  const items = material.items?.length
    ? material.items
    : (material.questions || ["Review the key ideas in this class."])
      .map((prompt) => ({ prompt, reflection: true }));
  const scoreableCount = items.filter((item) => Array.isArray(item.choices) && item.choices.length).length;
  let index = 0;
  let score = 0;
  const checked = new Set();
  const selections = new Map();

  function drawComplete() {
    const result = scoreableCount
      ? `${score} of ${scoreableCount} correct`
      : `You reviewed ${items.length} prompts.`;
    root.innerHTML = `
      <div class="session-complete">
        <h3>Review complete</h3>
        <p class="muted">${result}</p>
        <div class="study-actions" style="justify-content: center; gap: 0.5rem; margin-top: 1rem;">
          <button class="btn btn-primary" type="button" data-restart>Try again</button>
          ${onReturn ? `<button class="btn btn-secondary" type="button" data-return>Back to guide</button>` : ""}
        </div>
      </div>`;
    root.querySelector("[data-restart]").addEventListener("click", () => {
      index = 0;
      score = 0;
      checked.clear();
      selections.clear();
      draw();
    });
    root.querySelector("[data-return]")?.addEventListener("click", onReturn);
  }

  function draw() {
    if (index >= items.length) {
      drawComplete();
      return;
    }

    const item = items[index];
    const hasChoices = Array.isArray(item.choices) && item.choices.length > 0;
    const isChecked = checked.has(index);
    const selected = selections.get(index);
    const isCorrect = hasChoices && selected === item.answer;
    root.innerHTML = `
      <div class="study-progress"><span>Question ${index + 1} of ${items.length}</span><progress max="${items.length}" value="${index + 1}"></progress></div>
      <section class="question-panel">
        <h3>${escapeHtml(item.prompt || item)}</h3>
        ${hasChoices ? `<fieldset class="answer-options" ${isChecked ? "disabled" : ""}>
          ${item.choices.map((choice) => {
            const val = choice && typeof choice === "object" ? (choice.id ?? choice.text) : choice;
            const label = choice && typeof choice === "object" ? (choice.text ?? choice.label ?? choice.id) : choice;
            return `<label><input type="radio" name="answer" value="${escapeHtml(val)}" ${selected === val ? "checked" : ""} /><span>${mathHtml(label)}</span></label>`;
          }).join("")}
        </fieldset>` : `<p class="muted">Answer from memory, then reveal the review note.</p>`}
        <div class="answer-feedback" aria-live="polite">${isChecked ? hasChoices
          ? `<strong data-correct="${isCorrect}">${isCorrect ? "Correct" : `Answer: ${mathHtml(item.answer)}`}</strong><p>${mathHtml(item.explanation || "")}</p>`
          : `<strong>Review note</strong><p>${mathHtml(item.answer || `Look for this idea in ${material.sources?.[0] || "your class materials"}.`)}</p>`
          : ""}</div>
      </section>
      <div class="study-actions">
        ${onReturn ? `<button class="btn btn-secondary" type="button" data-return>Back to guide</button>` : ""}
        ${hasChoices
          ? isChecked
            ? `<button class="btn btn-primary" type="button" data-next>${index === items.length - 1 ? "See results" : "Next question"}</button>`
            : `<button class="btn btn-primary" type="button" data-check disabled>Check answer</button>`
          : isChecked
            ? `<button class="btn btn-primary" type="button" data-next>${index === items.length - 1 ? "Finish review" : "Next prompt"}</button>`
            : `<button class="btn btn-primary" type="button" data-reveal>Reveal review note</button>`}
      </div>
    `;
    void typesetMath(root);

    const checkButton = root.querySelector("[data-check]");
    if (checkButton) {
      root.querySelectorAll("input[name='answer']").forEach((input) => input.addEventListener("change", () => {
        selections.set(index, input.value);
        checkButton.disabled = false;
      }));
      checkButton.addEventListener("click", () => {
        if (selections.get(index) === item.answer) score += 1;
        checked.add(index);
        draw();
      });
    }
    root.querySelector("[data-reveal]")?.addEventListener("click", () => {
      checked.add(index);
      draw();
    });
    root.querySelector("[data-next]")?.addEventListener("click", () => {
      index += 1;
      draw();
    });
    root.querySelector("[data-return]")?.addEventListener("click", onReturn);
  }

  draw();
}

export function openMaterialViewer(material, {
  onRetry = null,
  initialFlashcardReview = false,
  currentUserAvatar = null
} = {}) {
  const dialog = addDialog(material.type || "Study material", { resizable: material.type === "Study guide" });
  const root = dialog.querySelector("#material-session");
  const chatHost = dialog.querySelector("[data-guide-chat-host]");
  let currentMaterial = material;
  const guidePositionKey = (materialId) => `jot-guide-position:${String(materialId || "local")}`;
  const readGuidePosition = (materialId) => {
    try {
      const value = Number(window.localStorage?.getItem(guidePositionKey(materialId)) || 0);
      return Number.isInteger(value) && value >= 0 ? value : 0;
    } catch {
      return 0;
    }
  };
  const saveGuidePosition = (materialId, page) => {
    try { window.localStorage?.setItem(guidePositionKey(materialId), String(page)); }
    catch { /* browser storage may be disabled */ }
  };
  let currentGuideMaterialId = String(material.id || "local");
  let currentGuidePage = readGuidePosition(currentGuideMaterialId);
  const miniQuizStates = new Map();
  const guideReviewProgress = new Map();
  const guideProgressController = new AbortController();
  let guideChat = null;
  let launchFlashcardDueReview = initialFlashcardReview;

  const renderCurrentGuidePage = () => {
    const sectionCount = parseGuideSections(currentMaterial).length;
    currentGuidePage = Math.max(0, Math.min(currentGuidePage, Math.max(0, sectionCount - 1)));
    renderGuide(
      root,
      currentMaterial,
      typeof onRetry === "function"
        ? (failedMaterial) => onRetry(failedMaterial, render)
        : null,
      {
        currentPage: currentGuidePage,
        currentUserAvatar,
        quizStates: miniQuizStates,
        reviewProgress: guideReviewProgress,
        onOpenChat: currentMaterial.isRemote && currentMaterial.status === "ready" ? (context) => {
          guideChat ||= createGuideChat(chatHost, {
            material: currentMaterial,
            classId: currentMaterial.classId,
            onApplyEdit: async (saved, proposal) => {
              const scrollTop = root.scrollTop;
              currentMaterial = {
                ...currentMaterial,
                content: saved.content,
                sections: Array.isArray(saved.content?.sections) ? saved.content.sections : currentMaterial.sections,
                updatedAt: saved.updated_at || currentMaterial.updatedAt
              };
              if (Number.isInteger(proposal?.section_index)) {
                guideReviewProgress.delete(proposal.section_index);
              }
              renderCurrentGuidePage();
              root.scrollTop = scrollTop;
            }
          });
          guideChat?.setContext({ sectionIndex: context.sectionIndex, sectionHeading: context.sectionHeading, material: currentMaterial });
          guideChat?.open(context);
        } : null,
        onReturnToGuide: renderCurrentGuidePage,
        onNavigate(page) {
          currentGuidePage = page;
          saveGuidePosition(currentGuideMaterialId, page);
          renderCurrentGuidePage();
          root.scrollTop = 0;
          root.querySelector(".guide-section")?.focus?.({ preventScroll: true });
        },
        async onClearProgress() {
          await clearGuideProgressApi(currentMaterial.id);
        },
        onQuizStateChange() {
          const scrollTop = root.scrollTop;
          renderCurrentGuidePage();
          root.scrollTop = scrollTop;
          const feedback = root.querySelector("[data-guide-mini-quiz-feedback]");
          feedback?.focus?.({ preventScroll: true });
        }
      }
    );
    const currentSection = parseGuideSections(currentMaterial)[currentGuidePage];
    guideChat?.setContext({
      sectionIndex: currentGuidePage,
      sectionHeading: currentSection?.heading || "Current section",
      material: currentMaterial,
      sourceTrigger: root.querySelector("[data-guide-chat-open]")
    });
  };

  const render = (nextMaterial) => {
    const nextGuideMaterialId = String(nextMaterial.id || "local");
    if (nextGuideMaterialId !== currentGuideMaterialId) {
      currentGuideMaterialId = nextGuideMaterialId;
      currentGuidePage = readGuidePosition(currentGuideMaterialId);
    }
    currentMaterial = nextMaterial;
    if (guideChat) guideChat.setContext({ sectionIndex: currentGuidePage, sectionHeading: parseGuideSections(nextMaterial)[currentGuidePage]?.heading || "Current section", material: nextMaterial });
    const scrollTop = root.scrollTop;
    const previewSectionCount = nextMaterial.previewContent?.sections?.length || 0;
    const count = nextMaterial.items?.length
      || nextMaterial.cards?.length
      || nextMaterial.questions?.length
      || nextMaterial.count
      || 0;
    const label = nextMaterial.status === "generating"
      ? "Live preview · not checked"
      : nextMaterial.status === "failed"
        ? "Generation failed"
        : nextMaterial.isSample ? "Sample material"
          : nextMaterial.isRemote ? "AI-generated" : "Local draft";
    const countLabel = nextMaterial.status === "generating"
      ? `${previewSectionCount}${count ? ` of ${count}` : ""} sections`
      : `${count} ${count === 1 ? "item" : "items"}`;
    const title = dialog.querySelector("#material-dialog-title");
    title.textContent = nextMaterial.title || nextMaterial.type || "Study material";
    const meta = dialog.querySelector("#material-dialog-meta");
    const guideSectionCount = nextMaterial.type === "Study guide"
      ? parseGuideSections(nextMaterial).length
      : null;
    const guideCountLabel = guideSectionCount === null
      ? countLabel
      : `${guideSectionCount} ${guideSectionCount === 1 ? "section" : "sections"}`;
    meta.textContent = `${nextMaterial.scope || "Class review"} - ${guideCountLabel} - ${label}`;

    const fullscreenButton = dialog.querySelector("[data-fullscreen]");
    if (fullscreenButton) fullscreenButton.hidden = nextMaterial.type !== "Study guide";
    const openInNewTabLink = dialog.querySelector("[data-open-in-new-tab]");
    if (openInNewTabLink) {
      const isRemoteGuide = nextMaterial.type === "Study guide" && nextMaterial.isRemote && nextMaterial.id;
      let href = "";
      if (isRemoteGuide && globalThis.location?.href) {
        const url = new URL(globalThis.location.href);
        url.hash = "";
        url.search = "";
        url.searchParams.set("study_guide", nextMaterial.id);
        href = `${url.pathname}${url.search}`;
      }
      openInNewTabLink.hidden = !href;
      if (href) openInNewTabLink.href = href;
    }

    if (nextMaterial.type === "Flashcards") {
      renderFlashcards(root, nextMaterial, {
        onBack: () => dialog.close(),
        initialDueReview: launchFlashcardDueReview
      });
      launchFlashcardDueReview = false;
    } else if (nextMaterial.type === "Study guide" && (
      nextMaterial.content || nextMaterial.sections || nextMaterial.previewContent
      || ["generating", "failed"].includes(nextMaterial.status)
    )) {
      renderCurrentGuidePage();
    } else {
      guideChat?.destroy();
      guideChat = null;
      renderQuestions(root, nextMaterial);
    }
    root.scrollTop = scrollTop;
  };

  render(material);
  if (material.isRemote && material.type === "Study guide" && material.status === "ready" && material.id) {
    getGuideProgressApi(material.id, { signal: guideProgressController.signal }).then((result) => {
      if (!dialog.open || guideProgressController.signal.aborted) return;
      guideReviewProgress.clear();
      for (const [index, item] of sectionProgressMap(result)) guideReviewProgress.set(index, item);
      const scrollTop = root.scrollTop;
      renderCurrentGuidePage();
      root.scrollTop = scrollTop;
    }).catch(() => {
      if (guideProgressController.signal.aborted || !dialog.open) return;
      const status = root.querySelector("[data-guide-progress-status]");
      if (status) status.textContent = "Private review timing could not be loaded. You can still read and use this guide.";
    });
  }
  dialog.addEventListener("close", () => {
    guideProgressController.abort();
    guideChat?.destroy();
    guideChat = null;
  }, { once: true });
  return {
    dialog,
    close: () => dialog.close(),
    get isOpen() { return dialog.open; },
    update(nextMaterial) {
      if (!dialog.open) return false;
      if (
        nextMaterial.status === currentMaterial.status
        && nextMaterial.previewRevision === currentMaterial.previewRevision
      ) return false;
      if (
        nextMaterial.status === "failed"
        && !nextMaterial.previewContent
        && currentMaterial.previewContent
      ) {
        nextMaterial = {
          ...nextMaterial,
          previewContent: currentMaterial.previewContent,
          previewRevision: currentMaterial.previewRevision
        };
      }
      render(nextMaterial);
      return true;
    }
  };
}
