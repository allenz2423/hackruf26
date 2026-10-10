import { icon } from "./icons.js";
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

function sampleQuestions(type, klass) {
  const subject = klass.focus || klass.name;
  if (type === "Flashcards") return [`Key term from ${subject}`, "Common misconception", "Source-backed definition"];
  if (type === "Study guide") return [`Core concepts in ${subject}`, "Important examples", "What to review next"];
  if (type === "Practice test") return [`Timed free response on ${subject}`, "Explain your reasoning", "Mixed review problem"];
  return [`Quick check on ${subject}`, "Multiple choice concept check", "Short answer with explanation"];
}

function createLocalMaterial(type, klass) {
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
    const usableCards = cards.length ? cards : sampleQuestions(type, klass).map((front) => ({
      front,
      back: `Review this concept in ${sources[0] || klass.name}. The full study service is not connected in this demo.`
    }));
    return { ...base, cards: usableCards, questions: usableCards.map((card) => card.front), count: usableCards.length };
  }

  if (type === "Study guide") {
    const items = bank.slice(0, 12);
    return {
      ...base,
      content: content || `Review focus: ${klass.focus || klass.name}\n\nUse the questions below to guide your review of the uploaded class materials.`,
      items,
      questions: items.map((item) => item.prompt),
      count: items.length || 1
    };
  }

  const items = bank.slice(0, 12);
  const questions = items.length ? items.map((item) => item.prompt) : sampleQuestions(type, klass);
  return { ...base, items, questions, count: items.length || questions.length };
}

export function renderStudyTab({ root, klass, onMaterialAdded }) {
  const hasUploads = klass.uploads.length > 0;

  root.innerHTML = `
    <section class="study-grid">
      <form class="panel generate-panel" id="generate-form">
        <div>
          <h2>Create a local review</h2>
          <p class="muted">Use the sample material in ${escapeHtml(klass.name)}. The study service is not connected in this demo.</p>
        </div>
        <div class="generate-context">
          <span>${icon("book", { size: 18 })}<strong>${klass.uploads.filter((upload) => upload.status === "ready").length}</strong> ready uploads</span>
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
        <p class="field-hint">The demo reuses available sample questions and cards. It does not generate new content.</p>
        <button class="btn btn-primary" type="submit" ${hasUploads ? "" : "disabled"}>
          ${icon("sparkles", { size: 18 })} Create review set
        </button>
        ${hasUploads ? "" : `<p class="field-hint">Upload materials in the Library tab first.</p>`}
      </form>
      <div class="section-stack">
        <h2>Saved materials</h2>
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
          <span>Create a local review set from the available class material.</span>
        </div>`;
      return;
    }

    list.innerHTML = `<div class="material-grid">${[...klass.materials].reverse().map((material) => {
      const itemCount = material.items?.length || material.cards?.length || material.questions?.length || material.count || 0;
      const prompts = material.items?.map((item) => item.prompt) || material.questions || [];
      return `
        <article class="panel material-card">
          <header>
            <span class="kind-icon">${icon(TYPE_ICONS[material.type] || "quiz")}</span>
            <div class="row-main">
              <strong>${escapeHtml(material.type)}</strong>
              <span class="row-meta">${escapeHtml(material.scope)} · ${itemCount} ${itemCount === 1 ? "item" : "items"}</span>
            </div>
          </header>
          <footer>
            <span class="ai-label">${material.isSample ? "Sample material" : "Local draft"} · ${formatDate(material.createdAt)}</span>
            <span class="pill" data-tone="${material.status === "ready" ? "good" : "busy"}"><strong>${escapeHtml(material.status)}</strong></span>
          </footer>
          ${material.score ? `<span class="score-line">${icon("check", { size: 16 })}${escapeHtml(material.score)}</span>` : ""}
          ${prompts.length ? `<ul class="preview-list">${prompts.slice(0, 3).map((question) => `<li>${escapeHtml(question)}</li>`).join("")}</ul>` : ""}
          ${material.sources?.length ? `<div class="source-chips">${material.sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("")}</div>` : ""}
          <button class="btn btn-secondary" type="button" data-study="${escapeHtml(material.id)}">${icon("book", { size: 16 })} Open material</button>
        </article>`;
    }).join("")}</div>`;
  }

  list.addEventListener("click", (event) => {
    const materialId = event.target.closest("[data-study]")?.dataset.study;
    const material = klass.materials.find((item) => item.id === materialId);
    if (material) openMaterialViewer(material);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const material = createLocalMaterial(new FormData(form).get("type"), klass);
    onMaterialAdded(material);
    toast(`${material.type} saved as a local demo set.`);
  });

  renderMaterials();
}
