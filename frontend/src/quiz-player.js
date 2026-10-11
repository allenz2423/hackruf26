import { mathHtml, typesetMath } from "./math.js";
import { escapeHtml, toast } from "./ui.js";
import { submitAttemptApi, flagMaterialItemApi } from "./api.js";
import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
import { quizTutorContext } from "./quiz-tutor-context.js";

function formatTime(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function getSubmittedDisplay(question, submittedRaw) {
  if (submittedRaw === null || submittedRaw === undefined || submittedRaw === "") {
    return "No answer selected";
  }
  const kind = question.kind || (Array.isArray(question.choices) && question.choices.length ? "multiple_choice" : "short_answer");
  if (kind === "true_false") {
    const lower = String(submittedRaw).trim().toLowerCase();
    if (lower === "true") return "True";
    if (lower === "false") return "False";
    return String(submittedRaw);
  }
  if (kind === "short_answer") {
    return String(submittedRaw);
  }
  // multiple_choice
  const choices = Array.isArray(question.choices) ? question.choices : [];
  for (let i = 0; i < choices.length; i++) {
    const ch = choices[i];
    if (ch && typeof ch === "object") {
      if (String(ch.id) === String(submittedRaw) || String(i) === String(submittedRaw)) {
        return ch.text || ch.label || ch.id;
      }
    } else if (String(i) === String(submittedRaw) || String(ch) === String(submittedRaw)) {
      return String(ch);
    }
  }
  return String(submittedRaw);
}

function getCorrectDisplay(question, correctAnswerVal) {
  if (correctAnswerVal !== undefined && correctAnswerVal !== null && correctAnswerVal !== "") {
    if (question.kind === "true_false") {
      const lower = String(correctAnswerVal).trim().toLowerCase();
      if (lower === "true") return "True";
      if (lower === "false") return "False";
    }
    return String(correctAnswerVal);
  }
  if (question.kind === "true_false") {
    if (question.answer !== undefined) {
      return String(question.answer).toLowerCase() === "true" ? "True" : "False";
    }
    return "N/A";
  }
  if (question.kind === "short_answer") {
    return question.answer || (Array.isArray(question.accepted_answers) ? question.accepted_answers.join(", ") : "N/A");
  }
  const choices = Array.isArray(question.choices) ? question.choices : [];
  if (Number.isInteger(question.answerIndex) && choices[question.answerIndex]) {
    const ch = choices[question.answerIndex];
    return typeof ch === "object" ? (ch.text || ch.id) : String(ch);
  }
  if (question.answer !== undefined) {
    const match = choices.find((c) => typeof c === "object" ? c.id === question.answer : c === question.answer);
    if (match) return typeof match === "object" ? (match.text || match.id) : String(match);
    return String(question.answer);
  }
  return "N/A";
}

function renderQuestionInput(question, index) {
  const kind = question.kind || (Array.isArray(question.choices) && question.choices.length ? "multiple_choice" : "short_answer");

  if (kind === "true_false") {
    return `
      <div class="practice-options">
        <label class="practice-option">
          <input type="radio" name="question-${index}" value="true" />
          <span>True</span>
        </label>
        <label class="practice-option">
          <input type="radio" name="question-${index}" value="false" />
          <span>False</span>
        </label>
      </div>
    `;
  }

  if (kind === "short_answer") {
    return `
      <div class="practice-short-answer">
        <input
          type="text"
          class="practice-text-input"
          name="question-${index}"
          placeholder="Type your answer here..."
          autocomplete="off"
        />
      </div>
    `;
  }

  // multiple_choice
  const choices = Array.isArray(question.choices) ? question.choices : [];
  return `
    <div class="practice-options">
      ${choices.map((choice, choiceIndex) => {
        const isObj = choice && typeof choice === "object";
        const val = isObj ? String(choice.id ?? choiceIndex) : String(choiceIndex);
        const label = isObj ? (choice.text ?? choice.label ?? choice.value ?? "") : String(choice);
        return `
          <label class="practice-option">
            <input type="radio" name="question-${index}" value="${escapeHtml(val)}" />
            <span>${mathHtml(label)}</span>
          </label>
        `;
      }).join("")}
    </div>
  `;
}

export function renderQuizPlayer(root, material, { onBack, onAttemptCompleted } = {}) {
  let timerId = null;
  let submitted = false;
  let expiredAnswers = null;
  let disposed = false;
  const deadline = material.timeLimitMinutes ? Date.now() + material.timeLimitMinutes * 60 * 1000 : null;

  function clearTimer() {
    if (timerId !== null) {
      window.clearInterval(timerId);
      timerId = null;
    }
  }

  function goBack() {
    disposed = true;
    clearTimer();
    window.removeEventListener("hashchange", leave);
    onBack?.();
  }

  const leave = () => { disposed = true; clearTimer(); };
  window.addEventListener("hashchange", leave, { once: true });

  function startTimer() {
    clearTimer();
    if (deadline === null || disposed) return;
    const countdown = root.querySelector("#assessment-countdown");
    const updateCountdown = () => {
      const secondsLeft = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (countdown) countdown.textContent = formatTime(secondsLeft);
      if (secondsLeft <= 0) {
        clearTimer();
        void submit(root.querySelector("#assessment-form"), true);
      }
    };
    timerId = window.setInterval(updateCountdown, 1000);
    updateCountdown();
  }

  function renderResults(attempt) {
    const totalCount = material.questions.length;
    const correctCount = typeof attempt.correctCount === "number"
      ? attempt.correctCount
      : (attempt.questionResults
          ? attempt.questionResults.filter((r) => r.correct).length
          : attempt.results.filter((r) => r.isCorrect).length);

    root.innerHTML = `
      <section class="assessment-view">
        ${sourceWarningsHtml(material)}
        <button class="back-link assessment-back" type="button" data-back-to-study>&larr; Back to study materials</button>
        <header class="assessment-header">
          <div>
            <p class="eyebrow">${material.isSample ? "Basic Algebra example" : "Practice results"}${attempt.timeExpired ? " &middot; Time expired" : ""}</p>
            <h2>${escapeHtml(material.title || material.type)}</h2>
          </div>
          <div class="assessment-score" role="status" aria-live="polite">
            <strong>${attempt.scorePercent}%</strong>
            <span>${correctCount} of ${totalCount} correct</span>
          </div>
        </header>
        <div class="question-review-list">
          ${material.questions.map((question, index) => {
            const qId = question.id || `q-${index + 1}`;
            const backendResult = attempt.questionResults?.find((r) => String(r.question_id) === String(qId))
              || attempt.questionResults?.[index];

            let isCorrect = false;
            let submittedRaw = attempt.answers?.[qId] ?? "";
            let correctAnswerVal = "";
            let explanation = question.explanation || "";
            let sources = question.sources || [];

            if (backendResult) {
              isCorrect = Boolean(backendResult.correct);
              submittedRaw = backendResult.submitted_answer ?? submittedRaw;
              correctAnswerVal = backendResult.correct_answer;
              explanation = backendResult.explanation || explanation;
              sources = backendResult.sources?.length ? backendResult.sources : sources;
            } else {
              const localRes = attempt.results?.[index];
              isCorrect = Boolean(localRes?.isCorrect);
              correctAnswerVal = question.answer;
            }

            const selected = getSubmittedDisplay(question, submittedRaw);
            const correct = getCorrectDisplay(question, correctAnswerVal);

            return `
              <article class="question-review" data-result="${isCorrect ? "correct" : "incorrect"}">
                <div class="review-heading">
                  <strong>Question ${index + 1}</strong>
                  <span class="pill" data-tone="${isCorrect ? "good" : "bad"}">
                    <strong>${isCorrect ? "Correct" : "Review"}</strong>
                  </span>
                </div>
                <p class="review-prompt">${mathHtml(question.prompt)}</p>
                <p class="review-answer">Your answer: <strong>${mathHtml(selected || "None")}</strong></p>
                <p class="review-answer">Answer key: <strong>${mathHtml(correct || "N/A")}</strong></p>
                ${explanation ? `<p class="review-explanation">${mathHtml(explanation)}</p>` : ""}
                ${sources && sources.length ? `
                  <div class="source-chips" style="margin-top: 0.5rem;">
                    ${sources.map(sourceCitationHtml).join("")}
                  </div>
                ` : ""}
                <div class="review-actions" style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                  ${!isCorrect ? `
                    <button class="btn btn-secondary btn-small" type="button" data-ask-tutor="${index}">
                      Ask tutor about this
                    </button>
                  ` : ""}
                  <button class="btn btn-ghost btn-small" type="button" data-flag-item="${escapeHtml(question.id || `q-${index + 1}`)}">
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
    void typesetMath(root);
    bindSourceCitations(root, material);

    root.querySelectorAll("[data-back-to-study]").forEach((button) => {
      button.addEventListener("click", goBack);
    });

    root.querySelector("[data-retake]").addEventListener("click", () => {
      disposed = true;
      clearTimer();
      window.removeEventListener("hashchange", leave);
      renderQuizPlayer(root, material, { onBack, onAttemptCompleted });
    });

    root.querySelectorAll("[data-ask-tutor]").forEach((button) => {
      button.addEventListener("click", () => {
        clearTimer();
        const question = material.questions[Number(button.dataset.askTutor)];
        const questionId = question.id || `q-${Number(button.dataset.askTutor) + 1}`;
        const context = quizTutorContext(question, attempt.answers?.[questionId], material.sourceUploadIds);
        sessionStorage.setItem("tutor_initial_question", JSON.stringify(context));
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
        if (material.isRemote) {
          try {
            await flagMaterialItemApi(material.id, questionId, "Student reported question for review");
            toast("Question flagged for review.");
          } catch (error) {
            toast(error.message, { tone: "error" });
            return;
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
    if (submitted || disposed) return;
    expired ||= deadline !== null && Date.now() >= deadline;
    submitted = true;
    clearTimer();

    const formData = new FormData(form);
    const answersMap = {};
    const localResults = material.questions.map((question, index) => {
      const questionKey = question.id || `q-${index + 1}`;
      const raw = expiredAnswers ? expiredAnswers[questionKey] : formData.get(`question-${index}`);
      const submittedVal = raw !== null ? String(raw).trim() : "";
      answersMap[questionKey] = submittedVal;

      const kind = question.kind || (Array.isArray(question.choices) && question.choices.length ? "multiple_choice" : "short_answer");
      let isCorrect = false;

      if (submittedVal) {
        if (kind === "true_false") {
          const expected = String(question.answer ?? "").toLowerCase();
          isCorrect = submittedVal.toLowerCase() === expected;
        } else if (kind === "short_answer") {
          const accepted = Array.isArray(question.accepted_answers) && question.accepted_answers.length
            ? question.accepted_answers
            : [question.answer ?? ""];
          const normSub = submittedVal.toLowerCase().replace(/[^\w]/g, "");
          isCorrect = accepted.some((acc) => String(acc).toLowerCase().replace(/[^\w]/g, "") === normSub);
        } else {
          // multiple_choice
          if (Number.isInteger(question.answerIndex)) {
            isCorrect = Number(raw) === question.answerIndex;
          } else if (question.answer !== undefined) {
            isCorrect = String(question.answer).toLowerCase() === submittedVal.toLowerCase();
          }
        }
      }

      return { questionId: questionKey, submittedVal, isCorrect };
    });

    let correctCount = localResults.filter((r) => r.isCorrect).length;
    let scorePercent = Math.round((correctCount / Math.max(1, material.questions.length)) * 100);
    let questionResults = null;
    if (expired) expiredAnswers = { ...answersMap };
    const inputs = [...form.querySelectorAll("input, textarea, select")];
    inputs.forEach((input) => { input.disabled = true; });

    // Remote answer keys stay on the server until the attempt is submitted.
    if (material.isRemote) {
      const submitBtn = form.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting…";
      try {
        const attemptRes = await submitAttemptApi(material.id, answersMap);
        if (disposed) return;
        if (typeof attemptRes.score_percent !== "number" || !Array.isArray(attemptRes.question_results)) {
          throw new Error("The server did not return a quiz score. Try submitting again.");
        }
        scorePercent = Math.round(attemptRes.score_percent);
        correctCount = attemptRes.correct_count;
        questionResults = attemptRes.question_results;
      } catch (err) {
        if (disposed) return;
        submitted = false;
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit for score";
        toast(`Could not score this quiz: ${err.message}`, { tone: "error" });
        if (expired || (deadline !== null && Date.now() >= deadline)) {
          expiredAnswers = { ...answersMap };
          const countdown = root.querySelector("#assessment-countdown");
          if (countdown) countdown.textContent = "00:00";
          submitBtn.textContent = "Retry scoring";
        } else {
          inputs.forEach((input) => { input.disabled = false; });
          startTimer();
        }
        return;
      }
    }

    const attempt = {
      id: crypto.randomUUID(),
      answers: answersMap,
      results: localResults,
      questionResults,
      scorePercent,
      correctCount,
      completedAt: new Date().toISOString(),
      timeExpired: expired
    };

    onAttemptCompleted?.(attempt);
    renderResults(attempt);
  }

  root.innerHTML = `
    <section class="assessment-view">
      ${sourceWarningsHtml(material)}
      <button class="back-link assessment-back" type="button" data-back-to-study>&larr; Back to study materials</button>
      <header class="assessment-header">
        <div>
          <p class="eyebrow">${material.isSample ? "Basic Algebra example" : escapeHtml(material.type)}</p>
          <h2>${escapeHtml(material.title || material.type)}</h2>
          <p class="muted">${escapeHtml(material.scope || "Practice questions")} &middot; ${material.questions.length} questions</p>
        </div>
        ${material.timeLimitMinutes
          ? `<div class="assessment-timer" role="timer" aria-live="off"><span>Time left</span><strong id="assessment-countdown">${formatTime(material.timeLimitMinutes * 60)}</strong></div>`
          : ""}
      </header>
      ${material.instructions ? `<p class="assessment-instructions">${escapeHtml(material.instructions)}</p>` : ""}
      <form id="assessment-form" class="assessment-form">
        ${material.questions.map((question, index) => `
          <fieldset class="practice-question">
            <legend>
              <span class="practice-question-heading">
                <span class="question-number">${index + 1}</span>
                <span class="practice-prompt">${mathHtml(question.prompt)}</span>
              </span>
            </legend>
            ${renderQuestionInput(question, index)}
          </fieldset>
        `).join("")}
        <div class="assessment-actions">
          <button class="btn btn-secondary" type="button" data-back-to-study>Cancel</button>
          <button class="btn btn-primary" type="submit">Submit for score</button>
        </div>
      </form>
    </section>
  `;
  void typesetMath(root);

  root.querySelectorAll("[data-back-to-study]").forEach((button) => {
    button.addEventListener("click", goBack);
  });

  const form = root.querySelector("#assessment-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit(form);
  });

  startTimer();
}
