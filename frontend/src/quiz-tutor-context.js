export function quizTutorContext(question, submittedAnswer, sourceUploadIds = []) {
  const choices = (question.choices || []).map((choice, index) => typeof choice === "object"
    ? `${choice.id ?? index}: ${choice.text ?? choice.label ?? ""}` : `${index}: ${choice}`);
  const buildText = (prompt, options, answer) => [
      `Help me understand this question: ${prompt}`,
      options.length ? `Choices:\n${options.join("\n")}` : "",
      `My attempted answer: ${answer || "I did not answer"}`,
      "Help me work through my mistake with a hint."
    ].filter(Boolean).join("\n\n");
  let text = buildText(question.prompt, choices, submittedAnswer);
  if (text.length > 5000) {
    const clip = (value, limit) => String(value || "").length > limit ? `${String(value).slice(0, limit - 1)}…` : String(value || "");
    text = buildText(clip(question.prompt, 2600), choices.map((choice) => clip(choice, Math.floor(1700 / choices.length))), clip(submittedAnswer, 300));
    text += "\n\n[Long question text shortened; the full question remains in the study set.]";
  }
  return {
    text,
    uploadIds: [...new Set([
      ...sourceUploadIds,
      ...(question.sources || []).map((source) => source?.upload_id).filter(Boolean)
    ])]
  };
}

export function readQuizTutorContext(raw) {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (typeof value?.text === "string") return {
      text: value.text,
      uploadIds: Array.isArray(value.uploadIds) ? value.uploadIds.filter((id) => typeof id === "string") : []
    };
  } catch { /* Earlier versions queued just the question text. */ }
  return { text: raw, uploadIds: [] };
}
