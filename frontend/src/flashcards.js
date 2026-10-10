export function renderFlashcards(root, material) {
  root.innerHTML = `
    <section class="workspace-section flashcard-stage">
      <button class="flashcard" type="button">
        <span>${material?.type || "Flashcards"}</span>
        <strong>Cards will appear here after generation.</strong>
      </button>
    </section>
  `;
}
