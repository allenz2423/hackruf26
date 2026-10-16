import { icon } from "./icons.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { renderFlashcards } from "./flashcards.js";
import { escapeHtml, toast } from "./ui.js";
import { openMaterialViewer } from "./material-viewer.js";
import { renderMasteryLadderPanel } from "./mastery-ladder.js";
import { loadTutorLanguage, GENERATION_LANGUAGES } from "./tutor-language.js";
import { classHasCapability, updateClass } from "./classes.js";
import { getBackendClassId, getBackendUploadId, prepareStudySources, waitForStudyMaterial } from "./course-sync.js";
import {
  getStudyMaterialsApi,
  getMyStudyMaterialsApi,
  getUploadsApi,
  createStudyMaterialApi,
  getStudyMaterialDetailApi,
  retryStudyMaterialApi,
  deleteStudyMaterialApi,
  updateResourcePublicationApi
} from "./api.js";

const TYPES = [
  { value: "Quiz", icon: "quiz", hint: "Quick check" },
  { value: "Practice test", icon: "exam", hint: "Timed, exam-style" },
  { value: "Study guide", icon: "guide", hint: "Key ideas summary" },
  { value: "Flashcards", icon: "cards", hint: "Terms and recall" }
];

const TYPE_ICONS = Object.fromEntries(TYPES.map((type) => [type.value, type.icon]));
const MIN_STUDY_ITEM_COUNT = 4;
const MAX_STUDY_ITEM_COUNT = 100;
const generationOperations = new Map();

function secureOperationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("Secure request IDs are unavailable in this browser.");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function privateRequestFingerprint(value) {
  const source = JSON.stringify(value);
  if (!globalThis.crypto?.subtle?.digest || typeof TextEncoder === "undefined") return null;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Reuses one id for retries of the same quiz-generation request without storing its text. */
export async function getStudyMaterialOperationId(classId, payload) {
  const key = `jot-study-generation:${encodeURIComponent(String(classId))}`;
  const canonical = JSON.stringify(payload);
  const inMemory = generationOperations.get(key);
  if (inMemory?.canonical === canonical) return inMemory.operationId;
  const fingerprint = await privateRequestFingerprint(payload).catch(() => null);
  if (fingerprint) {
    try {
      const saved = JSON.parse(globalThis.sessionStorage?.getItem(key) || "null");
      if (saved?.fingerprint === fingerprint && typeof saved.operation_id === "string") {
        generationOperations.set(key, { canonical, operationId: saved.operation_id });
        return saved.operation_id;
      }
    } catch { /* Storage can be disabled; this visit still gets stable retries. */ }
  }
  const operationId = secureOperationId();
  generationOperations.set(key, { canonical, operationId });
  if (fingerprint) {
    try { globalThis.sessionStorage?.setItem(key, JSON.stringify({ fingerprint, operation_id: operationId })); }
    catch { /* Storage is optional; do not persist raw generation instructions. */ }
  }
  return operationId;
}

export function clearStudyMaterialOperationId(classId, operationId) {
  const key = `jot-study-generation:${encodeURIComponent(String(classId))}`;
  if (generationOperations.get(key)?.operationId === operationId) generationOperations.delete(key);
  try {
    const saved = JSON.parse(globalThis.sessionStorage?.getItem(key) || "null");
    if (saved?.operation_id === operationId) globalThis.sessionStorage?.removeItem(key);
  } catch { /* Storage is optional. */ }
}

export function defaultPracticeTestTimeLimit(count) {
  return Math.max(15, Math.ceil(count * 0.75));
}

function toApiKind(uiType) {
  if (uiType === "Practice test") return "practice_exam";
  if (uiType === "Study guide") return "study_guide";
  if (uiType === "Flashcards") return "flashcards";
  return "quiz";
}

function fromApiKind(apiKind) {
  if (apiKind === "practice_exam") return "Practice test";
  if (apiKind === "study_guide") return "Study guide";
  if (apiKind === "flashcards") return "Flashcards";
  return "Quiz";
}

function formatDate(iso) {
  if (!iso) return "recently";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function canRetryStudyGuide(material, klass, currentUserId) {
  if (!material?.isRemote || material.type !== "Study guide") return false;
  const isAuthor = material.createdBy && String(material.createdBy) === String(currentUserId);
  return material.resourceScope === "personal"
    ? Boolean(isAuthor)
    : Boolean(klass?.isOwner || (isAuthor && klass && classHasCapability(klass, "create")));
}

export function formatRemoteMaterial(detail, klassId, uiTypeFallback, uploads = [], backendClassId = null) {
  const uiType = detail.kind ? fromApiKind(detail.kind) : (uiTypeFallback || "Quiz");
  const uploadNames = new Map(uploads.map((upload) => [getBackendUploadId(upload), upload.name]));
  const withSources = (item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    const result = {
      ...item,
      sources: (Array.isArray(item.sources) ? item.sources : []).map((source) =>
        typeof source === "object" && source !== null
          ? { ...source, name: uploadNames.get(source.upload_id) || source.name }
          : source)
    };
    if (Array.isArray(item.blocks)) result.blocks = item.blocks.map(withSources);
    return result;
  };
  const isAssessment = ["Quiz", "Practice test"].includes(uiType);
  const sections = !isAssessment && Array.isArray(detail.content?.sections) ? detail.content.sections.map(withSources) : [];
  const cards = !isAssessment && Array.isArray(detail.content?.cards) ? detail.content.cards.map(withSources) : [];
  const previewSections = uiType === "Study guide" && Array.isArray(detail.preview_content?.sections)
    ? detail.preview_content.sections.map(withSources)
    : [];
  const previewContent = previewSections.length
    ? { ...detail.preview_content, sections: previewSections }
    : null;

  const guideContent = uiType === "Study guide" && sections.length
    ? { ...(detail.content && typeof detail.content === "object" ? detail.content : {}), sections }
    : (!isAssessment && sections.length
      ? sections.map((s) => `## ${s.heading}\n\n${s.body}`).join("\n\n")
      : (!isAssessment && typeof detail.content === "string" ? detail.content : null));

  const count = detail.item_count
    || cards.length
    || sections.length
    || 0;

  const sourceIds = new Set(detail.scope?.upload_ids || []);
  const sourceNames = uploads.filter((upload) => sourceIds.has(getBackendUploadId(upload))).map((upload) => upload.name);
  const scopeLabel = detail.scope?.kind === "all" ? "Whole class"
    : sourceNames.length === 1 && sourceIds.size === 1 ? sourceNames[0]
      : sourceIds.size ? `${sourceIds.size} selected ${sourceIds.size === 1 ? "upload" : "uploads"}` : "Class material";

  return {
    id: detail.id,
    classId: klassId,
    backendClassId,
    title: detail.title || `${uiType}: Material`,
    type: uiType,
    scope: scopeLabel,
    sources: sourceNames,
    sourceUploadIds: [...sourceIds],
    sourceWarnings: detail.source_warnings || [],
    status: detail.status || "ready",
    count,
    createdAt: detail.created_at || new Date().toISOString(),
    timeLimitMinutes: detail.options?.time_limit_minutes || (uiType === "Practice test" ? 15 : undefined),
    isRemote: true,
    resourceScope: detail.resource_scope || "personal",
    createdBy: detail.created_by || detail.owner_id || null,
    publicationState: detail.publication_state || (detail.resource_scope === "personal" || !detail.resource_scope ? null : "published"),
    releaseAt: detail.release_at || null,
    downloadUrls: detail.download_urls || {},
    instructions: typeof detail.content?.instructions === "string" ? detail.content.instructions : "",
    sections,
    content: guideContent,
    previewContent,
    previewRevision: Number.isInteger(detail.preview_revision) ? detail.preview_revision : 0,
    cards,
    // Question and answer material is delivered only by the immutable,
    // per-run public projection. Never put assessment keys in the saved
    // material object or the browser's shared class cache.
    questions: [],
    // Material summaries have no per-run feedback policy. Keep saved scores
    // out of this cache and render them only from a release-approved run DTO.
    attempts: []
  };
}

export function openRemoteStudyGuideViewer(
  material,
  { klassId, uploads = [], backendClassId = null, canRetry = false } = {}
) {
  let viewer;
  const onRetry = async (failedMaterial, updateViewer) => {
    const summary = await retryStudyMaterialApi(failedMaterial.id);
    const pending = formatRemoteMaterial(
      summary, klassId, "Study guide", uploads, backendClassId
    );
    updateViewer({
      ...pending,
      previewContent: failedMaterial.previewContent,
      previewRevision: failedMaterial.previewRevision
    });

    const controller = new AbortController();
    const stopOnNavigation = () => controller.abort();
    const stopOnClose = () => controller.abort();
    window.addEventListener("hashchange", stopOnNavigation, { once: true });
    viewer.dialog.addEventListener("close", stopOnClose, { once: true });
    try {
      const detail = await waitForStudyMaterial(summary.id, {
        signal: controller.signal,
        timeoutMs: Number.POSITIVE_INFINITY,
        onStatus: (nextDetail) => updateViewer(formatRemoteMaterial(
          nextDetail, klassId, "Study guide", uploads, backendClassId
        ))
      });
      const result = formatRemoteMaterial(detail, klassId, "Study guide", uploads, backendClassId);
      updateViewer(result);
      return result;
    } finally {
      window.removeEventListener("hashchange", stopOnNavigation);
      viewer.dialog.removeEventListener("close", stopOnClose);
    }
  };
  viewer = openMaterialViewer(material, canRetry ? { onRetry } : {});
  return viewer;
}

function scoreableQuestions(material) {
  if (!material) return [];
  const questions = Array.isArray(material.questions) ? material.questions : [];
  const playableQuestions = questions.filter((question) => {
    if (!question || typeof question !== "object") return false;
    if (typeof question.prompt !== "string" || !question.prompt.trim()) return false;
    const hasChoices = Array.isArray(question.choices) && question.choices.length > 0;
    const hasKind = typeof question.kind === "string" && question.kind.trim().length > 0;
    return hasChoices || hasKind;
  });

  if (playableQuestions.length) {
    return playableQuestions.map((q, idx) => ({
      id: q.id || `${material.id || "mat"}-q-${idx + 1}`,
      kind: q.kind || (Array.isArray(q.choices) && q.choices.length ? "multiple_choice" : "short_answer"),
      prompt: q.prompt,
      choices: Array.isArray(q.choices) ? q.choices : [],
      sources: Array.isArray(q.sources) ? q.sources : [],
      ...(q.answerIndex !== undefined ? { answerIndex: q.answerIndex } : {}),
      ...(q.answer !== undefined ? { answer: q.answer } : {}),
      explanation: q.explanation || ""
    }));
  }

  return (material.items || []).flatMap((item, index) => {
    if (!item || typeof item !== "object") return [];
    if (typeof item.prompt !== "string" || !item.prompt.trim()) return [];
    const hasChoices = Array.isArray(item.choices) && item.choices.length > 0;
    const hasKind = typeof item.kind === "string" && item.kind.trim().length > 0;
    if (!hasChoices && !hasKind) return [];

    const answerIndex = typeof item.answer === "number"
      ? item.answer
      : Array.isArray(item.choices) ? item.choices.indexOf(item.answer) : -1;

    return [{
      id: item.id || `${material.id || "mat"}-question-${index + 1}`,
      prompt: item.prompt,
      kind: item.kind || (hasChoices ? "multiple_choice" : "short_answer"),
      choices: Array.isArray(item.choices) ? item.choices : [],
      ...(answerIndex >= 0 ? { answerIndex } : {}),
      ...(item.answer !== undefined ? { answer: item.answer } : {}),
      explanation: item.explanation || ""
    }];
  });
}

export async function renderStudyTab({ root, klass, currentUserId = null, onMaterialAdded, onAttemptCompleted }) {
  const backendClassId = getBackendClassId(klass);
  let personalSummaries = [];
  if (currentUserId) {
    try {
      personalSummaries = await getMyStudyMaterialsApi();
    } catch {
      // Disconnected clients retain the existing local/class view.
    }
  }
  if (backendClassId) {
    try {
      const [materialResult, uploadResult] = await Promise.allSettled([
        getStudyMaterialsApi(backendClassId), getUploadsApi(backendClassId)
      ]);
      if (uploadResult.status === "fulfilled") {
        const localByBackendId = new Map(klass.uploads.map((upload) => [getBackendUploadId(upload), upload]));
        const kinds = { notes: "Notes", document: "Document", syllabus: "Syllabus", slides: "Slides", lecture_audio: "Lecture", lecture_video: "Lecture" };
        const remoteUploads = uploadResult.value.map((upload) => ({
          ...localByBackendId.get(upload.id),
          id: localByBackendId.get(upload.id)?.id || upload.id,
          backendId: upload.id,
          name: upload.title || upload.original_filename || "Course material",
          kind: kinds[upload.kind] || "Document",
          status: upload.status,
          progress: upload.processing_progress,
          size: upload.file_size_bytes,
          resourceScope: upload.resource_scope || "class_owned",
          createdBy: upload.created_by || null,
          publicationState: upload.publication_state || "published",
          releaseAt: upload.release_at || null,
          isRemote: true
        }));
        const mappedIds = new Set(remoteUploads.map((upload) => upload.id));
        klass.uploads = [...remoteUploads, ...klass.uploads.filter((upload) => !mappedIds.has(upload.id))];
      } else {
        // Do not display stale remote records when current authorization could not be checked.
        klass.uploads = klass.uploads.filter((upload) => !getBackendUploadId(upload));
      }
      const classMaterials = materialResult.status === "fulfilled" ? materialResult.value : [];
      const classMaterialIds = new Set(classMaterials.map((material) => material.id));
      const remoteMaterials = [
        ...classMaterials,
        ...personalSummaries.filter((material) => !classMaterialIds.has(material.id))
      ];
      const existing = new Map(klass.materials.map((material) => [material.id, material]));
      const merged = remoteMaterials.map((summary) => {
        const cached = existing.get(summary.id);
        return formatRemoteMaterial(
          {
            ...summary,
            download_urls: cached?.downloadUrls,
            content: cached?.status === "ready" ? cached : null
          },
          klass.id,
          undefined,
          klass.uploads,
          summary.resource_scope === "personal" ? null : backendClassId,
        );
      });
      const localMaterials = klass.materials.filter((material) => !material.isRemote);
      klass.materials = [...localMaterials, ...merged];
      // Personal server resources are fetched by owner and never persisted in the
      // shared browser cache, so they remain available after leaving their source class.
      await updateClass(klass.id, {
        uploads: klass.uploads,
        materials: klass.materials.filter((material) => material.resourceScope !== "personal")
      });
    } catch (e) {
      console.warn("Could not sync remote study materials:", e);
    }
  } else if (personalSummaries.length) {
    const existing = new Map(klass.materials.map((material) => [material.id, material]));
    const personalMaterials = personalSummaries.map((summary) => {
      const cached = existing.get(summary.id);
      return formatRemoteMaterial(
        { ...summary, download_urls: cached?.downloadUrls, content: cached?.status === "ready" ? cached : null },
        klass.id,
        undefined,
        klass.uploads,
        null,
      );
    });
    const personalIds = new Set(personalMaterials.map((material) => material.id));
    klass.materials = [...klass.materials.filter((material) => !personalIds.has(material.id)), ...personalMaterials];
  }

  // Retain choices when returning from a saved quiz or flashcard player.
  const selectedSourceIds = new Set(klass.uploads.map((upload) => upload.id));
  const sourceStatus = (upload) => upload.status === "failed" ? "Failed — retry in Library"
    : upload.status === "ready" ? "Ready" : getBackendUploadId(upload) ? "Processing" : "Saved in browser";

  function renderStudyView() {
    const hasUploads = klass.uploads.length > 0;
    root.innerHTML = `
      <section class="section-stack">
        <form class="panel generate-panel" id="generate-form">
          <div class="generate-head">
            <span class="kind-icon">${icon("sparkles", { size: 22 })}</span>
            <div>
              <h2>Create study materials</h2>
              <p class="muted">Generate quizzes, practice tests, guides, or flashcards grounded in your class notes.</p>
            </div>
          </div>
          <div class="generate-fields">
            <fieldset class="field-block">
              <legend>Material type</legend>
              <div class="type-cards">
                ${TYPES.map((type, index) => `
                  <label class="type-card">
                    <input type="radio" name="type" value="${type.value}" ${index === 0 ? "checked" : ""} />
                    <span class="type-card-main">
                      <span class="kind-icon">${icon(type.icon, { size: 18 })}</span>
                      <strong>${type.value}</strong>
                      <span class="muted">${type.hint}</span>
                    </span>
                  </label>
                `).join("")}
              </div>
            </fieldset>
            <fieldset class="study-source-picker" id="study-source-picker" aria-describedby="source-selection-count">
              <legend>Source materials</legend>
              <p class="field-hint">Choose the uploads to use for this study set.</p>
              <div class="source-picker-actions">
                <button class="btn btn-secondary btn-small" type="button" data-source-filter="all">Select all</button>
                <button class="btn btn-ghost btn-small" type="button" data-source-filter="none">Clear</button>
                <button class="btn btn-ghost btn-small" type="button" data-source-filter="notes">Notes only</button>
                <button class="btn btn-ghost btn-small" type="button" data-source-filter="syllabus">Syllabus only</button>
              </div>
              ${hasUploads ? `<div class="study-source-list">${klass.uploads.map((upload) => `
                <label class="study-source-option">
                  <input type="checkbox" name="source_upload" value="${escapeHtml(upload.id)}" ${selectedSourceIds.has(upload.id) ? "checked" : ""} />
                  <span class="study-source-info">
                    <strong>${escapeHtml(upload.name || "Course material")}</strong>
                    <span class="row-meta">${escapeHtml(upload.kind || "Document")} &middot; <span data-source-status="${escapeHtml(upload.id)}">${escapeHtml(sourceStatus(upload))}</span></span>
                  </span>
                </label>`).join("")}</div>` : ""}
              <p id="source-selection-count" class="field-hint" role="status" aria-live="polite"></p>
            </fieldset>
            <div class="field-block" id="study-guide-title-block" hidden>
              <label for="study-guide-title">Study guide name (optional)</label>
              <input id="study-guide-title" name="study_guide_title" type="text" maxlength="160" autocomplete="off" placeholder="Leave blank and AI will suggest a name" aria-describedby="study-guide-title-help" />
              <p class="field-hint" id="study-guide-title-help">Choose a name for your guide, or leave it blank for an AI-suggested title.</p>
            </div>
            <fieldset class="field-block study-guide-style-picker" id="study-guide-style-block" hidden aria-describedby="study-guide-style-help">
              <legend>Study guide styles</legend>
              <p class="field-hint" id="study-guide-style-help">Choose one or more styles to blend. Balanced is selected by default and can be combined with the other styles. Diagrams and interactive activities are included only when supported by your sources and the available templates.</p>
              <div class="study-guide-style-list">
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="balanced" checked /><span><strong>Balanced</strong><small>A clear mix of explanations, definitions, formulas, and examples.</small></span></label>
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="visual" /><span><strong>Visual overview</strong><small>Emphasize relationships, processes, and supported diagrams.</small></span></label>
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="text_dense" /><span><strong>Text-dense explanations</strong><small>Use fuller explanations and explicit connections.</small></span></label>
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="interactive" /><span><strong>Interactive learning</strong><small>Add supported activities where they help explain a concept.</small></span></label>
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="worked_examples" /><span><strong>Worked examples</strong><small>Emphasize source-supported procedures and reasoning.</small></span></label>
                <label class="study-guide-style-option"><input type="checkbox" name="guide_styles" value="quick_reference" /><span><strong>Quick reference</strong><small>Prioritize concise definitions, formulas, and decision rules.</small></span></label>
              </div>
            </fieldset>
            <fieldset class="field-block study-destination-picker">
              <legend>Save this study set</legend>
              <label class="destination-option"><input type="radio" name="destination" value="personal" checked /><span><strong>Keep it personal</strong><small>Only you can see this quiz or study set.</small></span></label>
              ${classHasCapability(klass, "create") ? `<label class="destination-option"><input type="radio" name="destination" value="class_contribution" /><span><strong>Share with this class</strong><small>Starts as a draft. Publish it when it is ready for classmates.</small></span></label>` : ""}
            </fieldset>
            <div class="field-grid">
              <div class="field-block">
                <label for="generate-count" id="generate-count-label">Question count</label>
                <input id="generate-count" name="count" type="number" min="${MIN_STUDY_ITEM_COUNT}" max="${MAX_STUDY_ITEM_COUNT}" step="1" value="10" inputmode="numeric" required />
                <p class="field-hint">Choose ${MIN_STUDY_ITEM_COUNT}–${MAX_STUDY_ITEM_COUNT} items. Larger sets take longer to generate.</p>
              </div>
              <div class="field-block">
                <label for="generate-difficulty">Difficulty</label>
                <select id="generate-difficulty" name="difficulty">
                  <option value="mixed" selected>Mixed difficulty</option>
                  <option value="easier">Foundational</option>
                  <option value="harder">Challenging</option>
                </select>
              </div>
            </div>
            <div class="field-block" id="generation-instructions-block">
              <label for="generation-instructions">Additional instructions (optional)</label>
              <textarea id="generation-instructions" name="additional_instructions" rows="6" maxlength="2000" aria-describedby="generation-instructions-help" placeholder="Focus on chapters 3–5. Emphasize application questions rather than definitions."></textarea>
              <p class="field-hint" id="generation-instructions-help">Guide quiz or exam topics and style. Generated questions remain grounded in the selected course materials.</p>
            </div>
            <div class="field-block" id="generation-language-block">
              <label for="generation-language">Question language</label>
              <select id="generation-language" name="generation_language">
                ${GENERATION_LANGUAGES.map((language) => `<option value="${escapeHtml(language.id)}" ${language.id === loadTutorLanguage() ? "selected" : ""}>${escapeHtml(language.id === "auto" ? "Match source language" : language.label)}</option>`).join("")}
              </select>
              <p class="field-hint">Choose the language used for quiz and practice-test questions.</p>
            </div>
          </div>
          <button id="generate-btn" class="btn btn-primary generate-submit" type="submit" ${hasUploads ? "" : "disabled"}>
            ${icon("sparkles", { size: 18 })} Generate study set
          </button>
          <p id="generation-status" class="field-hint" role="status" aria-live="polite"></p>
          ${hasUploads ? "" : `<p class="field-hint">Add course material in the Library tab first.</p>`}
        </form>
        ${backendClassId ? `<div id="mastery-ladder-root"></div>` : ""}
        <div class="section-stack">
          <div>
            <h2>Saved materials</h2>
            <p class="muted">Open study guides and flashcards, or start a quiz to see explanations and saved scores.</p>
          </div>
          <div class="account-status-banner signed-in-banner">
            ${icon("sparkles", { size: 20 })}
            <div>
              <strong>AI-generated study materials</strong>
              <span>Selected course text and prompts are processed by this Jot deployment's administrator-configured AI provider. Scanned files and lecture recordings may use Gemini. Generated materials and quiz scores are saved by the backend.</span>
            </div>
          </div>
          <div id="material-list"></div>
        </div>
      </section>
    `;

    const form = root.querySelector("#generate-form");
    const list = root.querySelector("#material-list");
    const generateBtn = root.querySelector("#generate-btn");
    const generationStatus = root.querySelector("#generation-status");
    const sourcePicker = root.querySelector("#study-source-picker");
    const sourceCount = root.querySelector("#source-selection-count");
    const instructionsBlock = root.querySelector("#generation-instructions-block");
    const languageBlock = root.querySelector("#generation-language-block");
    const guideTitleBlock = root.querySelector("#study-guide-title-block");
    const guideStyleBlock = root.querySelector("#study-guide-style-block");
    let generating = false;
    const liveGuidePreviews = new Map();
    function updateTypeFields(uiType) {
      const isAssessment = ["Quiz", "Practice test"].includes(uiType);
      guideTitleBlock.hidden = uiType !== "Study guide";
      guideStyleBlock.hidden = uiType !== "Study guide";
      root.querySelector("#generate-count-label").textContent = uiType === "Study guide"
        ? "Section count" : uiType === "Flashcards" ? "Card count" : "Question count";
      instructionsBlock.hidden = !isAssessment;
      languageBlock.hidden = !isAssessment;
    }
    updateTypeFields("Quiz");
    form.addEventListener("change", (event) => {
      if (event.target.name === "type") {
        updateTypeFields(event.target.value);
        return;
      }
      if (
        event.target.name === "guide_styles"
        && !event.target.checked
        && !form.querySelector('input[name="guide_styles"]:checked')
      ) {
        event.target.checked = true;
      }
    });

    function updateSourceSelection() {
      if (!form.isConnected) return;
      const checked = [...sourcePicker.querySelectorAll('input[name="source_upload"]:checked')];
      selectedSourceIds.clear();
      checked.forEach((input) => selectedSourceIds.add(input.value));
      sourceCount.textContent = `${checked.length} of ${klass.uploads.length} ${klass.uploads.length === 1 ? "material" : "materials"} selected${checked.length ? "" : ". Select at least one to generate."}`;
      generateBtn.disabled = generating || !checked.length;
      sourcePicker.querySelector('[data-source-filter="all"]').disabled = !hasUploads || checked.length === klass.uploads.length;
      sourcePicker.querySelector('[data-source-filter="none"]').disabled = !checked.length;
      for (const kind of ["notes", "syllabus"]) {
        sourcePicker.querySelector(`[data-source-filter="${kind}"]`).disabled = !klass.uploads.some((upload) => String(upload.kind).toLowerCase() === kind);
      }
    }

    function watchLiveGuidePreview(material, controller, viewer) {
      const materialId = String(material.id);
      const closeOnNavigation = () => {
        controller.abort();
        viewer.close();
      };
      window.addEventListener("hashchange", closeOnNavigation, { once: true });
      const stopOnClose = () => controller.abort();
      viewer.dialog.addEventListener("close", stopOnClose, { once: true });

      const session = { viewer, promise: null };
      session.promise = waitForStudyMaterial(material.id, {
        signal: controller.signal,
        timeoutMs: Number.POSITIVE_INFINITY,
        onStatus: (detail) => {
          viewer.update(formatRemoteMaterial(
            detail, klass.id, "Study guide", klass.uploads, backendClassId
          ));
        }
      }).then((detail) => {
        const updated = formatRemoteMaterial(
          detail, klass.id, "Study guide", klass.uploads, backendClassId
        );
        viewer.update(updated);
        return updated;
      }).finally(() => {
        window.removeEventListener("hashchange", closeOnNavigation);
        viewer.dialog.removeEventListener("close", stopOnClose);
        if (liveGuidePreviews.get(materialId) === session) {
          liveGuidePreviews.delete(materialId);
        }
      });
      liveGuidePreviews.set(materialId, session);
      return session;
    }

    function startLiveGuidePreview(material, controller) {
      const existing = liveGuidePreviews.get(String(material.id));
      if (existing?.viewer.isOpen) {
        existing.viewer.update(material);
        return existing;
      }

      let viewer;
      viewer = openMaterialViewer(material, canRetryStudyGuide(material, klass, currentUserId) ? {
        onRetry: async (failedMaterial, updateViewer) => {
          const summary = await retryStudyMaterialApi(failedMaterial.id);
          const pending = formatRemoteMaterial(
            summary, klass.id, "Study guide", klass.uploads, backendClassId
          );
          const pendingViewer = {
            ...pending,
            previewContent: failedMaterial.previewContent,
            previewRevision: failedMaterial.previewRevision
          };
          await onMaterialAdded(pending);
          renderMaterials();
          updateViewer(pendingViewer);
          const retryController = new AbortController();
          const retrySession = watchLiveGuidePreview(pendingViewer, retryController, viewer);
          const result = await retrySession.promise;
          await onMaterialAdded(result);
          renderMaterials();
          return result;
        }
      } : {});
      return watchLiveGuidePreview(material, controller, viewer);
    }

    sourcePicker.addEventListener("change", updateSourceSelection);
    sourcePicker.addEventListener("click", (event) => {
      const filter = event.target.closest("[data-source-filter]")?.dataset.sourceFilter;
      if (!filter || generating) return;
      const matches = new Set(klass.uploads.filter((upload) => filter === "all" || String(upload.kind).toLowerCase() === filter).map((upload) => upload.id));
      for (const input of sourcePicker.querySelectorAll('input[name="source_upload"]')) input.checked = matches.has(input.value);
      updateSourceSelection();
    });
    updateSourceSelection();

    const ladderRoot = root.querySelector("#mastery-ladder-root");
    if (ladderRoot) void renderMasteryLadderPanel({ root: ladderRoot, klass, currentUserId });

    function renderMaterials() {
      if (!klass.materials.length) {
        list.innerHTML = `
          <div class="panel empty-state">
            <span class="empty-icon">${icon("sparkles", { size: 26 })}</span>
            <strong>No study materials yet</strong>
            <span>Create a review set from available class material.</span>
          </div>`;
        return;
      }

      list.innerHTML = `<div class="material-grid">${[...klass.materials].reverse().map((material) => {
        material.classId = klass.id;
        const questions = Array.isArray(material.questions) ? material.questions : [];
        const count = material.items?.length || material.cards?.length || questions.length || material.count || 0;
        const lastAttempt = material.attempts?.at(-1);
        const isAssessment = ["Quiz", "Practice test"].includes(material.type);
        const isFlashcards = material.type === "Flashcards";
        const canPlay = isAssessment || isFlashcards;
        const title = material.title || material.type || "Study material";
        const isAuthor = material.createdBy && String(material.createdBy) === String(currentUserId);
        const canDelete = (material.resourceScope === "personal" && isAuthor)
          || klass.isOwner || (isAuthor && classHasCapability(klass, "delete"));
        const canRetry = material.isRemote
          && material.type === "Study guide"
          && material.status === "failed"
          && canRetryStudyGuide(material, klass, currentUserId);
        const canPublish = klass.isOwner || classHasCapability(klass, "manage_permissions")
          || (isAuthor && classHasCapability(klass, "create"));
        const prompts = material.items?.map((item) => item.prompt).filter(Boolean)
          || questions.map((question) => typeof question === "string" ? question : question.prompt).filter(Boolean);

        return `
          <article class="panel material-card">
            <header>
              <span class="kind-icon">${icon(TYPE_ICONS[material.type] || "quiz")}</span>
              <div class="row-main">
                <strong>${escapeHtml(title)}</strong>
                <span class="row-meta">${escapeHtml(material.type || "Material")} &middot; ${escapeHtml(material.scope || "Class material")} &middot; ${count} ${count === 1 ? "item" : "items"}</span>
              </div>
            </header>
            <div class="material-badges">
              ${material.isSample ? `<span class="sample-badge">Sample material</span>` : `<span class="ai-label">${material.isRemote ? `AI-generated &middot; ${escapeHtml(material.status || "ready")}` : "Local draft"}</span>`}
              ${material.resourceScope === "personal" ? `<span class="permission-badge">Personal</span>` : material.publicationState ? `<span class="permission-badge" data-publication="${escapeHtml(material.publicationState)}">${material.publicationState === "scheduled" ? `Scheduled for ${escapeHtml(formatDate(material.releaseAt))}` : escapeHtml(material.publicationState)}</span>` : ""}
              ${lastAttempt ? `<span class="row-meta">${typeof lastAttempt.scorePercent === "number" ? `Last score: <strong>${lastAttempt.scorePercent}%</strong> &middot; ` : ""}${(material.attempts || []).length} ${(material.attempts || []).length === 1 ? "attempt" : "attempts"}</span>` : ""}
            </div>
            <footer>
              <span class="row-meta">${formatDate(material.createdAt)}${material.timeLimitMinutes ? ` &middot; ${material.timeLimitMinutes} min` : ""}</span>
              <div class="material-card-actions">
                <button class="btn ${canPlay ? "btn-primary" : "btn-secondary"} material-open" type="button" data-open-material="${escapeHtml(material.id)}">
                  ${isFlashcards ? "Study cards" : isAssessment ? "Start" : "Open"}
                </button>
                ${canRetry ? `<button class="btn btn-secondary" type="button" data-retry-material="${escapeHtml(material.id)}">Retry</button>` : ""}
                ${material.isRemote && canDelete ? `<button class="btn btn-ghost btn-icon" type="button" data-delete-material="${escapeHtml(material.id)}" aria-label="Delete material">${icon("trash", { size: 16 })}</button>` : ""}
              </div>
            </footer>
            ${material.isRemote && material.resourceScope !== "personal" && canPublish ? `
              <details class="resource-release-control"><summary>Manage release</summary>
                <form data-material-publication="${escapeHtml(material.id)}">
                  <label class="field"><span>Visibility</span><select name="state"><option value="draft" ${material.publicationState === "draft" ? "selected" : ""}>Draft</option><option value="published" ${material.publicationState === "published" ? "selected" : ""}>Published</option><option value="scheduled" ${material.publicationState === "scheduled" ? "selected" : ""}>Schedule release</option></select></label>
                  <label class="field" data-release-date-wrap ${material.publicationState === "scheduled" ? "" : "hidden"}><span>Release date and time</span><input name="release_at" type="datetime-local" value="${escapeHtml(toLocalDateTime(material.releaseAt))}" /></label>
                  <button class="btn btn-secondary btn-small" type="submit">Save release</button>
                </form>
              </details>` : ""}
            ${prompts.length ? `<ul class="preview-list">${prompts.slice(0, 2).map((prompt) => `<li>${escapeHtml(prompt)}</li>`).join("")}</ul>` : ""}
            ${material.sources?.length ? `<div class="source-chips">${material.sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("")}</div>` : ""}
          </article>
        `;
      }).join("")}</div>`;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (generateBtn.disabled) return;
      const data = new FormData(form);
      const uiType = data.get("type");
      const count = Number(data.get("count"));
      if (!Number.isInteger(count) || count < MIN_STUDY_ITEM_COUNT || count > MAX_STUDY_ITEM_COUNT) {
        generationStatus.textContent = `Choose between ${MIN_STUDY_ITEM_COUNT} and ${MAX_STUDY_ITEM_COUNT} items.`;
        root.querySelector("#generate-count").focus();
        return;
      }
      const difficulty = data.get("difficulty") || "mixed";
      const additionalInstructions = ["Quiz", "Practice test"].includes(uiType)
        ? String(data.get("additional_instructions") || "").trim()
        : "";
      const studyGuideTitle = uiType === "Study guide"
        ? String(data.get("study_guide_title") || "").trim()
        : "";
      const studyGuideStyles = uiType === "Study guide"
        ? data.getAll("guide_styles")
        : null;
      const isAssessment = ["Quiz", "Practice test"].includes(uiType);
      const language = isAssessment ? String(data.get("generation_language") || "auto") : null;
      const destination = data.get("destination") === "class_contribution" && classHasCapability(klass, "create")
        ? "class_contribution" : "personal";
      const selectedUploadIds = data.getAll("source_upload");
      const controller = new AbortController();
      let generationOperationId = null;
      let generationClassId = null;
      const stopWaiting = () => controller.abort();
      window.addEventListener("hashchange", stopWaiting, { once: true });
      generating = true;
      sourcePicker.disabled = true;
      updateSourceSelection();
      generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Preparing sources…`;
      try {
        const { classId, uploadIds } = await prepareStudySources(klass, {
          selectedUploadIds,
          signal: controller.signal,
          onProgress: (message) => { generationStatus.textContent = message; }
        });
        generationClassId = classId;
        controller.signal.throwIfAborted();
        generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generating…`;
        generationStatus.textContent = uiType === "Study guide"
          ? "Generating your study guide and PDF from the selected materials…"
          : "Generating from your selected course materials…";
        const options = {
          count,
          difficulty,
          time_limit_minutes: uiType === "Practice test" ? defaultPracticeTestTimeLimit(count) : undefined
        };
        const scope = { kind: "uploads", upload_ids: uploadIds };
        const operationPayload = {
          kind: toApiKind(uiType), scope, options,
          additional_instructions: additionalInstructions || null,
          destination, language
        };
        generationOperationId = isAssessment
          ? await getStudyMaterialOperationId(classId, operationPayload)
          : null;
        const summary = await createStudyMaterialApi(classId, {
          kind: toApiKind(uiType),
          scope,
          options,
          additionalInstructions,
          title: studyGuideTitle,
          guideStyles: studyGuideStyles,
          destination,
          ...(isAssessment ? { language, operationId: generationOperationId } : {})
        });
        if (generationOperationId) clearStudyMaterialOperationId(classId, generationOperationId);
        // Persist the pending job so reloads and polling failures do not lose it.
        await onMaterialAdded(formatRemoteMaterial(summary, klass.id, uiType, klass.uploads, backendClassId));
        renderMaterials();
        const previewSession = uiType === "Study guide"
          ? startLiveGuidePreview(
            formatRemoteMaterial(summary, klass.id, uiType, klass.uploads, backendClassId),
            controller
          )
          : null;
        const detail = previewSession
          ? await previewSession.promise
          : await waitForStudyMaterial(summary.id, { signal: controller.signal });
        await onMaterialAdded(formatRemoteMaterial(detail, klass.id, uiType, klass.uploads, backendClassId));
        renderMaterials();
        if (detail.status === "failed") {
          throw new Error("Study generation failed on the server. Try generating again.");
        }
        if (detail.status !== "ready") {
          generationStatus.textContent = "This material is still generating. Open it later to check its progress.";
          return;
        }
        generationStatus.textContent = `Your ${uiType.toLowerCase()} is ready.`;
        toast(`Generated ${uiType} ready to practice!`);
      } catch (error) {
        if (controller.signal.aborted) return;
        if (generationOperationId && ["question_generation_failed", "question_generation_timeout"].includes(error.code)) {
          clearStudyMaterialOperationId(generationClassId || backendClassId || klass.id, generationOperationId);
        }
        generationStatus.textContent = error.message;
        toast(error.message, { tone: "error" });
      } finally {
        window.removeEventListener("hashchange", stopWaiting);
        generating = false;
        sourcePicker.disabled = false;
        updateSourceSelection();
        for (const label of sourcePicker.querySelectorAll("[data-source-status]")) {
          const upload = klass.uploads.find((item) => item.id === label.dataset.sourceStatus);
          if (upload) label.textContent = sourceStatus(upload);
        }
        generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;
      }
    });

    list.addEventListener("click", async (event) => {
      const retryId = event.target.closest("[data-retry-material]")?.dataset.retryMaterial;
      if (retryId) {
        const material = klass.materials.find((item) => item.id === retryId);
        const button = event.target.closest("[data-retry-material]");
        if (!material || !button) return;
        button.disabled = true;
        button.textContent = "Retrying…";
        try {
          const summary = await retryStudyMaterialApi(retryId);
          const pending = formatRemoteMaterial(
            summary, klass.id, "Study guide", klass.uploads, backendClassId
          );
          await onMaterialAdded(pending);
          renderMaterials();
          const previewMaterial = {
            ...pending,
            previewContent: material.previewContent,
            previewRevision: material.previewRevision
          };
          const session = startLiveGuidePreview(previewMaterial, new AbortController());
          session.promise.catch((error) => {
            if (error?.name !== "AbortError") toast(error.message, { tone: "error" });
          });
        } catch (error) {
          button.disabled = false;
          button.textContent = "Retry";
          toast(error.message || "Could not retry this study guide.", { tone: "error" });
        }
        return;
      }

      const deleteId = event.target.closest("[data-delete-material]")?.dataset.deleteMaterial;
      if (deleteId) {
        const material = klass.materials.find((item) => item.id === deleteId);
        if (material?.isRemote) {
          try {
            await deleteStudyMaterialApi(deleteId);
          } catch (error) {
            toast(error.message, { tone: "error" });
            return;
          }
        }
        klass.materials = klass.materials.filter((m) => m.id !== deleteId);
        await updateClass(klass.id, {
          materials: klass.materials.filter((item) => item.resourceScope !== "personal")
        });
        toast("Material deleted.");
        renderMaterials();
        return;
      }

      const materialId = event.target.closest("[data-open-material]")?.dataset.openMaterial;
      const material = klass.materials.find((item) => item.id === materialId);
      if (!material) return;

      material.classId = klass.id;

      if (material.isRemote && (material.status !== "ready" || (!material.questions?.length && !material.cards?.length && !material.content))) {
        try {
          const detail = await getStudyMaterialDetailApi(materialId);
          const refreshed = formatRemoteMaterial(
            detail, klass.id, material.type, klass.uploads, backendClassId
          );
          if (refreshed.status === "generating" && refreshed.type === "Study guide") {
            const previewController = new AbortController();
            const session = startLiveGuidePreview(refreshed, previewController);
            session.promise.catch((error) => {
              if (!previewController.signal.aborted) {
                toast(error.message, { tone: "error" });
              }
            });
            return;
          }
          if (refreshed.status === "failed" && refreshed.type === "Study guide") {
            const previewController = new AbortController();
            const session = startLiveGuidePreview(refreshed, previewController);
            session.promise.catch((error) => {
              if (!previewController.signal.aborted) {
                toast(error.message, { tone: "error" });
              }
            });
            return;
          }
          Object.assign(material, refreshed);
          await updateClass(klass.id, {
            materials: klass.materials.filter((item) => item.resourceScope !== "personal")
          });
          renderMaterials();
        } catch (e) {
          toast(e.message, { tone: "error" });
          return;
        }
      }
      if (material.isRemote && material.status !== "ready") {
        toast(material.status === "failed"
          ? "Study generation failed. Generate a new study set to retry."
          : "This study material is still generating. Open it again to check progress.", { tone: material.status === "failed" ? "error" : "busy" });
        return;
      }

      if (material.type === "Practice test" && !material.timeLimitMinutes) {
        material.timeLimitMinutes = 15;
      }

      if (material.type === "Flashcards") {
        renderFlashcards(root, material, {
          onBack: renderStudyView,
          onAttemptCompleted: (attempt) => {
            material.attempts = [...(material.attempts || []), attempt];
            onAttemptCompleted?.(material.id, attempt);
          }
        });
        return;
      }

      if (material.isRemote && ["Quiz", "Practice test"].includes(material.type)) {
        // Remote question content is intentionally not read from material
        // detail. The run endpoint supplies a safe immutable snapshot.
        renderQuizPlayer(root, { ...material, questions: [], classId: klass.id }, {
          onBack: renderStudyView,
          onAttemptCompleted: (attempt) => {
            material.attempts = [...(material.attempts || []), attempt];
            onAttemptCompleted?.(material.id, attempt);
          }
        });
        return;
      }

      const questions = scoreableQuestions(material);
      if (!["Quiz", "Practice test"].includes(material.type) || !questions.length) {
        openMaterialViewer(material);
        return;
      }

      const playableMaterial = { ...material, questions, classId: klass.id };
      renderQuizPlayer(root, playableMaterial, {
        onBack: renderStudyView,
        onAttemptCompleted: (attempt) => {
          material.attempts = [...(material.attempts || []), attempt];
          onAttemptCompleted?.(material.id, attempt);
        }
      });
    });

    list.addEventListener("change", (event) => {
      if (!event.target.matches?.('[data-material-publication] [name="state"]')) return;
      const form = event.target.closest("[data-material-publication]");
      form.querySelector("[data-release-date-wrap]").hidden = event.target.value !== "scheduled";
    });
    list.addEventListener("submit", async (event) => {
      const form = event.target.closest?.("[data-material-publication]");
      if (!form) return;
      event.preventDefault();
      const material = klass.materials.find((item) => item.id === form.dataset.materialPublication);
      if (!material) return;
      const state = form.querySelector('[name="state"]').value;
      const localDate = form.querySelector('[name="release_at"]').value;
      if (state === "scheduled" && !localDate) {
        toast("Choose a release date and time.", { tone: "error" });
        return;
      }
      try {
        const response = await updateResourcePublicationApi("study_materials", material.id, {
          state,
          releaseAt: state === "scheduled" ? new Date(localDate).toISOString() : null
        });
        material.publicationState = response.publication_state || state;
        material.releaseAt = response.release_at || (state === "scheduled" ? new Date(localDate).toISOString() : null);
        toast(state === "scheduled" ? "Release scheduled." : `Study material ${state}.`);
        renderMaterials();
      } catch (error) {
        toast(`Could not update release settings: ${error.message}`, { tone: "error" });
      }
    });

    renderMaterials();
  }

  renderStudyView();
}

function toLocalDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.valueOf() - offset).toISOString().slice(0, 16);
}
