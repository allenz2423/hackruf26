import { icon } from "./icons.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { escapeHtml, toast } from "./ui.js";
import { openMaterialViewer } from "./material-viewer.js";

const TYPES = [
  { value: "Quiz", icon: "quiz", hint: "Quick check" },
  { value: "Practice test", icon: "exam", hint: "Timed, exam-style" },
  { value: "Study guide", icon: "guide", hint: "Key ideas summary" },
  { value: "Flashcards", icon: "cards", hint: "Terms and recall" }
];

const TYPE_ICONS = Object.fromEntries(TYPES.map((type) => [type.value, type.icon]));

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function scoreableQuestions(material) {
  const questions = Array.isArray(material.questions) ? material.questions : [];
  const playableQuestions = questions.filter((question) => question
    && typeof question === "object"
    && typeof question.prompt === "string"
    && Array.isArray(question.choices)
    && Number.isInteger(question.answerIndex));
  if (playableQuestions.length) return playableQuestions;

  return (material.items || []).flatMap((item, index) => {
    if (!item.prompt || !Array.isArray(item.choices)) return [];
    const answerIndex = item.choices.indexOf(item.answer);
    if (answerIndex < 0) return [];
    return [{
      id: item.id || `${material.id}-question-${index + 1}`,
      prompt: item.prompt,
      choices: item.choices,
      answerIndex,
      explanation: item.explanation || ""
    }];
  });
}

function sampleQuestions(type, klass) {
  const subject = klass.focus || klass.name;
  if (type === "Flashcards") return [`Key term from ${subject}`, "Common misconception", "Source-backed definition"];
  if (type === "Study guide") return [`Core concepts in ${subject}`, "Important examples", "What to review next"];
  if (type === "Practice test") return [`Timed free response on ${subject}`, "Explain your reasoning", "Mixed review problem"];
  return [`Quick check on ${subject}`, "Multiple choice concept check", "Short answer with explanation"];
}

function createLocalMaterial(type, klass, requestedCount) {
  const bank = klass.materials.flatMap((material) => material.items || []);
  const sources = klass.uploads.slice(0, 2).map((upload) => upload.name);
  const content = klass.materials.find((material) => material.type === "Study guide")?.content
    || klass.uploads.map((upload) => upload.content || upload.summary).filter(Boolean).join("\n\n");
  const base = {
    id: crypto.randomUUID(),
    type,
    scope: "Whole class",
    difficulty: "Mixed",
    status: "ready",
    createdAt: new Date().toISOString(),
    sources,
    isSample: false
  };

  if (type === "Flashcards") {
    const cards = klass.materials.flatMap((material) => material.cards || []).slice(0, 12);
    const usableCards = cards.length ? cards : sampleQuestions(type, klass).map((front, index) => ({
      front,
      back: bank[index]?.answer || bank[index]?.explanation || `Review this concept in ${sources[0] || klass.name}.`
    }));
    return { ...base, cards: usableCards, questions: usableCards.map((card) => card.front), count: usableCards.length };
  }

  if (type === "Study guide") {
    const items = bank.slice(0, 12);
    return {
      ...base,
      content: content || `Review focus: ${klass.focus || klass.name}\n\nUse the questions below to guide your review of the class material.`,
      items,
      questions: items.map((item) => item.prompt),
      count: items.length || 1
    };
  }

  const items = bank.filter((item) => item.prompt && Array.isArray(item.choices)).slice(0, Math.max(1, requestedCount));
  const questions = items.length
    ? scoreableQuestions({ id: base.id, items })
    : sampleQuestions(type, klass);
  return {
    ...base,
    ...(type === "Practice test" ? { timeLimitMinutes: 10 } : {}),
    items,
    questions,
    count: questions.length
  };
}

