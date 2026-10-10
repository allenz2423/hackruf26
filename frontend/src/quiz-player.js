import { escapeHtml, toast } from "./ui.js";
import { submitAttemptApi, flagMaterialItemApi, getAccessToken } from "./api.js";

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
        <button class="back-link assessment-back" type="button" data-back-to-study>&larr; Back to study materials</button>
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
            const isCorrect = result.isCorrect;

            return `
              <article class="question-review" data-result="${isCorrect ? "correct" : "incorrect"}">
                <div class="review-heading">
                  <strong>Question ${index + 1}</strong>
                  <span class="pill" data-tone="${isCorrect ? "good" : "bad"}">
                    <strong>${isCorrect ? "Correct" : "Review"}</strong>
                  </span>
                </div>
                <p class="review-prompt">${escapeHtml(question.prompt)}</p>
                <p class="review-answer">Your answer: <strong>${escapeHtml(selected || "None")}</strong></p>
                <p class="review-answer">Answer key: <strong>${escapeHtml(correct || "N/A")}</strong></p>
                ${question.explanation ? `<p class="review-explanation">${escapeHtml(question.explanation)}</p>` : ""}
                <div class="review-actions" style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                  ${!isCorrect ? `
                    <button class="btn btn-secondary btn-small" type="button" data-ask-tutor="${escapeHtml(question.prompt)}">
                      Ask tutor about this
                    </button>
                  ` : ""}
                  <button class="btn btn-ghost btn-small" type="button" data-flag-item="${escapeHtml(question.id || `q-${index}`)}">
                    Flag question
                  </button>
                </div>
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

    root.querySelectorAll("[data-ask-tutor]").forEach((button) => {
      button.addEventListener("click", () => {
        const prompt = button.dataset.askTutor;
        sessionStorage.setItem("tutor_initial_question", prompt);
        if (material.classId) {
          window.location.hash = `#/classes/${encodeURIComponent(material.classId)}/tutor`;
        } else {
          window.location.hash = "#/tutor";
        }
      });
    });

    root.querySelectorAll("[data-flag-item]").forEach((button) => {
      button.addEventListener("click", async () => {
        const questionId = button.dataset.flagItem;
        const token = await getAccessToken();
        if (token && material.isRemote) {
          try {
            await flagMaterialItemApi(material.id, questionId, "Student reported question for review");
            toast("Question flagged for review.");
          } catch {
            toast("Flag recorded locally.");
          }
        } else {
          toast("Question flagged.");
        }
        button.disabled = true;
        button.textContent = "Flagged";
      });
    });
  }

  async function submit(form, expired = false) {
    if (submitted) return;
    submitted = true;
    clearTimer();

    const formData = new FormData(form);
    const answersMap = {};
    const localResults = material.questions.map((question, index) => {
      const raw = formData.get(`question-${index}`);
      const selectedIndex = raw === null ? null : Number(raw);
      const isCorrect = selectedIndex === question.answerIndex;
      const questionKey = question.id || `q-${index}`;
      answersMap[questionKey] = selectedIndex !== null ? String(selectedIndex) : "";
      return { questionId: questionKey, selectedIndex, isCorrect };
    });

    const token = await getAccessToken();
    let scorePercent = Math.round((localResults.filter((r) => r.isCorrect).length / material.questions.length) * 100);

    // If remote material on backend, submit attempt for verified scoring
    if (token && material.isRemote) {
      try {
        const attemptRes = await submitAttemptApi(material.id, answersMap);
        if (attemptRes && typeof attemptRes.score_percent === "number") {
          scorePercent = Math.round(attemptRes.score_percent);
        }
      } catch (err) {
        console.warn("Backend attempt submission failed, using local scoring:", err);
      }
    }

    const attempt = {
      id: crypto.randomUUID(),
      answers: answersMap,
      results: localResults,
      scorePercent,
      completedAt: new Date().toISOString(),
      timeExpired: expired
    };

    onAttemptCompleted?.(attempt);
    renderResults(attempt);
  }

  root.innerHTML = `
    <section class="assessment-view">
      <button class="back-link assessment-back" type="button" data-back-to-study>&larr; Back to study materials</button>
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
