import { escapeHtml, toast } from "./ui.js";
import { icon } from "./icons.js";
import { renderFlashcards, normalizeCards, formatSourceCitation } from "./flashcards.js";
import { downloadStudyFileApi } from "./api.js";

function addDialog(title) {
  const dialog = document.createElement("dialog");
  dialog.className = "dialog material-dialog";
  dialog.setAttribute("aria-labelledby", "material-dialog-title");
  dialog.innerHTML = `
    <header class="preview-header">
      <div>
        <h2 id="material-dialog-title">${escapeHtml(title)}</h2>
        <p class="row-meta" id="material-dialog-meta"></p>
      </div>
      <button class="btn btn-secondary" type="button" data-close aria-label="Close study material">Close</button>
    </header>
    <div class="material-session" id="material-session"></div>
  `;
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
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
  let content = material.content;

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

function formatBodyParagraphs(body) {
  if (!body) return "";
  if (typeof body !== "string") {
    if (typeof body === "object") {
      try {
        return `<pre class="document-text">${escapeHtml(JSON.stringify(body, null, 2))}</pre>`;
      } catch {
        return "";
      }
    }
    body = String(body);
  }
  return body
    .split(/\n{2,}/)
    .filter((p) => p.trim())
    .map((p) => `<p>${escapeHtml(p.trim()).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function renderGuide(root, material) {
  const sections = parseGuideSections(material);
  const flashcardsAvailable = normalizeCards(material).length > 0;
  const questionsAvailable = (material.items?.length || material.questions?.length || 0) > 0;

  root.innerHTML = `
    <div class="study-mode-actions">
      ${material.isRemote ? `
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
          ${sections.map((section, idx) => `
            <section class="guide-section">
              <h3 class="guide-section-heading">${escapeHtml(section.heading || `Section ${idx + 1}`)}</h3>
              <div class="guide-section-body">
                ${formatBodyParagraphs(section.body)}
              </div>
              ${Array.isArray(section.key_terms) && section.key_terms.length > 0 ? `
                <div class="guide-meta-block">
                  <span class="guide-meta-label">Key terms:</span>
                  <div class="guide-chips">
                    ${section.key_terms.map((term) => `<span class="chip key-term-chip">${escapeHtml(term)}</span>`).join("")}
                  </div>
                </div>
              ` : ""}
              ${Array.isArray(section.sources) && section.sources.length > 0 ? `
                <div class="guide-meta-block">
                  <span class="guide-meta-label">Sources:</span>
                  <div class="guide-chips">
                    ${section.sources.map((src) => `<span class="chip source-chip">${escapeHtml(formatSourceCitation(src))}</span>`).join("")}
                  </div>
                </div>
              ` : ""}
            </section>
          `).join("")}
        </div>
      ` : `
        <p class="muted">This study guide has no written content yet.</p>
      `}
    </article>
  `;

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
    renderFlashcards(root, material, { onBack: () => renderGuide(root, material) });
  });

  root.querySelector("[data-practice]")?.addEventListener("click", () => {
    renderQuestions(root, material, () => renderGuide(root, material));
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
            return `<label><input type="radio" name="answer" value="${escapeHtml(val)}" ${selected === val ? "checked" : ""} /><span>${escapeHtml(label)}</span></label>`;
          }).join("")}
        </fieldset>` : `<p class="muted">Answer from memory, then reveal the review note.</p>`}
        <div class="answer-feedback" aria-live="polite">${isChecked ? hasChoices
          ? `<strong data-correct="${isCorrect}">${isCorrect ? "Correct" : `Answer: ${escapeHtml(item.answer)}`}</strong><p>${escapeHtml(item.explanation || "")}</p>`
          : `<strong>Review note</strong><p>${escapeHtml(item.answer || `Look for this idea in ${material.sources?.[0] || "your class materials"}.`)}</p>`
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

export function openMaterialViewer(material) {
  const dialog = addDialog(material.type || "Study material");
  const root = dialog.querySelector("#material-session");
  const count = material.items?.length || material.cards?.length || material.questions?.length || material.count || 0;
  const label = material.isSample ? "Sample material" : material.isRemote ? "AI-generated" : "Local draft";
  dialog.querySelector("#material-dialog-meta").textContent = `${material.scope || "Class review"} - ${count} ${count === 1 ? "item" : "items"} - ${label}`;

  if (material.type === "Flashcards") {
    renderFlashcards(root, material, {
      onBack: () => dialog.close()
    });
  } else if (material.type === "Study guide" && (material.content || material.sections)) {
    renderGuide(root, material);
  } else {
    renderQuestions(root, material);
  }
}