export function renderStudyTab({ root, klass, onMaterialAdded, onAttemptCompleted }) {
  function renderStudyView() {
    const hasUploads = klass.uploads.length > 0;
    const readyUploads = klass.uploads.filter((upload) => upload.status === "ready").length;

    root.innerHTML = `
      <section class="study-grid">
        <form class="panel generate-panel" id="generate-form">
          <div>
            <h2>Create a local review</h2>
            <p class="muted">Use saved sample material in ${escapeHtml(klass.name)}. New AI generation is not connected in this demo.</p>
          </div>
          <div class="generate-context">
            <span>${icon("book", { size: 18 })}<strong>${readyUploads}</strong> ready uploads</span>
            <span>${icon("syllabus", { size: 18 })}<strong>${escapeHtml(klass.nextExam || "No exam")}</strong></span>
          </div>
          <fieldset class="type-options">
            <legend>Type</legend>
            ${TYPES.map((type, index) => `
              <label class="type-option">
                <input type="radio" name="type" value="${type.value}" ${index === 0 ? "checked" : ""} />
                <span>${icon(type.icon, { size: 22 })}${type.value}<small class="field-hint">${type.hint}</small></span>
              </label>
            `).join("")}
          </fieldset>
          <label class="field">
            <span>Questions</span>
            <input name="count" type="number" min="1" max="50" value="10" inputmode="numeric" />
          </label>
          <p class="field-hint">Local reviews reuse saved questions, notes, and cards. They do not invent new content.</p>
          <button class="btn btn-primary" type="submit" ${hasUploads ? "" : "disabled"}>
            ${icon("sparkles", { size: 18 })} Create review set
          </button>
          ${hasUploads ? "" : `<p class="field-hint">Add material in the Library tab first.</p>`}
        </form>
        <div class="section-stack">
          <div>
            <h2>Saved materials</h2>
            <p class="muted">Open study guides and flashcards, or start a quiz to see explanations and saved scores.</p>
          </div>
          <div id="material-list"></div>
        </div>
      </section>
    `;

    const form = root.querySelector("#generate-form");
    const list = root.querySelector("#material-list");

    function renderMaterials() {
      if (!klass.materials.length) {
        list.innerHTML = `
          <div class="panel empty-state">
            <span class="empty-icon">${icon("sparkles", { size: 26 })}</span>
            <strong>No study materials yet</strong>
            <span>Create a local review set from available class material.</span>
          </div>`;
        return;
      }

      list.innerHTML = `<div class="material-grid">${[...klass.materials].reverse().map((material) => {
        const questions = Array.isArray(material.questions) ? material.questions : [];
        const count = material.items?.length || material.cards?.length || questions.length || material.count || 0;
        const lastAttempt = material.attempts?.at(-1);
        const canView = Boolean(material.content || material.items?.length || material.cards?.length || questions.length);
        const isAssessment = ["Quiz", "Practice test"].includes(material.type);
        const canPlay = isAssessment && scoreableQuestions(material).length > 0;
        const title = material.title || material.type || "Study material";
        const prompts = material.items?.map((item) => item.prompt).filter(Boolean)
          || questions.map((question) => typeof question === "string" ? question : question.prompt).filter(Boolean);
        return `
          <article class="panel material-card">
            <header>
              <span class="kind-icon">${icon(TYPE_ICONS[material.type] || "quiz")}</span>
              <div class="row-main">
                <strong>${escapeHtml(title)}</strong>
                <span class="row-meta">${escapeHtml(material.type || "Material")} &middot; ${escapeHtml(material.scope || "Class material")} &middot; ${count} ${count === 1 ? "item" : "items"}</span>
              </div>
            </header>
            <div class="material-badges">
              ${material.isSample ? `<span class="sample-badge">Sample material</span>` : `<span class="ai-label">${escapeHtml(material.status || "Local draft")}</span>`}
              ${lastAttempt ? `<span class="row-meta">Last score: <strong>${lastAttempt.scorePercent}%</strong> &middot; ${(material.attempts || []).length} ${(material.attempts || []).length === 1 ? "attempt" : "attempts"}</span>` : ""}
            </div>
            <footer>
              <span class="row-meta">${formatDate(material.createdAt)}${material.timeLimitMinutes ? ` &middot; ${material.timeLimitMinutes} min` : ""}</span>
              <button class="btn ${canPlay ? "btn-primary" : "btn-secondary"} material-open" type="button" data-open-material="${escapeHtml(material.id)}" ${canView ? "" : "disabled"}>
                ${canPlay ? "Start" : "Open material"}
              </button>
            </footer>
            ${prompts.length ? `<ul class="preview-list">${prompts.slice(0, 2).map((prompt) => `<li>${escapeHtml(prompt)}</li>`).join("")}</ul>` : ""}
            ${material.sources?.length ? `<div class="source-chips">${material.sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("")}</div>` : ""}
          </article>
        `;
      }).join("")}</div>`;
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const material = createLocalMaterial(data.get("type"), klass, Number(data.get("count")) || 10);
      onMaterialAdded(material);
      toast(`${material.type} saved as a local review set.`);
    });

    list.addEventListener("click", (event) => {
      const materialId = event.target.closest("[data-open-material]")?.dataset.openMaterial;
      const material = klass.materials.find((item) => item.id === materialId);
      if (!material) return;

      const questions = scoreableQuestions(material);
      if (!["Quiz", "Practice test"].includes(material.type) || !questions.length) {
        openMaterialViewer(material);
        return;
      }

      const playableMaterial = { ...material, questions };
      renderQuizPlayer(root, playableMaterial, {
        onBack: renderStudyView,
        onAttemptCompleted: (attempt) => {
          material.attempts = [...(material.attempts || []), attempt];
          onAttemptCompleted?.(material.id, attempt);
        }
      });
    });

    renderMaterials();
  }

  renderStudyView();
}
