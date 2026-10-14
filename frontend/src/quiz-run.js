const SAFE_ATTEMPT_STATES = new Set([
  "awaiting_reasoning",
  "evaluating_reasoning",
  "reasoning_insufficient",
  "reasoning_failed",
  "reasoning_accepted",
  "submitted",
  "completed",
  "skipped"
]);

function safeString(value, maxLength = 12_000) {
  return typeof value === "string" ? value.slice(0, maxLength) : "";
}

function safeSource(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const result = {};
  for (const key of [
    "upload_id", "material_id", "name", "title", "filename", "page", "slide",
    "timestamp_seconds", "timestamp_end_seconds", "paragraph", "block_order_start", "block_order_end"
  ]) {
    const value = source[key];
    if (typeof value === "string" || (typeof value === "number" && Number.isFinite(value))) result[key] = value;
  }
  return Object.keys(result).length ? result : null;
}

function safeFeedbackFields(source) {
  const result = {};
  if (typeof source.correct === "boolean") result.correct = source.correct;
  for (const key of [
    "submitted_answer", "correct_answer", "explanation", "answer_feedback", "reasoning_feedback"
  ]) {
    if (typeof source[key] === "string") result[key] = safeString(source[key], 12_000);
  }
  if (Array.isArray(source.sources)) {
    result.sources = source.sources.map(safeSource).filter(Boolean);
  }
  if (["pending", "available", "failed", "superseded"].includes(source.feedback_status)) {
    result.feedback_status = source.feedback_status;
  }
  return result;
}

export function normalizePublicQuizOutcome(source, feedbackAvailable = false, questionCount = null) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const result = {};
  const maxCount = Number.isSafeInteger(questionCount) && questionCount >= 0 ? questionCount : Number.MAX_SAFE_INTEGER;
  for (const key of ["response_count", "answered_count", "unanswered_count", "correct_count"]) {
    const value = source[key];
    if (Number.isSafeInteger(value) && value >= 0 && value <= maxCount
      && (key !== "correct_count" || feedbackAvailable)) result[key] = value;
  }
  if (Number.isSafeInteger(source.xp_awarded) && source.xp_awarded >= 0 && source.xp_awarded <= 1_000_000) {
    result.xp_awarded = source.xp_awarded;
  }
  if (feedbackAvailable && ["correct", "incorrect", "unanswered", "pending", "unavailable"].includes(source.correctness)) {
    result.correctness = source.correctness;
  } else if (["unanswered", "pending", "unavailable"].includes(source.correctness)) {
    result.correctness = source.correctness;
  }
  return Object.keys(result).length ? result : null;
}

export function quizOutcomeMarkup(outcome, { question = false } = {}) {
  const xp = outcome?.xp_awarded;
  if (!Number.isSafeInteger(xp) || xp < 0) return "";
  return `<span class="row-meta">${xp} XP earned${question ? " for this question" : ""}</span>`;
}

function publicQuestion(question) {
  if (!question || typeof question !== "object" || Array.isArray(question)) return null;
  if (typeof question.id !== "string" || !question.id || typeof question.prompt !== "string" || !question.prompt.trim()) {
    return null;
  }
  const kind = typeof question.kind === "string" ? question.kind : "";
  if (!["multiple_choice", "true_false", "short_answer", "free_response"].includes(kind)) return null;
  const choices = Array.isArray(question.choices)
    ? question.choices.flatMap((choice) => (
      choice && typeof choice === "object" && !Array.isArray(choice)
      && typeof choice.id === "string" && choice.id
      && typeof choice.text === "string"
        ? [{ id: choice.id, text: choice.text }]
        : []
    ))
    : [];
  const responseType = question.interaction?.response_type;
  if (kind === "multiple_choice" && choices.length < 2) return null;
  if (kind === "true_false" && choices.length !== 2) return null;
  if (new Set(choices.map((choice) => choice.id)).size !== choices.length) return null;

  const concepts = Array.isArray(question.concepts)
    ? question.concepts.flatMap((concept) => (
      concept && typeof concept === "object"
      && typeof concept.label === "string"
        ? [{ label: concept.label.slice(0, 240) }]
        : []
    ))
    : [];
  const result = {
    id: question.id,
    prompt: question.prompt,
    kind,
    choices,
    concepts,
    interaction: {
      response_type: typeof responseType === "string"
        ? safeString(responseType, 80)
        : (["multiple_choice", "true_false"].includes(kind) ? "single_choice" : "free_text")
    }
  };
  if (Number.isInteger(question.level) && question.level >= 1 && question.level <= 4) result.level = question.level;
  return result;
}

