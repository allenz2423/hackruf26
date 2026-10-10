export function renderStudyTab({ root, klass, onMaterialAdded }) {
  root.innerHTML = `
    <section class="workspace-section study-grid">
      <form class="generate-panel" id="generate-form">
        <label>
          Type
          <select name="type">
            <option>Quiz</option>
            <option>Practice test</option>
            <option>Study guide</option>
            <option>Flashcards</option>
          </select>
        </label>
        <label>
          Scope
          <select name="scope">
            <option>Whole class</option>
            <option>Selected uploads</option>
            <option>Upcoming exam</option>
          </select>
        </label>
        <label>
          Questions
          <input name="count" type="number" min="4" max="50" value="10" />
        </label>
        <button type="submit">Generate</button>
      </form>
      <ul class="data-list" id="material-list"></ul>
    </section>
  `;

  const form = root.querySelector("#generate-form");
  const list = root.querySelector("#material-list");

  function renderMaterials() {
    list.replaceChildren();
    for (const material of klass.materials) {
      const item = document.createElement("li");
      item.innerHTML = `
        <span class="file-name">${material.type}</span>
        <span>${material.scope}</span>
        <span class="status-pill status-ready">${material.status}</span>
      `;
      list.append(item);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    onMaterialAdded({
      id: crypto.randomUUID(),
      type: data.get("type"),
      scope: data.get("scope"),
      count: Number(data.get("count")),
      status: "ready",
      createdAt: new Date().toISOString()
    });
  });

  renderMaterials();
}
