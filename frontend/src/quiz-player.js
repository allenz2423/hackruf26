import { mathHtml, typesetMath } from "./math.js";
import { escapeHtml, toast } from "./ui.js";
import {
  submitAttemptApi, flagMaterialItemApi, createQuizRunApi, getQuizRunApi,
  submitReasoningApi, retryReasoningApi, skipQuizQuestionApi,
  submitReasoningAnswerApi, retryReasoningFeedbackApi
} from "./api.js";
import { sourceCitationHtml, bindSourceCitations, sourceWarningsHtml } from "./source-citations.js";
import { StudyTutorPanel } from "./study-tutor.js";
import { LiveSessionController } from "./live-session.js";
import { normalizePublicQuizOutcome, normalizePublicQuizRun, publicRunReview, quizOutcomeMarkup } from "./quiz-run.js";

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
  const choices = Array.isArray(question.choices) ? question.choices : [];
  const selectedChoice = choices.find((choice) => choice && typeof choice === "object"
    && String(choice.id) === String(submittedRaw));
  if (selectedChoice) return selectedChoice.text || selectedChoice.label || selectedChoice.id;
  if (kind === "true_false") {
    const lower = String(submittedRaw).trim().toLowerCase();
    if (lower === "true") return "True";
    if (lower === "false") return "False";
    return String(submittedRaw);
  }
  if (["short_answer", "free_response"].includes(kind)) {
    return String(submittedRaw);
  }
  // multiple_choice
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
  if (["short_answer", "free_response"].includes(question.kind)) {
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

export function renderQuestionInput(question, index) {
  const kind = question.kind || (Array.isArray(question.choices) && question.choices.length ? "multiple_choice" : "short_answer");
  const choices = Array.isArray(question.choices) ? question.choices : [];

  if (choices.length) {
    return `
      <div class="practice-options">
        ${choices.map((choice, choiceIndex) => {
          const isObject = choice && typeof choice === "object";
          const value = isObject ? String(choice.id) : String(choiceIndex);
          const label = isObject ? choice.text : String(choice);
          return `
          <label class="practice-option">
            <input type="radio" name="question-${index}" value="${escapeHtml(value)}" />
            <span>${mathHtml(label)}</span>
          </label>
        `;
        }).join("")}
      </div>
    `;
  }

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

  if (kind === "free_response") {
    return `
      <div class="practice-short-answer">
        <textarea
          class="practice-text-input"
          data-answer-text
          name="question-${index}"
          placeholder="Write your answer here..."
          rows="4"
        ></textarea>
      </div>
    `;
  }

  if (kind === "short_answer") {
    return `
      <div class="practice-short-answer">
        <input
          type="text"
          class="practice-text-input"
          data-answer-text
          name="question-${index}"
          placeholder="Type your answer here..."
          autocomplete="off"
        />
      </div>
    `;
  }

  return `<p class="quiz-error" role="alert">This question has no available answer choices.</p>`;
}

function questionContextHtml(question) {
  const labels = [];
  if (Number.isInteger(question.level) && question.level >= 1 && question.level <= 4) {
    labels.push(`Level ${question.level}`);
  }
  for (const concept of question.concepts || []) {
    if (typeof concept?.label === "string" && concept.label.trim()) labels.push(concept.label.trim());
  }
  return labels.length ? `<span class="row-meta">${labels.map(escapeHtml).join(" · ")}</span>` : "";
}

function newOperationId() {
  return globalThis.crypto?.randomUUID?.()
    || "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const value = Math.floor(Math.random() * 16);
      return (char === "x" ? value : (value & 3) | 8).toString(16);
    });
}

function runOperationKey(materialId, mode) {
  return `jot-quiz-run:${materialId}:${mode}`;
}

function getStoredRunOperation(materialId, mode, forceNew = false) {
  const key = runOperationKey(materialId, mode);
  if (!forceNew) {
    try {
      const value = window.sessionStorage?.getItem(key);
      if (value) return value;
    } catch { /* session storage may be disabled */ }
  }
  const operationId = newOperationId();
  try { window.sessionStorage?.setItem(key, operationId); } catch { /* in-memory run remains usable */ }
  return operationId;
}

function getStoredSubmissionOperation(runId, answers) {
  const answerDigest = JSON.stringify(Object.entries(answers).sort(([left], [right]) => left.localeCompare(right)));
  const key = `jot-quiz-submit:${runId}`;
  try {
    const saved = JSON.parse(window.sessionStorage?.getItem(key) || "null");
    if (saved?.answerDigest === answerDigest && saved.operationId) return saved.operationId;
    const operationId = newOperationId();
    window.sessionStorage?.setItem(key, JSON.stringify({ answerDigest, operationId }));
    return operationId;
  } catch {
    return newOperationId();
  }
}

function getStoredReasoningOperation(runId, questionId, text, source) {
  const key = `jot-quiz-reasoning:${runId}:${questionId}`;
  try {
    const saved = JSON.parse(window.sessionStorage?.getItem(key) || "null");
    if (saved?.text === text && saved?.source === source && saved.operationId) return saved.operationId;
    const operationId = newOperationId();
    window.sessionStorage?.setItem(key, JSON.stringify({ text, source, operationId }));
    return operationId;
  } catch {
    return newOperationId();
  }
}

function clearStoredReasoningOperation(runId, questionId) {
  try { window.sessionStorage?.removeItem(`jot-quiz-reasoning:${runId}:${questionId}`); } catch { /* storage may be disabled */ }
}

function voiceQueueKey(runId, questionId) {
  return `jot-quiz-voice:${runId}:${questionId}`;
}

function reasoningDraftKey(runId, questionId) {
  return `jot-quiz-draft:${runId}:${questionId}`;
}

function readReasoningDraft(runId, questionId) {
  try { return JSON.parse(window.sessionStorage?.getItem(reasoningDraftKey(runId, questionId)) || "null") || {}; }
  catch { return {}; }
}

function writeReasoningDraft(runId, questionId, draft) {
  try { window.sessionStorage?.setItem(reasoningDraftKey(runId, questionId), JSON.stringify(draft)); }
  catch { /* storage may be disabled */ }
}