function publicAttempt(attempt, questionIds, feedbackAvailable, questionCount) {
  if (!attempt || typeof attempt !== "object" || Array.isArray(attempt)) return null;
  const questionId = typeof attempt.question_id === "string" ? attempt.question_id : "";
  if (!questionIds.has(questionId)) return null;
  const state = SAFE_ATTEMPT_STATES.has(attempt.state) ? attempt.state : "awaiting_reasoning";
  const result = { question_id: questionId, state };
  if (typeof attempt.attempt_id === "string") result.attempt_id = attempt.attempt_id;
  if (Number.isSafeInteger(attempt.version) && attempt.version >= 0) result.version = attempt.version;
  if (Array.isArray(attempt.reasoning_turns)) {
    result.reasoning_turns = attempt.reasoning_turns.flatMap((turn) => (
      turn && typeof turn === "object" && typeof turn.text === "string"
        ? [{
          text: safeString(turn.text),
          source: turn.source === "voice" ? "voice" : "text",
          ...(typeof turn.created_at === "string" ? { created_at: turn.created_at } : {})
        }]
        : []
    ));
  }
  if (attempt.evaluation && typeof attempt.evaluation === "object") {
    result.evaluation = {
      ...(typeof attempt.evaluation.status === "string" ? { status: safeString(attempt.evaluation.status, 80) } : {}),
      ...(typeof attempt.evaluation.follow_up === "string" ? { follow_up: safeString(attempt.evaluation.follow_up, 1_000) } : {})
    };
  }
  if (typeof attempt.final_answer === "string") result.final_answer = safeString(attempt.final_answer, 5_000);
  if (["pending", "available", "failed", "superseded"].includes(attempt.feedback_status)) {
    result.feedback_status = attempt.feedback_status;
  }
  const outcome = normalizePublicQuizOutcome(attempt.outcome, feedbackAvailable, questionCount);
  if (outcome) result.outcome = outcome;
  if (feedbackAvailable && ["submitted", "completed"].includes(state)) {
    Object.assign(result, safeFeedbackFields(attempt));
  }
  return result;
}

/**
 * Project a backend run onto the intentionally small public assessment contract.
 * Unknown fields are discarded so answer keys, rubrics, stable bank IDs, and
 * provider rationale never become part of the browser's remote question model.
 */
export function normalizePublicQuizRun(dto, { materialId, mode } = {}) {
  if (!dto || typeof dto !== "object" || Array.isArray(dto)
    || typeof dto.run_id !== "string" || !dto.run_id
    || typeof dto.material_id !== "string" || !dto.material_id
    || (materialId && String(dto.material_id) !== String(materialId))
    || !["standard", "reasoning"].includes(dto.mode)
    || (mode && dto.mode !== mode)
    || !Array.isArray(dto.questions) || !dto.questions.length
    || !dto.feedback_policy || typeof dto.feedback_policy !== "object"
    || typeof dto.feedback_policy.release !== "string"
    || typeof dto.feedback_policy.feedback_available !== "boolean") {
    throw new Error("This quiz is not available in a secure format. Refresh the study material or generate a new quiz.");
  }
  const questions = dto.questions.map(publicQuestion);
  if (questions.some((question) => !question)) {
    throw new Error("This quiz contains a question that cannot be securely displayed. Please generate a new quiz.");
  }
  const questionIds = new Set(questions.map((question) => question.id));
  if (questionIds.size !== questions.length) {
    throw new Error("This quiz contains duplicate question identifiers. Please generate a new quiz.");
  }
  const feedbackAvailable = dto.feedback_policy.feedback_available === true;
  const attempts = Array.isArray(dto.attempts)
    ? dto.attempts.map((attempt) => publicAttempt(attempt, questionIds, feedbackAvailable, questions.length)).filter(Boolean)
    : [];
  const terminalQuestionIds = dto.status === "completed"
    ? questionIds
    : new Set(attempts
      .filter((attempt) => ["submitted", "completed", "skipped"].includes(attempt.state))
      .map((attempt) => attempt.question_id));
  const result = {
    run_id: dto.run_id,
    material_id: dto.material_id,
    mode: dto.mode,
    status: typeof dto.status === "string" ? dto.status : "in_progress",
    feedback_policy: {
      release: safeString(dto.feedback_policy.release, 80),
      feedback_available: feedbackAvailable
    },
    questions,
    attempts
  };
  const outcome = normalizePublicQuizOutcome(dto.outcome, feedbackAvailable, questions.length);
  if (outcome) result.outcome = outcome;
  if (feedbackAvailable) {
    const allTerminal = questions.every((question) => terminalQuestionIds.has(question.id));
    if ((dto.status === "completed" || allTerminal)
      && typeof dto.score_percent === "number" && Number.isFinite(dto.score_percent)) result.score_percent = dto.score_percent;
    if ((dto.status === "completed" || allTerminal)
      && Number.isSafeInteger(dto.correct_count) && dto.correct_count >= 0) result.correct_count = dto.correct_count;
    if (typeof dto.completed_at === "string") result.completed_at = dto.completed_at;
    const reviews = Array.isArray(dto.question_results) ? dto.question_results : [];
    result.question_results = reviews.flatMap((review) => {
      if (!review || typeof review !== "object" || !terminalQuestionIds.has(String(review.question_id))) return [];
      return [{ question_id: String(review.question_id), ...safeFeedbackFields(review) }];
    });
  }
  return result;
}

export function publicRunReview(run) {
  const feedbackAvailable = run?.feedback_policy?.feedback_available === true;
  const byId = new Map();
  if (feedbackAvailable) {
    for (const result of run.question_results || []) {
      if (result && typeof result.question_id === "string") {
        byId.set(String(result.question_id), { question_id: String(result.question_id), ...safeFeedbackFields(result) });
      }
    }
  }
  for (const attempt of run.attempts || []) {
    if (typeof attempt.question_id !== "string") continue;
    const entry = byId.get(String(attempt.question_id)) || { question_id: String(attempt.question_id) };
    if (feedbackAvailable) Object.assign(entry, safeFeedbackFields(attempt));
    const outcome = normalizePublicQuizOutcome(attempt.outcome, feedbackAvailable, run.questions?.length);
    if (outcome) entry.outcome = outcome;
    if (Object.keys(entry).length > 1) byId.set(String(attempt.question_id), entry);
  }
  if (!feedbackAvailable && byId.size === 0) return [];
  return [...byId.values()];
}
