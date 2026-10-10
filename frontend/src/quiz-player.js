import { escapeHtml } from "./ui.js";

function formatTime(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export function renderQuizPlayer(root, material, { onBack, onAttemptCompleted } = {}) {
  let timerId = null;
  let submitted = false;

  function clearTimer() {
    if (timerId !== null) {
      window.clearInterval(timerId);
      timerId = null;
    }
  }

  function goBack() {
    clearTimer();
    onBack?.();
  }

  function renderResults(attempt) {
    const correctCount = attempt.results.filter((result) => result.isCorrect).length;
    root.innerHTML = `
      <section class="assessment-view">
        <button class="back-link assessment-back" type="button" data-back-to-study>← Back to study materials</button>
        <header class="assessment-header">
          <div>
            <p class="eyebrow">${material.isSample ? "Basic Algebra example" : "Practice results"}</p>
            <h2>${escapeHtml(material.title || material.type)}</h2>
          </div>
          <div class="assessment-score" role="status" aria-live="polite">
            <strong>${attempt.scorePercent}%</strong>
            <span>${correctCount} of ${material.questions.length} correct</span>
          </div>
        </header>
        <div class="question-review-list">
          ${material.questions.map((question, index) => {
            const result = attempt.results[index];
            const selected = result.selectedIndex === null
              ? "No answer selected"
              : question.choices[result.selectedIndex];
            const correct = question.choices[question.answerIndex];
            return `
              <article class="question-review" data-result="${result.isCorrect ? "correct" : "incorrect"}">
                <div class="review-heading">
                  <strong>Question ${index + 1}</strong>
                  <span class="pill" data-tone="${result.isCorrect ? "good" : "bad"}">
                    <strong>${result.isCorrect ? "Correct" : "Review"}</strong>
                  </span>
                </div>
                <p class="review-prompt">${escapeHtml(question.prompt)}</p>
                <p class="review-answer">Your answer: <strong>${escapeHtml(selected)}</strong></p>
                <p class="review-answer">Answer key: <strong>${escapeHtml(correct)}</strong></p>
                <p class="review-explanation">${escapeHtml(question.explanation)}</p>
              </article>
            `;
          }).join("")}
        </div>
        <div class="assessment-actions">
          <button class="btn btn-secondary" type="button" data-retake>Try again</button>
          <button class="btn btn-primary" type="button" data-back-to-study>Back to study materials</button>
        </div>
      </section>
    `;

    root.querySelectorAll("[data-back-to-study]").forEach((button) => {
      button.addEventListener("click", goBack);
    });
    root.querySelector("[data-retake]").addEventListener("click", () => {
      renderQuizPlayer(root, material, { onBack, onAttemptCompleted });
    });
  }

  function submit(form, expired = false) {
    if (submitted) return;
    submitted = true;
    clearTimer();

    const formData = new FormData(form);
    const answers = {};
    const results = material.questions.map((question, index) => {
      const raw = formData.get(`question-${index}`);
      const selectedIndex = raw === null ? null : Number(raw);
      const isCorrect = selectedIndex === question.answerIndex;
      answers[question.id] = selectedIndex;
      return { questionId: question.id, selectedIndex, isCorrect };
    });
    const correctCount = results.filter((result) => result.isCorrect).length;
    const attempt = {
      id: crypto.randomUUID(),
      answers,
      results,
      scorePercent: Math.round((correctCount / material.questions.length) * 100),
      completedAt: new Date().toISOString(),
      timeExpired: expired
    };

    onAttemptCompleted?.(attempt);
    renderResults(attempt);
  }

  root.innerHTML = `
    <section class="assessment-view">
      <button class="back-link assessment-back" type="button" data-back-to-study>← Back to study materials</button>
      <header class="assessment-header">
        <div>
          <p class="eyebrow">${material.isSample ? "Basic Algebra example" : escapeHtml(material.type)}</p>
          <h2>${escapeHtml(material.title || material.type)}</h2>
          <p class="muted">${escapeHtml(material.scope || "Practice questions")} · ${material.questions.length} questions</p>
        </div>
        ${material.timeLimitMinutes
          ? `<div class="assessment-timer" role="timer" aria-live="off"><span>Time left</span><strong id="assessment-countdown">${formatTime(material.timeLimitMinutes * 60)}</strong></div>`
          : ""}
      </header>
      <form id="assessment-form" class="assessment-form">
        ${material.questions.map((question, index) => `
          <fieldset class="practice-question">
            <legend><span class="question-number">${index + 1}</span>${escapeHtml(question.prompt)}</legend>
            <div class="practice-options">
              ${question.choices.map((choice, choiceIndex) => `
                <label class="practice-option">
                  <input type="radio" name="question-${index}" value="${choiceIndex}" />
                  <span>${escapeHtml(choice)}</span>
                </label>
              `).join("")}
            </div>
          </fieldset>
        `).join("")}
        <div class="assessment-actions">
          <button class="btn btn-secondary" type="button" data-back-to-study>Cancel</button>
          <button class="btn btn-primary" type="submit">Submit for score</button>
        </div>
      </form>
    </section>
  `;

  root.querySelectorAll("[data-back-to-study]").forEach((button) => {
    button.addEventListener("click", goBack);
  });

  const form = root.querySelector("#assessment-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit(form);
  });

  if (material.timeLimitMinutes) {
    const deadline = Date.now() + material.timeLimitMinutes * 60 * 1000;
    const countdown = root.querySelector("#assessment-countdown");
    const updateCountdown = () => {
      const secondsLeft = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      countdown.textContent = formatTime(secondsLeft);
      if (secondsLeft === 0) submit(form, true);
    };
    timerId = window.setInterval(updateCountdown, 1000);
    updateCountdown();
  }
}