function renderRemoteQuizStart(root, material, options, { preferredMode = "standard", forceNewRun = false } = {}) {
  root.innerHTML = `
    <section class="assessment-view quiz-mode-start">
      <button class="back-link assessment-back" type="button" data-start-back>&larr; Back to study materials</button>
      <header class="assessment-header">
        <div><p class="eyebrow">Start quiz</p><h2>${escapeHtml(material.title || material.type)}</h2></div>
      </header>
      <p class="muted">Choose how you want to take this quiz. Standard mode works as usual. Reasoning Mode asks you to explain your thinking before final submission.${material.timeLimitMinutes ? " Reasoning Mode is untimed; choose Standard mode to keep the existing timer." : ""}</p>
      <fieldset class="quiz-mode-options">
        <legend>Quiz mode</legend>
        <label class="practice-option"><input type="radio" name="quiz-mode" value="standard" ${preferredMode === "standard" ? "checked" : ""}><span><strong>Standard quiz</strong><br>Answer and submit questions as usual.</span></label>
        <label class="practice-option"><input type="radio" name="quiz-mode" value="reasoning" ${preferredMode === "reasoning" ? "checked" : ""}><span><strong>Reasoning Mode</strong><br>Meaningful reasoning unlocks submission; correctness is graded separately.</span></label>
      </fieldset>
      <p class="quiz-start-error" role="alert" hidden></p>
      <div class="assessment-actions"><button class="btn btn-secondary" type="button" data-start-back>Cancel</button><button class="btn btn-primary" type="button" data-start-quiz>Start quiz</button></div>
    </section>`;
  root.querySelectorAll("[data-start-back]").forEach((button) => button.addEventListener("click", () => options.onBack?.()));
  const startButton = root.querySelector("[data-start-quiz]");
  const errorNode = root.querySelector(".quiz-start-error");
  startButton.addEventListener("click", async () => {
    const mode = root.querySelector('input[name="quiz-mode"]:checked')?.value === "reasoning" ? "reasoning" : "standard";
    startButton.disabled = true;
    startButton.textContent = "Starting…";
    errorNode.hidden = true;
    try {
      let operationId = getStoredRunOperation(material.id, mode, forceNewRun);
      let run = await createQuizRunApi(material.id, operationId, mode);
      run = normalizePublicQuizRun(run, { materialId: material.id, mode });
      if (mode === "reasoning") {
        renderReasoningQuizPlayer(root, { ...material, questions: run.questions }, options, run);
      } else {
        renderQuizPlayer(root, { ...material, questions: run.questions }, {
          ...options, runContext: { mode, runId: run.run_id, run }
        });
      }
    } catch (error) {
      errorNode.textContent = error.message || "The quiz could not start. Try again.";
      errorNode.hidden = false;
      startButton.disabled = false;
      startButton.textContent = "Start quiz";
    }
  });
}

