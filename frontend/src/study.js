import { icon } from "./icons.js";
import { escapeHtml, toast } from "./ui.js";

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

export function renderStudyTab({ root, klass, onMaterialAdded }) {
  const hasUploads = klass.uploads.length > 0;

  root.innerHTML = `
    <section class="study-grid">
      <form class="panel generate-panel" id="generate-form">
        <div>
          <h2>Generate study material</h2>
          <p class="muted">Built only from ${escapeHtml(klass.name)}'s uploads.</p>
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
          <span>Cover</span>
          <select name="scope">
            <option>Whole class</option>
            <option>Selected uploads</option>
            <option>Syllabus topics</option>
            <option>Upcoming exam</option>
          </select>
        </label>
        <div class="field-row">
          <label class="field">
            <span>Questions</span>
            <input name="count" type="number" min="4" max="50" value="10" inputmode="numeric" />
          </label>
          <label class="field">
            <span>Difficulty</span>
            <select name="difficulty">
              <option>Mixed</option>
              <option>Easier</option>
              <option>Harder</option>
            </select>
          </label>
        </div>
        <button class="btn btn-primary" type="submit" ${hasUploads ? "" : "disabled"}>
          ${icon("sparkles", { size: 18 })} Generate
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
          <span>Generate a quiz, practice test, study guide, or flashcards from your uploads.</span>
        </div>`;
      return;
    }

    list.innerHTML = `<div class="material-grid">${[...klass.materials].reverse().map((material) => `
      <article class="panel material-card">
        <header>
          <span class="kind-icon">${icon(TYPE_ICONS[material.type] || "quiz")}</span>
          <div class="row-main">
            <strong>${escapeHtml(material.type)}</strong>
            <span class="row-meta">${escapeHtml(material.scope)} · ${material.count} items</span>
          </div>
        </header>
        <footer>
          <span class="ai-label">AI-generated · ${formatDate(material.createdAt)}</span>
          <span class="pill" data-tone="${material.status === "ready" ? "good" : "busy"}"><strong>${escapeHtml(material.status)}</strong></span>
        </footer>
      </article>
    `).join("")}</div>`;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    onMaterialAdded({
      id: crypto.randomUUID(),
      type: data.get("type"),
      scope: data.get("scope"),
      difficulty: data.get("difficulty"),
      count: Number(data.get("count")),
      status: "ready",
      createdAt: new Date().toISOString()
    });
    toast(`${data.get("type")} added. Questions will fill in once the study service is connected.`);
  });

  renderMaterials();
}
