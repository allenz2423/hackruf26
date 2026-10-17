import { mathHtml, typesetMath } from "./math.js";
import { escapeHtml, toast } from "./ui.js";
import { icon } from "./icons.js";
import { renderFlashcards, normalizeCards } from "./flashcards.js";
import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
import { downloadStudyFileApi } from "./api.js";
import { normalizeGuideBlocks } from "./study-guide/normalize.js";
import { mountStudyGuideBlocks, renderStudyGuideBlock } from "./study-guide/force-components.js";

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
    <div class="material-session" id="material-session"></div>
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

function renderGuide(root, material, onRetry = null) {
  const sections = parseGuideSections(material);
  const flashcardsAvailable = normalizeCards(material).length > 0;
  const questionsAvailable = (material.items?.length || material.questions?.length || 0) > 0;

  root.innerHTML = `
    ${sourceWarningsHtml(material)}
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
    </div>

    <article class="guide-content">
      ${sections.length > 0 ? `
        <div class="guide-sections-list">
          ${sections.map((section, idx) => {
            const blocks = normalizeGuideBlocks(section, idx);
            const sources = guideSectionSources(section, blocks);
            return `
            <section class="guide-section" data-guide-section="${escapeHtml(section.id || `section-${idx + 1}`)}">
              <h3 class="guide-section-heading">${mathHtml(section.heading || `Section ${idx + 1}`)}</h3>
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
            </section>
          `;
          }).join("")}
        </div>
      ` : `
        <p class="muted">${material.status === "generating"
          ? "Preparing the first section preview…"
          : material.status === "failed"
            ? "No complete guide was saved. Generate a new study guide to try again."
            : "This study guide has no written content yet."}</p>
      `}
    </article>
  `;
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
    renderFlashcards(root, material, { onBack: () => renderGuide(root, material, onRetry) });
  });

  root.querySelector("[data-practice]")?.addEventListener("click", () => {
    renderQuestions(root, material, () => renderGuide(root, material, onRetry));
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

export function openMaterialViewer(material, { onRetry = null } = {}) {
  const dialog = addDialog(material.type || "Study material", { resizable: material.type === "Study guide" });
  const root = dialog.querySelector("#material-session");
  let currentMaterial = material;

  const render = (nextMaterial) => {
    currentMaterial = nextMaterial;
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
    meta.textContent = `${nextMaterial.scope || "Class review"} - ${countLabel} - ${label}`;

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
        onBack: () => dialog.close()
      });
    } else if (nextMaterial.type === "Study guide" && (
      nextMaterial.content || nextMaterial.sections || nextMaterial.previewContent
      || ["generating", "failed"].includes(nextMaterial.status)
    )) {
      renderGuide(root, nextMaterial, typeof onRetry === "function"
        ? (failedMaterial) => onRetry(failedMaterial, render)
        : null);
    } else {
      renderQuestions(root, nextMaterial);
    }
    root.scrollTop = scrollTop;
  };

  render(material);
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