function renderReasoningQuizPlayer(root, material, options, initialRun) {
  let run = normalizePublicQuizRun(initialRun, { materialId: material.id, mode: "reasoning" });
  material = { ...material, questions: run.questions };
  let disposed = false;
  let reportedRunId = null;
  let pollTimer = null;
  const drafts = new Map();
  const answerOperations = new Map();
  const reasoningOperations = new Map();
  const retryOperations = new Map();
  const skipOperations = new Map();
  const feedbackOperations = new Map();
  const tutorPanels = new Map();
  const evaluationsInFlight = new Set();
  const voiceQueues = new Map();
  const voiceFlushes = new Map();
  let activeVoice = null;
  let voiceGeneration = 0;
  let voiceStatus = { questionId: null, message: "Voice is optional. Finalized speech turns are checked automatically.", retry: false };
  const seenTranscriptIds = new Set();
  const leave = () => {
    disposed = true;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    disposeActiveVoice();
    for (const panel of tutorPanels.values()) panel.dispose();
  };
  window.addEventListener("hashchange", leave, { once: true });

  const questionResults = () => new Map((run.attempts || []).map((item) => [String(item.question_id), item]));
  const captureDrafts = () => {
    root.querySelectorAll("[data-reasoning-question]").forEach((card) => {
      const questionId = card.dataset.questionId;
      const prior = drafts.get(questionId) || {};
      const reasoning = card.querySelector("[data-reasoning-input]");
      const checked = [...card.querySelectorAll("input[type=radio]")].find((input) => input.checked);
      const answerText = card.querySelector("[data-answer-text]");
      drafts.set(questionId, {
        ...prior,
        reasoning: reasoning ? reasoning.value : prior.reasoning || "",
        answer: checked ? checked.value : answerText ? answerText.value : prior.answer || ""
      });
      writeReasoningDraft(run.run_id, questionId, drafts.get(questionId));
    });
  };

  const clearReasoningDraftIfUnchanged = (questionId, submittedText) => {
    const current = drafts.get(questionId) || {};
    if (String(current.reasoning || "").trim() !== submittedText) return false;
    const next = { ...current, reasoning: "" };
    drafts.set(questionId, next);
    writeReasoningDraft(run.run_id, questionId, next);
    return true;
  };

  async function refresh() {
    run = normalizePublicQuizRun(await getQuizRunApi(material.id, run.run_id), {
      materialId: material.id, mode: "reasoning"
    });
    material = { ...material, questions: run.questions };
    if (!disposed) {
      render();
      for (const questionId of voiceQueues.keys()) void flushVoiceQueue(questionId);
    }
  }

  function schedulePoll() {
    const needsPoll = (run.attempts || []).some((item) =>
      item.state === "evaluating_reasoning" || item.feedback_status === "pending");
    if (!needsPoll) {
      if (pollTimer !== null) window.clearTimeout(pollTimer);
      pollTimer = null;
      return;
    }
    if (pollTimer !== null || disposed) return;
    pollTimer = window.setTimeout(async () => {
      pollTimer = null;
      try { await refresh(); }
      catch { schedulePoll(); }
    }, 1500);
  }

  function setVoiceStatus(questionId, message, retry = false) {
    voiceStatus = { questionId, message, retry };
    const status = root.querySelector("[data-voice-status]");
    if (status) status.textContent = message;
    const retryButton = root.querySelector("[data-retry-voice]");
    if (retryButton) retryButton.hidden = !retry;
  }

  function disposeActiveVoice() {
    const binding = activeVoice;
    activeVoice = null;
    voiceGeneration += 1;
    if (!binding) return;
    binding.live?.close();
    void binding.audio?.close().catch(() => {});
  }

  function getVoiceQueue(questionId) {
    if (!voiceQueues.has(questionId)) {
      let saved = [];
      try { saved = JSON.parse(window.sessionStorage?.getItem(voiceQueueKey(run.run_id, questionId)) || "[]"); }
      catch { saved = []; }
      voiceQueues.set(questionId, Array.isArray(saved) ? saved : []);
    }
    return voiceQueues.get(questionId);
  }

  function storeVoiceQueue(questionId) {
    try {
      window.sessionStorage?.setItem(voiceQueueKey(run.run_id, questionId), JSON.stringify(getVoiceQueue(questionId)));
    } catch { /* session storage may be disabled */ }
  }

  async function flushVoiceQueue(questionId) {
    const existing = voiceFlushes.get(questionId);
    if (existing) return existing;
    const pending = getVoiceQueue(questionId);
    if (!pending.length || evaluationsInFlight.has(questionId) || disposed) return;
    const attempt = questionResults().get(String(questionId));
    if (["evaluating_reasoning", "submitted", "completed", "skipped"].includes(attempt?.state)) return;

    const task = (async () => {
      while (pending.length && !disposed) {
        const current = questionResults().get(String(questionId));
        if (evaluationsInFlight.has(questionId) || ["evaluating_reasoning", "submitted", "completed", "skipped"].includes(current?.state)) return;
        const turn = pending[0];
        evaluationsInFlight.add(questionId);
        try {
          const result = await submitReasoningApi(material.id, run.run_id, questionId, {
            operationId: turn.id, text: turn.text, source: "voice", expectedVersion: current?.version
          });
          pending.shift();
          storeVoiceQueue(questionId);
          clearStoredReasoningOperation(run.run_id, questionId);
          const index = (run.attempts || []).findIndex((entry) => String(entry.question_id) === String(questionId));
          if (index >= 0) run.attempts[index] = { ...run.attempts[index], ...result };
          setVoiceStatus(questionId, result.state === "reasoning_accepted"
            ? "Voice explanation saved. Your reasoning is accepted; you can submit your answer or add more."
            : result.evaluation?.follow_up || "Voice explanation saved. Add a little more about your approach.");
          evaluationsInFlight.delete(questionId);
          await refresh();
        } catch (error) {
          evaluationsInFlight.delete(questionId);
          if (error.status === 503) {
            // The server persists the finalized turn before evaluating it.
            pending.shift();
            storeVoiceQueue(questionId);
            clearStoredReasoningOperation(run.run_id, questionId);
            setVoiceStatus(questionId, "Your voice explanation is saved, but its check failed. Retry the reasoning check below.");
            try { await refresh(); } catch { /* the saved turn remains available on refresh */ }
          } else if (error.code === "evaluation_in_progress") {
            setVoiceStatus(questionId, "Your voice turn is waiting for the current reasoning check to finish.");
            try { await refresh(); } catch { /* polling will retry */ }
          } else if (error.code === "attempt_terminal") {
            pending.shift();
            storeVoiceQueue(questionId);
            setVoiceStatus(questionId, "This question is finished, so the late voice turn was not added.");
            try { await refresh(); } catch { /* preserve the current attempt state */ }
          } else {
            setVoiceStatus(questionId, "The finalized voice turn could not be saved. Retry it or type your explanation.", true);
            try { await refresh(); } catch { /* keep the submitted transcript for retry */ }
          }
          return;
        }
      }
      if (!pending.length) {
        try { window.sessionStorage?.removeItem(voiceQueueKey(run.run_id, questionId)); } catch { /* storage may be disabled */ }
      }
    })();
    voiceFlushes.set(questionId, task);
    try { await task; }
    finally { if (voiceFlushes.get(questionId) === task) voiceFlushes.delete(questionId); }
  }

  async function startVoice(questionId, question) {
    disposeActiveVoice();
    const binding = { questionId: String(questionId), generation: voiceGeneration, live: null, audio: null, transcriptIds: new Set() };
    activeVoice = binding;
    setVoiceStatus(questionId, "Connecting to voice reasoning…");
    render();
    try {
      const { AudioController } = await import("./audio.js");
      if (activeVoice !== binding || disposed) return;
      binding.audio = new AudioController({
        onAudioChunk: (chunk) => {
          if (activeVoice === binding && !binding.live?.sendAudio(chunk)) {
            setVoiceStatus(questionId, "Voice disconnected. Your text explanation is still available.");
          }
        },
        onAudioInterrupted: () => binding.audio?.stopPlayback(),
        onStatusChange: (status) => {
          if (activeVoice !== binding) return;
          if (status === "on") setVoiceStatus(questionId, "Listening. Speak naturally; finalized turns are checked automatically.");
        },
        onError: (error) => {
          if (activeVoice === binding) setVoiceStatus(questionId, `Voice input stopped: ${error.message} You can keep typing.`);
        }
      });
      await binding.audio.preparePlayback();
      if (activeVoice !== binding || disposed) return;
      binding.live = new LiveSessionController({
        onMessage: (message) => {
          if (activeVoice === binding && message.role === "tutor") {
            setVoiceStatus(questionId, `Jot: ${String(message.text).slice(0, 600)}`);
          }
        },
        onAudioData: (data, mimeType) => binding.audio?.playPcm(data, mimeType),
        onAudioInterrupted: () => binding.audio?.stopPlayback(),
        onStatusChange: (status, message) => {
          if (activeVoice !== binding) return;
          if (status === "error") {
            setVoiceStatus(questionId, `Voice disconnected${message ? `: ${message}` : "."} You can keep typing.`);
            binding.audio?.disable();
          }
        },
        onStudentTranscript: ({ transcriptId, text }) => {
          if (activeVoice !== binding || binding.generation !== voiceGeneration
            || binding.questionId !== String(questionId) || seenTranscriptIds.has(transcriptId)) return;
          seenTranscriptIds.add(transcriptId);
          binding.transcriptIds.add(transcriptId);
          const queue = getVoiceQueue(questionId);
          if (queue.some((turn) => turn.id === transcriptId)) return;
          queue.push({ id: transcriptId, text });
          storeVoiceQueue(questionId);
          setVoiceStatus(questionId, "Finalized voice turn received. Checking your explanation…");
          void flushVoiceQueue(questionId);
        }
      });
      await binding.live.connect({ mode: "quiz_reasoning", useSavedMemory: false });
      if (activeVoice !== binding || disposed) return;
      if (!binding.live.sendText(`Quiz question: ${String(question.prompt || "").slice(0, 5000)}. Invite the student to explain their own thinking without giving an answer or judging it.`)) {
        throw new Error("The voice session is not ready. Try again or type your thinking.");
      }
      await binding.audio.enable();
      if (activeVoice === binding && !disposed) setVoiceStatus(questionId, "Listening. Speak naturally; finalized turns are checked automatically.");
    } catch (error) {
      if (activeVoice !== binding) return;
      disposeActiveVoice();
      void binding.audio?.close().catch(() => {});
      setVoiceStatus(questionId, `Voice input is unavailable: ${error.message} You can keep typing.`);
      render();
    }
  }

  function resultPayload() {
    const attempts = run.attempts || [];
    const answered = attempts.filter((item) => item.state === "completed");
    const feedbackAvailable = run.feedback_policy.feedback_available;
    const graded = answered.filter((item) => typeof item.correct === "boolean");
    const correctCount = feedbackAvailable && typeof run.correct_count === "number"
      ? run.correct_count
      : (feedbackAvailable && graded.length ? graded.filter((item) => item.correct).length : undefined);
    const questionAnswerMap = Object.fromEntries(attempts
      .filter((item) => item.state === "completed")
      .map((item) => [item.question_id, item.final_answer]));
    const reviews = new Map(publicRunReview(run).map((item) => [String(item.question_id), item]));
    return {
      id: run.run_id,
      backendAttemptId: run.run_id,
      mode: "reasoning",
      answers: questionAnswerMap,
      ...(feedbackAvailable && typeof run.score_percent === "number" ? { scorePercent: run.score_percent } : {}),
      feedbackAvailable,
      correctCount,
      ...(run.outcome ? { outcome: run.outcome } : {}),
      questionResults: attempts.map((item) => ({
        question_id: item.question_id,
        ...(reviews.get(String(item.question_id)) || {}),
        skipped: item.state === "skipped",
        submitted_answer: item.final_answer
      })),
      completedAt: run.completed_at || new Date().toISOString()
    };
  }

  function showResults() {
    const result = resultPayload();
    const totalCount = material.questions.length;
    const attemptByQuestion = questionResults();
    const feedbackAvailable = run.feedback_policy.feedback_available;
    const xpAwardedMarkup = quizOutcomeMarkup(run.outcome);
    const reviewByQuestion = new Map(publicRunReview(run).map((item) => [String(item.question_id), item]));
    root.innerHTML = `
      <section class="assessment-view">
        ${sourceWarningsHtml(material)}
        <button class="back-link assessment-back" type="button" data-back-to-study>&larr; Back to study materials</button>
        <header class="assessment-header">
          <div><p class="eyebrow">Reasoning Mode results</p><h2>${escapeHtml(material.title || material.type)}</h2></div>
          <div class="assessment-score" role="status" aria-live="polite">
            ${feedbackAvailable && typeof run.score_percent === "number"
    ? `<strong>${Math.round(run.score_percent)}%</strong><span>${result.correctCount ?? "—"} of ${totalCount} correct</span>${xpAwardedMarkup}`
    : `<strong>Submitted</strong><span>${feedbackAvailable ? "Feedback is available." : "Feedback is not available yet."}</span>${xpAwardedMarkup}`}
          </div>
        </header>
        <div class="question-review-list">
          ${material.questions.map((question, index) => {
            const item = attemptByQuestion.get(String(question.id));
            const skipped = item?.state === "skipped";
            const review = reviewByQuestion.get(String(question.id)) || {};
            const outcomeCorrectness = review.outcome?.correctness ?? item?.outcome?.correctness;
            const isGraded = feedbackAvailable && (typeof (review.correct ?? item?.correct) === "boolean"
              || ["correct", "incorrect"].includes(outcomeCorrectness));
            const correct = isGraded && (typeof (review.correct ?? item?.correct) === "boolean"
              ? Boolean(review.correct ?? item?.correct) : outcomeCorrectness === "correct");
            const feedback = item?.feedback_status;
            const selected = skipped ? "Skipped" : getSubmittedDisplay(question, item?.final_answer);
            const resultTone = skipped ? "neutral" : !isGraded ? "neutral" : correct ? "good" : "bad";
            const resultLabel = skipped ? "Skipped" : !isGraded ? "Submitted" : correct ? "Correct" : "Review";
            return `<article class="question-review" data-result="${skipped ? "skipped" : isGraded ? (correct ? "correct" : "incorrect") : "submitted"}">
              <div class="review-heading"><strong>Question ${index + 1}</strong><span class="pill" data-tone="${resultTone}"><strong>${resultLabel}</strong></span></div>
              <p class="review-prompt">${mathHtml(question.prompt)}</p>
              ${quizOutcomeMarkup(review.outcome, { question: true }) ? `<p>${quizOutcomeMarkup(review.outcome, { question: true })}</p>` : ""}
              ${!skipped ? `<p class="review-answer">Your answer: <strong>${mathHtml(selected || "No answer")}</strong></p>
                ${feedbackAvailable && (review.correct_answer ?? item?.correct_answer) ? `<p class="review-answer">Answer key: <strong>${mathHtml(getCorrectDisplay(question, review.correct_answer ?? item?.correct_answer))}</strong></p>` : ""}
                ${feedbackAvailable && (review.answer_feedback ?? item?.answer_feedback) ? `<p class="review-explanation"><strong>Answer feedback:</strong> ${mathHtml(review.answer_feedback ?? item?.answer_feedback)}</p>` : ""}
                ${feedbackAvailable && feedback === "pending" ? `<p class="muted" role="status">Reasoning feedback is being prepared…</p>` : ""}
                ${feedbackAvailable && feedback === "failed" ? `<p class="quiz-error" role="status">Your answer and grade are saved. Reasoning feedback could not be generated.</p><button class="btn btn-secondary btn-small" type="button" data-retry-feedback="${escapeHtml(question.id)}">Retry reasoning feedback</button>` : ""}
                ${feedbackAvailable && (review.reasoning_feedback ?? item?.reasoning_feedback) ? `<p class="review-explanation"><strong>Reasoning feedback:</strong> ${mathHtml(review.reasoning_feedback ?? item?.reasoning_feedback)}</p>` : ""}` : `<p class="muted">This question was skipped and is not counted as a submitted answer.</p>`}
              <div class="reasoning-history"><strong>Submitted reasoning</strong><ol>${(item?.reasoning_turns || []).map((turn) => `<li>${mathHtml(turn.text)}</li>`).join("")}</ol></div>
              ${feedbackAvailable && (review.sources || item?.sources)?.length ? `<div class="source-chips">${(review.sources || item.sources).map(sourceCitationHtml).join("")}</div>` : ""}
              <div class="review-actions">
                ${feedbackAvailable ? `<button class="btn btn-secondary btn-small" type="button" data-ask-tutor="${index}">Ask tutor about this</button>` : ""}
                <button class="btn btn-ghost btn-small" type="button" data-flag-item="${escapeHtml(question.id)}">Flag question</button>
              </div>
            </article>`;
          }).join("")}
        </div>
        <div class="assessment-actions"><button class="btn btn-secondary" type="button" data-retake>Try again</button><button class="btn btn-primary" type="button" data-back-to-study>Back to study materials</button></div>
      </section>`;
    void typesetMath(root);
    bindSourceCitations(root, material);
    root.querySelectorAll("[data-back-to-study]").forEach((button) => button.addEventListener("click", () => {
      leave();
      window.removeEventListener("hashchange", leave);
      options.onBack?.();
    }));
    root.querySelector("[data-retake]").addEventListener("click", () => {
      leave();
      window.removeEventListener("hashchange", leave);
      renderRemoteQuizStart(root, material, options, { preferredMode: "reasoning", forceNewRun: true });
    });
    if (reportedRunId !== run.run_id) {
      reportedRunId = run.run_id;
      options.onAttemptCompleted?.(material.id, result);
    }
    root.querySelectorAll("[data-retry-feedback]").forEach((button) => button.addEventListener("click", async () => {
      const questionId = button.dataset.retryFeedback;
      button.disabled = true;
      try {
        let operationId = feedbackOperations.get(questionId);
        if (!operationId) { operationId = newOperationId(); feedbackOperations.set(questionId, operationId); }
        const attempt = questionResults().get(String(questionId));
        await retryReasoningFeedbackApi(material.id, run.run_id, questionId, operationId, attempt?.version);
        await refresh();
      } catch (error) {
        feedbackOperations.delete(questionId);
        toast(error.message, { tone: "error" });
        button.disabled = false;
      }
    }));
    root.querySelectorAll("[data-ask-tutor]").forEach((button) => button.addEventListener("click", () => {
      const index = Number(button.dataset.askTutor);
      if (!tutorPanels.has(index)) {
        tutorPanels.set(index, new StudyTutorPanel(button.closest(".question-review"), button, {
          material_id: material.id,
          question_id: material.questions[index].id,
          attempt_id: run.run_id
        }));
      }
      tutorPanels.get(index).toggle();
    }));
    root.querySelectorAll("[data-flag-item]").forEach((button) => button.addEventListener("click", async () => {
      try {
        await flagMaterialItemApi(material.id, button.dataset.flagItem, "Student reported question for review");
        button.disabled = true;
        button.textContent = "Flagged";
      } catch (error) { toast(error.message, { tone: "error" }); }
    }));
    schedulePoll();
  }

  function render() {
    if (disposed) return;
    if (run.status === "completed") {
      disposeActiveVoice();
      showResults();
      return;
    }
    const byQuestion = questionResults();
    if (activeVoice && ["completed", "skipped"].includes(byQuestion.get(activeVoice.questionId)?.state)) {
      disposeActiveVoice();
    }
    root.innerHTML = `
      <section class="assessment-view">
        ${sourceWarningsHtml(material)}
        <button class="back-link assessment-back" type="button" data-reasoning-back>&larr; Back to study materials</button>
        <header class="assessment-header"><div><p class="eyebrow">Reasoning Mode</p><h2>${escapeHtml(material.title || material.type)}</h2><p class="muted">Select or draft answers at any time. Submission unlocks after the backend accepts a meaningful reasoning attempt.</p></div><div class="assessment-score" role="status"><strong>${byQuestion.size}/${material.questions.length}</strong><span>questions in this run</span></div></header>
        <div class="reasoning-question-list">
          ${material.questions.map((question, index) => {
            const qid = String(question.id);
            const item = byQuestion.get(qid);
            const state = item?.state || "awaiting_reasoning";
            const terminal = ["completed", "skipped"].includes(state);
            const accepted = state === "reasoning_accepted";
            if (terminal) {
              const queued = getVoiceQueue(qid);
              if (queued.length) {
                queued.length = 0;
                storeVoiceQueue(qid);
              }
            }
            const draft = drafts.get(qid) || {};
            const transcriptHistory = item?.reasoning_turns || [];
            let status = "Explain your thinking to unlock final submission.";
            if (state === "evaluating_reasoning") status = "Checking your explanation…";
            else if (state === "reasoning_insufficient") status = item.evaluation?.follow_up || "Add a little more about your approach.";
            else if (state === "reasoning_failed") status = "Your explanation is saved, but the check failed. Retry the check or skip this question.";
            else if (accepted) status = "Ready to submit. You can still revise your answer or add more reasoning.";
            const answerHtml = renderQuestionInput(question, index);
            return `<article class="reasoning-question" data-reasoning-question data-question-id="${escapeHtml(qid)}" id="reasoning-question-${index}">
              <fieldset class="practice-question"><legend><span class="practice-question-heading"><span class="question-number">${index + 1}</span><span class="practice-prompt">${mathHtml(question.prompt)}</span></span>${questionContextHtml(question)}</legend>
                <div class="reasoning-answer-area" ${terminal ? "aria-disabled=\"true\"" : ""}>${answerHtml}</div>
                <div class="reasoning-section"><h3>Explain your thinking</h3>
                  ${transcriptHistory.length ? `<ol class="reasoning-history">${transcriptHistory.map((turn) => `<li><span>${turn.source === "voice" ? "Voice" : "You"}</span>${mathHtml(turn.text)}</li>`).join("")}</ol>` : ""}
                  ${terminal ? `<p class="reasoning-status" role="status">${state === "skipped" ? "Skipped. You can continue with the other questions." : "Answer submitted and graded."}</p>` : `
                    <label class="visually-hidden" for="reasoning-input-${index}">Explain your thinking for question ${index + 1}</label>
                    <textarea id="reasoning-input-${index}" data-reasoning-input rows="3" maxlength="12000" placeholder="Describe how you approached this question…">${escapeHtml(draft.reasoning || "")}</textarea>
                    <p class="reasoning-status" data-reasoning-status role="status" aria-live="polite">${escapeHtml(status)}</p>
                    <div class="reasoning-actions">
                      ${state === "reasoning_failed" ? `<button class="btn btn-secondary btn-small" type="button" data-retry-reasoning>Retry reasoning check</button>` : `<button class="btn btn-secondary btn-small" type="button" data-submit-reasoning ${state === "evaluating_reasoning" ? "disabled" : ""}>${accepted ? "Add to my reasoning" : transcriptHistory.length ? "Check updated reasoning" : "Check my reasoning"}</button>`}
                      <button class="btn btn-ghost btn-small" type="button" data-voice-reasoning aria-pressed="${activeVoice?.questionId === qid ? "true" : "false"}">${activeVoice?.questionId === qid ? "Stop voice" : "Explain aloud"}</button>
                      <button class="btn btn-ghost btn-small" type="button" data-skip-question>Skip question</button>
                    </div>
                    <form class="reasoning-final-answer" data-final-answer-form>
                      <button class="btn btn-primary btn-small" type="submit" ${accepted ? "" : "disabled"}>Submit answer</button>
                      ${!accepted ? `<span class="muted">Submission is locked until your reasoning is accepted.</span>` : ""}
                    </form>
                  `}
                </div>
              </fieldset>
            </article>`;
          }).join("")}
        </div>
        <div class="reasoning-voice-status" role="status" aria-live="polite">
          <span data-voice-status>${escapeHtml(voiceStatus.message)}</span>
          <button class="btn btn-ghost btn-small" type="button" data-retry-voice ${voiceStatus.retry ? "" : "hidden"}>Retry voice transcript</button>
        </div>
        <div class="assessment-actions"><button class="btn btn-secondary" type="button" data-reasoning-back>Back to study materials</button></div>
      </section>`;
    void typesetMath(root);
    root.querySelectorAll("[data-reasoning-back]").forEach((button) => button.addEventListener("click", () => {
      leave();
      window.removeEventListener("hashchange", leave);
      options.onBack?.();
    }));
    root.querySelector("[data-retry-voice]")?.addEventListener("click", () => {
      if (voiceStatus.questionId) void flushVoiceQueue(voiceStatus.questionId);
    });
    root.querySelectorAll("[data-reasoning-question]").forEach((card, index) => {
      const qid = card.dataset.questionId;
      const question = material.questions[index];
      const item = byQuestion.get(qid);
      const state = item?.state || "awaiting_reasoning";
      const draft = drafts.get(qid) || readReasoningDraft(run.run_id, qid);
      drafts.set(qid, draft);
      const textAnswer = card.querySelector("[data-answer-text]");
      if (textAnswer && draft.answer !== undefined) textAnswer.value = draft.answer;
      const answerInputs = [...card.querySelectorAll(".reasoning-answer-area input[type=radio]")];
      if (answerInputs.length && draft.answer !== undefined) answerInputs.forEach((input) => { input.checked = input.value === draft.answer; });
      if (["completed", "skipped"].includes(state)) card.querySelectorAll(".reasoning-answer-area input").forEach((input) => { input.disabled = true; });
      card.querySelectorAll(".reasoning-answer-area input").forEach((input) => input.addEventListener("change", () => {
        const selected = [...card.querySelectorAll("input[type=radio]")].find((control) => control.checked);
        const next = { ...(drafts.get(qid) || {}), answer: selected?.value || "" };
        drafts.set(qid, next);
        writeReasoningDraft(run.run_id, qid, next);
      }));
      textAnswer?.addEventListener("input", () => {
        const next = { ...(drafts.get(qid) || {}), answer: textAnswer.value };
        drafts.set(qid, next);
        writeReasoningDraft(run.run_id, qid, next);
      });
      card.querySelector("[data-reasoning-input]")?.addEventListener("input", (event) => {
        const next = { ...(drafts.get(qid) || {}), reasoning: event.currentTarget.value };
        drafts.set(qid, next);
        writeReasoningDraft(run.run_id, qid, next);
      });
      card.querySelector("[data-voice-reasoning]")?.addEventListener("click", () => {
        if (activeVoice?.questionId === qid) {
          disposeActiveVoice();
          setVoiceStatus(qid, "Voice input stopped. Your current text and selected answer are saved.");
          render();
        } else {
          captureDrafts();
          void startVoice(qid, question);
        }
      });
      card.querySelector("[data-submit-reasoning]")?.addEventListener("click", async () => {
        captureDrafts();
        const text = String(drafts.get(qid)?.reasoning || "").trim();
        if (!text) { toast("Add an explanation before checking your reasoning.", { tone: "error" }); return; }
        const existing = reasoningOperations.get(qid);
        const operation = existing?.text === text ? existing : {
          id: getStoredReasoningOperation(run.run_id, qid, text, "text"), text, source: "text"
        };
        reasoningOperations.set(qid, operation);
        evaluationsInFlight.add(qid);
        try {
          const attempt = questionResults().get(qid);
          const result = await submitReasoningApi(material.id, run.run_id, qid, {
            operationId: operation.id, text, source: "text", expectedVersion: attempt?.version
          });
          evaluationsInFlight.delete(qid);
          reasoningOperations.delete(qid);
          clearStoredReasoningOperation(run.run_id, qid);
          clearReasoningDraftIfUnchanged(qid, text);
          const pos = (run.attempts || []).findIndex((entry) => String(entry.question_id) === qid);
          if (pos >= 0) run.attempts[pos] = { ...run.attempts[pos], ...result };
          await refresh();
          if (result.state !== "evaluating_reasoning") void flushVoiceQueue(qid);
        } catch (error) {
          evaluationsInFlight.delete(qid);
          if (error.status === 503) reasoningOperations.delete(qid);
          try {
            await refresh();
            const attempt = questionResults().get(qid);
            const persisted = attempt?.reasoning_turns?.some((turn) => turn.operation_id === operation.id);
            if (persisted && clearReasoningDraftIfUnchanged(qid, text)) render();
          } catch { /* leave the explanation visible for retry */ }
          toast(error.message || "The reasoning check could not be completed.", { tone: "error" });
        }
      });
      card.querySelector("[data-retry-reasoning]")?.addEventListener("click", async (event) => {
        event.currentTarget.disabled = true;
        const operationId = retryOperations.get(qid) || newOperationId();
        retryOperations.set(qid, operationId);
        evaluationsInFlight.add(qid);
        try {
          const attempt = questionResults().get(qid);
          await retryReasoningApi(material.id, run.run_id, qid, operationId, attempt?.version);
          evaluationsInFlight.delete(qid);
          retryOperations.delete(qid);
          await refresh();
        } catch (error) {
          evaluationsInFlight.delete(qid);
          retryOperations.delete(qid);
          try { await refresh(); } catch { event.currentTarget.disabled = false; }
          toast(error.message, { tone: "error" });
        }
      });
      card.querySelector("[data-skip-question]")?.addEventListener("click", async () => {
        if (!window.confirm("Are you sure you want to skip this question?")) return;
        captureDrafts();
        if (activeVoice?.questionId === qid) disposeActiveVoice();
        const operationId = skipOperations.get(qid) || newOperationId();
        skipOperations.set(qid, operationId);
        try {
          const attempt = questionResults().get(qid);
          await skipQuizQuestionApi(material.id, run.run_id, qid, operationId, attempt?.version);
          skipOperations.delete(qid);
          await refresh();
          root.querySelector(`#reasoning-question-${index + 1}`)?.scrollIntoView?.({ block: "start" });
        } catch (error) {
          try { await refresh(); } catch { /* keep current drafts */ }
          toast(error.message, { tone: "error" });
        }
      });
      card.querySelector("[data-final-answer-form]")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        captureDrafts();
        const answer = String(drafts.get(qid)?.answer || "").trim();
        if (!answer) { toast("Choose or enter an answer before submitting.", { tone: "error" }); return; }
        const existing = answerOperations.get(qid);
        const operation = existing?.answer === answer ? existing : { id: newOperationId(), answer };
        answerOperations.set(qid, operation);
        if (activeVoice?.questionId === qid) disposeActiveVoice();
        const button = card.querySelector("[data-final-answer-form] button[type=submit]");
        if (button) { button.disabled = true; button.textContent = "Submitting…"; }
        try {
          const attempt = questionResults().get(qid);
          await submitReasoningAnswerApi(material.id, run.run_id, qid, {
            operationId: operation.id, answer, expectedVersion: attempt?.version
          });
          answerOperations.delete(qid);
          await refresh();
        } catch (error) {
          try { await refresh(); } catch { if (button) button.disabled = false; }
          toast(error.message, { tone: "error" });
        }
      });
    });
    schedulePoll();
    for (const questionId of voiceQueues.keys()) void flushVoiceQueue(questionId);
  }

  if (run.status === "completed") { render(); return; }
  render();
}

