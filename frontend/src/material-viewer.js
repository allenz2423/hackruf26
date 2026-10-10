import { escapeHtml } from "./ui.js";

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

function renderGuide(root, material) {
  root.innerHTML = `
    ${material.items?.length ? `<div class="study-mode-actions">
      <button class="btn btn-secondary" type="button" data-practice>Practice questions</button>
    </div>` : ""}
    <article class="guide-content"><pre class="document-text">${escapeHtml(material.content || "This study guide has no written content yet.")}</pre></article>
  `;
  root.querySelector("[data-practice]")?.addEventListener("click", () => renderQuestions(root, material, () => renderGuide(root, material)));
}

function renderFlashcards(root, material) {
  const cards = material.cards?.length
    ? material.cards
    : (material.questions || ["Review this topic"])
      .map((front) => ({ front, back: `Review this concept in ${material.sources?.[0] || "your class materials"}.` }));
  let index = 0;
  let flipped = false;

  function draw() {
    const card = cards[index];
    root.innerHTML = `
      <div class="study-progress"><span>Card ${index + 1} of ${cards.length}</span><progress max="${cards.length}" value="${index + 1}"></progress></div>
      <button class="study-flashcard ${flipped ? "is-flipped" : ""}" type="button" data-flip aria-label="${flipped ? "Show question" : "Reveal answer"}">
        <span class="field-hint">${flipped ? "Answer" : "Question"} - Select card to ${flipped ? "flip back" : "reveal"}</span>
        <strong>${escapeHtml(flipped ? card.back : card.front)}</strong>
      </button>
      <div class="study-actions">
        <button class="btn btn-secondary" type="button" data-prev ${index === 0 ? "disabled" : ""}>Previous</button>
        <button class="btn btn-primary" type="button" data-next>${index === cards.length - 1 ? "Finish" : "Next card"}</button>
      </div>
    `;
    root.querySelector("[data-flip]").addEventListener("click", () => {
      flipped = !flipped;
      draw();
    });
    root.querySelector("[data-prev]").addEventListener("click", () => {
      index -= 1;
      flipped = false;
      draw();
    });
    root.querySelector("[data-next]").addEventListener("click", () => {
      if (index < cards.length - 1) {
        index += 1;
        flipped = false;
        draw();
        return;
      }
      root.innerHTML = `<div class="session-complete"><h3>Deck complete</h3><p class="muted">You reviewed ${cards.length} ${cards.length === 1 ? "card" : "cards"}.</p><button class="btn btn-primary" type="button" data-restart>Review again</button></div>`;
      root.querySelector("[data-restart]").addEventListener("click", () => {
        index = 0;
        flipped = false;
        draw();
      });
    });
  }

  draw();
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
        <button class="btn btn-primary" type="button" data-restart>Try again</button>
      </div>`;
    root.querySelector("[data-restart]").addEventListener("click", () => {
      index = 0;
      score = 0;
      checked.clear();
      selections.clear();
      draw();
    });
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
          <legend class="visually-hidden">Choose an answer</legend>
          ${item.choices.map((choice) => `<label><input type="radio" name="answer" value="${escapeHtml(choice)}" ${selected === choice ? "checked" : ""} /><span>${escapeHtml(choice)}</span></label>`).join("")}
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
  const label = material.isSample ? "Sample material" : "Local draft";
  dialog.querySelector("#material-dialog-meta").textContent = `${material.scope || "Class review"} - ${count} ${count === 1 ? "item" : "items"} - ${label}`;

  if (material.type === "Flashcards") {
    renderFlashcards(root, material);
  } else if (material.type === "Study guide" && material.content) {
    renderGuide(root, material);
  } else {
    renderQuestions(root, material);
  }
}
