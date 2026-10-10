export function renderQuizPlayer(root, material) {
  root.innerHTML = `
    <section class="workspace-section">
      <h2>${material?.type || "Quiz"}</h2>
      <p class="muted">Generated questions will appear here when the study service is connected.</p>
      <button type="button" onclick="history.back()">Back</button>
    </section>
  `;
}
