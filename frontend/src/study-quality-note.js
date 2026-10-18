import { escapeHtml } from "./ui.js";

export function generatedStudyQualityNote(material, contentLabel) {
  if (!material?.isRemote) return "";
  const label = escapeHtml(contentLabel || "study material");
  return `<p class="guide-quality-note ai-content-quality-note" role="note">
    <strong>AI-generated ${label}.</strong>
    Its claims, answer keys, and explanations have not been independently fact-checked. Use linked course sources to confirm details.
  </p>`;
}