export function renderQuizPlayer(root, material, { onBack, onAttemptCompleted, runContext } = {}) {
  if (material.isRemote && !runContext) {
    renderRemoteQuizStart(root, material, { onBack, onAttemptCompleted });
    return;
  }
  if (material.isRemote) {
    try {
      const safeRun = normalizePublicQuizRun(runContext.run, {
        materialId: material.id, mode: runContext.mode
      });
      material = { ...material, questions: safeRun.questions };
      runContext = { ...runContext, runId: safeRun.run_id, run: safeRun };
    } catch (error) {
      root.innerHTML = `
        <section class="assessment-view" role="alert">
          <h2>Quiz unavailable</h2>
          <p class="quiz-error">${escapeHtml(error.message || "This quiz could not be securely loaded.")}</p>
          <button class="btn btn-secondary" type="button" data-secure-quiz-back>Back to study materials</button>
        </section>`;
      root.querySelector("[data-secure-quiz-back]")?.addEventListener("click", () => onBack?.());
      return;
    }
  }
  let timerId = null;
  let submitted = false;
  let expiredAnswers = null;
  let disposed = false;
  let submissionAnswerDigest = null;
  let submissionOperationId = null;
  const tutorPanels = new Map();
  const deadline = material.timeLimitMinutes ? Date.now() + material.timeLimitMinutes * 60 * 1000 : null;

  function clearTimer() {
    if (timerId !== null) {
      window.clearInterval(timerId);
      timerId = null;
    }
  }

  function disposeTutorPanels() {
    for (const panel of tutorPanels.values()) panel.dispose();
    tutorPanels.clear();
  }

  function goBack() {
    disposed = true;
    clearTimer();
    disposeTutorPanels();
    window.removeEventListener("hashchange", leave);
    onBack?.();
  }

  const leave = () => { disposed = true; clearTimer(); disposeTutorPanels(); };
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
    const feedbackAvailable = material.isRemote ? attempt.feedbackAvailable === true : true;
    const gradedResults = (attempt.questionResults || []).filter((result) => typeof result.correct === "boolean");
    const correctCount = feedbackAvailable && typeof attempt.correctCount === "number"
      ? attempt.correctCount
      : (feedbackAvailable && gradedResults.length
          ? gradedResults.filter((r) => r.correct).length
          : (material.isRemote ? null : attempt.results.filter((r) => r.isCorrect).length));
    const scoreAvailable = feedbackAvailable && typeof attempt.scorePercent === "number";
    const xpAwardedMarkup = quizOutcomeMarkup(attempt.outcome);

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
            ${scoreAvailable
    ? `<strong>${attempt.scorePercent}%</strong><span>${correctCount ?? "—"} of ${totalCount} correct</span>${xpAwardedMarkup}`
    : `<strong>${material.isRemote ? "Submitted" : "Results"}</strong><span>${material.isRemote ? (feedbackAvailable ? "Feedback is available." : "Feedback is not available yet.") : "Your answers have been scored."}</span>${xpAwardedMarkup}`}
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
            let explanation = material.isRemote ? "" : (question.explanation || "");
            let sources = material.isRemote ? [] : (question.sources || []);
            let isGraded = false;

            if (backendResult) {
              submittedRaw = backendResult.submitted_answer ?? submittedRaw;
              if (feedbackAvailable && typeof backendResult.correct === "boolean") {
                isCorrect = backendResult.correct;
                isGraded = true;
              } else if (feedbackAvailable && ["correct", "incorrect"].includes(backendResult.outcome?.correctness)) {
                isCorrect = backendResult.outcome.correctness === "correct";
                isGraded = true;
              }
              if (feedbackAvailable) {
                correctAnswerVal = backendResult.correct_answer;
                explanation = backendResult.explanation || explanation;
                sources = backendResult.sources?.length ? backendResult.sources : sources;
              }
            } else {
              if (!material.isRemote) {
                const localRes = attempt.results?.[index];
                isCorrect = Boolean(localRes?.isCorrect);
                isGraded = true;
                correctAnswerVal = question.answer;
              }
            }

            const selected = getSubmittedDisplay(question, submittedRaw);
            const correct = getCorrectDisplay(question, correctAnswerVal);

            return `
              <article class="question-review" data-result="${!isGraded ? "submitted" : isCorrect ? "correct" : "incorrect"}">
                <div class="review-heading">
                  <strong>Question ${index + 1}</strong>
                  <span class="pill" data-tone="${!isGraded ? "neutral" : isCorrect ? "good" : "bad"}">
                    <strong>${!isGraded ? "Submitted" : isCorrect ? "Correct" : "Review"}</strong>
                  </span>
                </div>
                <p class="review-prompt">${mathHtml(question.prompt)}</p>
                ${quizOutcomeMarkup(backendResult?.outcome, { question: true }) ? `<p>${quizOutcomeMarkup(backendResult.outcome, { question: true })}</p>` : ""}
                <p class="review-answer">Your answer: <strong>${mathHtml(selected || "None")}</strong></p>
                ${feedbackAvailable && correctAnswerVal ? `<p class="review-answer">Answer key: <strong>${mathHtml(correct)}</strong></p>` : ""}
                ${explanation ? `<p class="review-explanation">${mathHtml(explanation)}</p>` : ""}
                ${feedbackAvailable && sources && sources.length ? `
                  <div class="source-chips" style="margin-top: 0.5rem;">
                    ${sources.map(sourceCitationHtml).join("")}
                  </div>
                ` : ""}
                <div class="review-actions" style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                  ${feedbackAvailable && isGraded && !isCorrect ? `
                    <button class="btn btn-secondary btn-small" type="button" data-ask-tutor="${index}">
                      Ask tutor about this
                    </button>
                  ` : ""}
                  ${material.isRemote ? "" : `<button class="btn btn-ghost btn-small" type="button" data-flag-item="${escapeHtml(question.id || `q-${index + 1}`)}">Flag question</button>`}
                </div>
              </article>
            `;
          }).join("")}
        </div>
        <div class="assessment-actions">
          <button class="btn btn-secondary" type="button" data-retake>${material.isRemote ? (material.type === "Practice test" ? "Retake practice test" : "Retake quiz") : "Try again"}</button>
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
      disposeTutorPanels();
      window.removeEventListener("hashchange", leave);
      if (material.isRemote) {
        renderRemoteQuizStart(root, material, { onBack, onAttemptCompleted }, {
          preferredMode: runContext?.mode || "standard",
          forceNewRun: true
        });
      } else {
        renderQuizPlayer(root, material, { onBack, onAttemptCompleted });
      }
    });

    root.querySelectorAll("[data-ask-tutor]").forEach((button) => {
      button.addEventListener("click", () => {
        if (disposed) return;
        const index = Number(button.dataset.askTutor);
        if (!tutorPanels.has(index)) {
          const question = material.questions[index];
          const questionId = question.id || `q-${index + 1}`;
          const review = attempt.questionResults?.find((result) => String(result.question_id) === String(questionId))
            || attempt.questionResults?.[index];
          const clip = (value, limit = 5000) => String(value ?? "").slice(0, limit);
          const context = material.isRemote ? {
            material_id: material.id, question_id: questionId, attempt_id: attempt.backendAttemptId
          } : {
            question: {
              prompt: clip(question.prompt),
              choices: (question.choices || []).slice(0, 8).map((choice, choiceIndex) =>
                clip(typeof choice === "object" && choice !== null
                  ? `${choice.id ?? choiceIndex}: ${choice.text ?? choice.label ?? ""}`
                  : `${choiceIndex}: ${choice}`, 1000)),
              submitted_answer: clip(getSubmittedDisplay(question, review?.submitted_answer ?? attempt.answers?.[questionId])),
              correct_answer: clip(getCorrectDisplay(question, review?.correct_answer)),
              explanation: clip(review?.explanation || question.explanation)
            }
          };
          tutorPanels.set(index, new StudyTutorPanel(button.closest(".question-review"), button, context));
        }
        tutorPanels.get(index).toggle();
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
    const localResults = [];
    material.questions.forEach((question, index) => {
      const questionKey = question.id || `q-${index + 1}`;
      const raw = expiredAnswers ? expiredAnswers[questionKey] : formData.get(`question-${index}`);
      const submittedVal = raw !== null ? String(raw).trim() : "";
      answersMap[questionKey] = submittedVal;

      if (material.isRemote) return;

      const kind = question.kind || (Array.isArray(question.choices) && question.choices.length ? "multiple_choice" : "short_answer");
      let isCorrect = false;

      if (submittedVal) {
        if (kind === "true_false") {
          const expected = String(question.answer ?? "").toLowerCase();
          isCorrect = submittedVal.toLowerCase() === expected;
        } else if (["short_answer", "free_response"].includes(kind)) {
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

      localResults.push({ questionId: questionKey, submittedVal, isCorrect });
    });

    let correctCount = material.isRemote ? undefined : localResults.filter((r) => r.isCorrect).length;
    let scorePercent = material.isRemote ? undefined : Math.round((correctCount / Math.max(1, material.questions.length)) * 100);
    let questionResults = null;
    let backendAttemptId = null;
    let feedbackAvailable = !material.isRemote;
    let outcome = null;
    if (expired) expiredAnswers = { ...answersMap };
    const inputs = [...form.querySelectorAll("input, textarea, select")];
    inputs.forEach((input) => { input.disabled = true; });

    // Remote scoring and feedback are server-owned. The browser submits only
    // opaque run-question and per-run option IDs, then renders release-approved
    // fields from the run projection.
    if (material.isRemote) {
      const submitBtn = form.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting…";
      try {
        if (!runContext?.runId) throw new Error("This quiz run is no longer available. Reopen the quiz to try again.");
        const answerDigest = JSON.stringify(Object.entries(answersMap).sort(([left], [right]) => left.localeCompare(right)));
        if (submissionAnswerDigest !== answerDigest || !submissionOperationId) {
          submissionAnswerDigest = answerDigest;
          submissionOperationId = getStoredSubmissionOperation(runContext.runId, answersMap);
        }
        const attemptRes = await submitAttemptApi(material.id, answersMap, {
          runId: runContext.runId,
          operationId: submissionOperationId
        });
        if (disposed) return;
        if (!attemptRes || typeof attemptRes !== "object" || Array.isArray(attemptRes)) {
          throw new Error("The server did not confirm that your answers were saved. Try submitting again.");
        }
        backendAttemptId = attemptRes.attempt_id || attemptRes.run_id || runContext.runId;
        let completedRun = null;
        try {
          completedRun = normalizePublicQuizRun(await getQuizRunApi(material.id, runContext.runId), {
            materialId: material.id, mode: "standard"
          });
        } catch {
          // Submission is durable even if the follow-up review fetch fails.
          // Keep the answer snapshot and show a no-leak submitted state.
        }
        if (completedRun) {
          feedbackAvailable = completedRun.feedback_policy.feedback_available;
          outcome = completedRun.outcome
            || normalizePublicQuizOutcome(attemptRes.outcome, completedRun.feedback_policy.feedback_available, material.questions.length);
          questionResults = publicRunReview(completedRun);
          if (feedbackAvailable) {
            scorePercent = typeof completedRun.score_percent === "number"
              ? Math.round(completedRun.score_percent) : undefined;
            correctCount = completedRun.correct_count;
          }
          backendAttemptId = completedRun.run_id;
        } else {
          feedbackAvailable = false;
          outcome = normalizePublicQuizOutcome(attemptRes.outcome, false, material.questions.length);
        }
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
      backendAttemptId,
      ...(typeof scorePercent === "number" ? { scorePercent } : {}),
      ...(typeof correctCount === "number" ? { correctCount } : {}),
      ...(material.isRemote && outcome ? { outcome } : {}),
      ...(material.isRemote ? { feedbackAvailable } : {}),
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
              ${questionContextHtml(question)}
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

  if (material.isRemote && runContext.run.status === "completed") {
    const run = runContext.run;
    const answers = Object.fromEntries((run.attempts || []).flatMap((item) => (
      typeof item.question_id === "string"
        ? [[item.question_id, item.final_answer ?? item.submitted_answer ?? ""]]
        : []
    )));
    renderResults({
      id: run.run_id,
      backendAttemptId: run.run_id,
      answers,
      questionResults: publicRunReview(run),
      feedbackAvailable: run.feedback_policy.feedback_available,
      ...(run.outcome ? { outcome: run.outcome } : {}),
      ...(typeof run.correct_count === "number" ? { correctCount: run.correct_count } : {}),
      ...(typeof run.score_percent === "number" ? { scorePercent: Math.round(run.score_percent) } : {}),
      completedAt: run.completed_at || null,
      timeExpired: false
    });
    return;
  }
  startTimer();
}
