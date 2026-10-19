import { icon } from "./icons.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { renderFlashcards } from "./flashcards.js";
import { escapeHtml, openDialog, toast } from "./ui.js";
import { openMaterialViewer } from "./material-viewer.js";
import { renderMasteryLadderPanel } from "./mastery-ladder.js";
import { loadTutorLanguage, GENERATION_LANGUAGES } from "./tutor-language.js";
import { classHasCapability, updateClass } from "./classes.js";
import { getBackendClassId, getBackendUploadId, prepareStudySources, waitForStudyMaterial } from "./course-sync.js";
import {
  getStudyMaterialsApi,
  getStudyMaterialFoldersApi,
  createStudyMaterialFolderApi,
  renameStudyMaterialFolderApi,
  deleteStudyMaterialFolderApi,
  assignStudyMaterialFolderApi,
  proposeStudyMaterialOrganizationApi,
  applyStudyMaterialOrganizationApi,
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

function continueStudyKey(classId, userId) {
  return `jot-study-last-open:${encodeURIComponent(String(userId || "guest"))}:${encodeURIComponent(String(classId || "local"))}`;
}

export function lastOpenedStudyMaterial(materials, { classId, userId } = {}) {
  const ready = materials.filter((material) => material.status === "ready");
  try {
    const id = globalThis.localStorage?.getItem(continueStudyKey(classId, userId));
    const previous = ready.find((material) => String(material.id) === id);
    if (previous) return previous;
  } catch { /* Browser storage may be unavailable; fall back to the latest ready set. */ }
  return ready[0] || null;
}

function rememberOpenedStudyMaterial(materialId, { classId, userId } = {}) {
  try { globalThis.localStorage?.setItem(continueStudyKey(classId, userId), String(materialId)); }
  catch { /* Resuming is optional when browser storage is disabled. */ }
}

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
  const displaySourceName = (name) => String(name || "").replace(/_/gu, " ").replace(/\s+/gu, " ").trim();
  const scopeLabel = detail.scope?.kind === "all" ? "Whole class"
    : sourceNames.length === 1 && sourceIds.size === 1 ? displaySourceName(sourceNames[0])
      : sourceIds.size ? `${sourceIds.size} selected ${sourceIds.size === 1 ? "upload" : "uploads"}` : "Class material";

  return {
    id: detail.id,
    classId: klassId,
    backendClassId,
    folderId: detail.folder_id || null,
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

/** Start the assistant's owner-private study materials from any class tab. */
export async function createClassAssistantMaterials({
  materialRequest,
  backendClassId,
  klass,
  onMaterialAdded,
  onMaterialsChanged,
  onStatus
}) {
  const requestedKinds = Array.isArray(materialRequest?.kinds) ? materialRequest.kinds : [];
  const uploadIds = Array.isArray(materialRequest?.upload_ids) ? materialRequest.upload_ids : [];
  if (!backendClassId || !requestedKinds.length || !uploadIds.length) {
    throw new Error("Jot could not verify which class sources to use.");
  }
  const started = [];
  function reportMaterial(item) {
    const index = started.findIndex((existing) =>
      (item.id && existing.id === item.id)
      || (!item.id && existing.kind === item.kind && existing.title === item.title)
    );
    if (index >= 0) started[index] = { ...started[index], ...item };
    else started.push(item);
    onStatus?.({ ...started[index >= 0 ? index : started.length - 1] });
  }
  const topic = String(materialRequest.topic || "Requested topic").trim().slice(0, 240);
  for (const kind of requestedKinds.slice(0, 4)) {
    const displayType = fromApiKind(kind);
    const title = `${topic} · ${displayType}`.slice(0, 160);
    const focus = String(materialRequest.learner_focus || "").trim().slice(0, 400);
    const additionalInstructions = [
      `Focus on ${topic}. Keep the material aligned to the selected class uploads.`,
      focus ? `Adapt the explanation and practice to this learner focus: ${focus}` : ""
    ].filter(Boolean).join(" ").slice(0, 1_000);
    try {
      const summary = await createStudyMaterialApi(backendClassId, {
        kind,
        title,
        scope: { kind: "uploads", upload_ids: uploadIds },
        options: {
          count: Math.max(4, Math.min(24, Number(materialRequest.count) || 10)),
          difficulty: "mixed"
        },
        additionalInstructions,
        destination: "personal",
        guideStyles: kind === "study_guide" ? ["balanced", "worked_examples"] : null,
        operationId: secureOperationId()
      });
      await onMaterialAdded?.(formatRemoteMaterial(summary, klass.id, displayType, klass.uploads, backendClassId));
      onMaterialsChanged?.();
      reportMaterial({
        id: summary.id,
        kind: displayType,
        title: summary.title || title,
        status: summary.status || "Generating"
      });

      void waitForStudyMaterial(summary.id, {
        timeoutMs: Number.POSITIVE_INFINITY,
        onStatus: async (detail) => {
          await onMaterialAdded?.(formatRemoteMaterial(detail, klass.id, displayType, klass.uploads, backendClassId));
          onMaterialsChanged?.();
          reportMaterial({
            id: detail.id || summary.id,
            kind: displayType,
            title: detail.title || summary.title || title,
            status: detail.status || "Generating"
          });
        }
      }).catch((error) => {
        reportMaterial({
          id: summary.id,
          kind: displayType,
          title: summary.title || title,
          status: "Status unavailable",
          error: error?.message || "Could not check generation status."
        });
      });
    } catch (error) {
      reportMaterial({
        kind: displayType,
        title,
        status: "Could not start",
        error: error?.message || "Generation request failed."
      });
    }
  }
  if (!started.some((item) => item.status !== "Could not start")) {
    throw new Error(started[0]?.error || "Could not start study-material generation.");
  }
  return started.map(({ error, ...item }) => item);
}

export function displayStudyMaterialTitle(title, className) {
  const original = String(title || "").trim();
  const courseName = String(className || "").trim();
  if (!original || !courseName) return original;

  const prefixes = [...new Set([courseName, courseName.replace(/\s+/g, "")])]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  for (const prefix of prefixes) {
    if (!original.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())) continue;
    const remainder = original.slice(prefix.length);
    const separator = remainder.match(/^\s*(?:[·:|—–-]\s*|\s+)/u);
    if (!separator) continue;
    const shortened = remainder.slice(separator[0].length).trim();
    if (shortened) return shortened;
  }
  return original;
}

export function openRemoteStudyGuideViewer(
  material,
  { klassId, uploads = [], backendClassId = null, canRetry = false, currentUserAvatar = null } = {}
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
  viewer = openMaterialViewer(material, { ...(canRetry ? { onRetry } : {}), currentUserAvatar });
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

export async function renderStudyTab({ root, klass, currentUserId = null, currentUserAvatar = null, onMaterialAdded, onAttemptCompleted, onRegisterAssistantMaterialsRefresh }) {
  const backendClassId = getBackendClassId(klass);
  let studyMaterialFolders = [];
  let selectedStudyFolder = "all";
  let folderLoadFailed = false;
  let organizationProposal = null;
  let organizationBusy = false;
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
      const [materialResult, uploadResult, folderResult] = await Promise.allSettled([
        getStudyMaterialsApi(backendClassId),
        getUploadsApi(backendClassId),
        currentUserId ? getStudyMaterialFoldersApi(backendClassId) : Promise.resolve([])
      ]);
      if (folderResult.status === "fulfilled") studyMaterialFolders = folderResult.value;
      else folderLoadFailed = true;
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
      const personalMaterialsForClass = personalSummaries.filter((material) =>
        material.resource_scope === "personal"
        && String(material.origin_class_id || "") === String(backendClassId)
      );
      const remoteMaterials = [
        ...classMaterials,
        ...personalMaterialsForClass.filter((material) => !classMaterialIds.has(material.id))
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
  }

  // Retain choices when returning from a saved quiz or flashcard player.
  const selectedSourceIds = new Set(klass.uploads.map((upload) => upload.id));
  const sourceStatus = (upload) => upload.status === "failed" ? "Failed — retry in Library"
    : upload.status === "ready" ? "Ready" : getBackendUploadId(upload) ? "Processing" : "Saved in browser";

  function renderStudyView() {
    const hasUploads = klass.uploads.length > 0;
    root.innerHTML = `
      <section class="section-stack">
        <details class="study-create-details" id="study-create-details">
          <summary><span><strong>Create or customize a study set</strong><small>Choose source materials, format, and difficulty when you are ready.</small></span><span class="study-create-chevron">Customize</span></summary>
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
              <label id="generation-instructions-label" for="generation-instructions">Additional instructions (optional)</label>
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
        </details>
        ${backendClassId ? `<div id="mastery-ladder-root"></div>` : ""}
        <div class="section-stack study-saved-section">
          <div>
            <h2>Saved materials</h2>
            <p class="muted">Open study guides and flashcards, or start a quiz to see explanations and saved scores.</p>
          </div>
          <details class="account-status-banner signed-in-banner study-ai-notice" open>
            <summary>${icon("sparkles", { size: 20 })}<span><strong>AI-generated study materials</strong><small>How Jot handles course content</small></span></summary>
            <div class="study-ai-notice-body">Selected course text and prompts are processed by this Jot deployment's administrator-configured AI provider. Scanned files and lecture recordings may use Gemini. Generated materials and quiz scores are saved by the backend.</div>
          </details>
          ${backendClassId && currentUserId ? `
            <div id="material-folder-toolbar" class="study-folder-toolbar"></div>
            <div id="study-organization-preview"></div>` : ""}
          <div id="material-list"></div>
        </div>
      </section>
    `;

    const pageSection = root.querySelector(":scope > section");
    const savedSection = pageSection?.querySelector(".study-saved-section");
    const customizeDetails = pageSection?.querySelector("#study-create-details");
    const aiNotice = pageSection?.querySelector(".study-ai-notice");
    if (aiNotice && window.matchMedia?.("(max-width: 640px)").matches) aiNotice.open = false;
    if (pageSection && savedSection && customizeDetails) {
      pageSection.insertBefore(savedSection, pageSection.firstChild);
      pageSection.append(customizeDetails);
    }

    const form = root.querySelector("#generate-form");
    const list = root.querySelector("#material-list");
    const folderToolbar = root.querySelector("#material-folder-toolbar");
    const organizationPreview = root.querySelector("#study-organization-preview");
    const generateBtn = root.querySelector("#generate-btn");
    const generationStatus = root.querySelector("#generation-status");
    const sourcePicker = root.querySelector("#study-source-picker");
    const sourceCount = root.querySelector("#source-selection-count");
    const instructionsBlock = root.querySelector("#generation-instructions-block");
    const instructionsLabel = root.querySelector("#generation-instructions-label");
    const instructionsHelp = root.querySelector("#generation-instructions-help");
    const instructionsInput = root.querySelector("#generation-instructions");
    const languageBlock = root.querySelector("#generation-language-block");
    const guideTitleBlock = root.querySelector("#study-guide-title-block");
    const guideStyleBlock = root.querySelector("#study-guide-style-block");

    let generating = false;
    let activeMaterialType = "Quiz";
    const instructionDrafts = new Map();
    const liveGuidePreviews = new Map();
    function updateTypeFields(uiType) {
      const isAssessment = ["Quiz", "Practice test"].includes(uiType);
      const isGuide = uiType === "Study guide";
      guideTitleBlock.hidden = uiType !== "Study guide";
      guideStyleBlock.hidden = uiType !== "Study guide";
      root.querySelector("#generate-count-label").textContent = uiType === "Study guide"
        ? "Section count" : uiType === "Flashcards" ? "Card count" : "Question count";
      instructionsBlock.hidden = !isAssessment && !isGuide;
      if (instructionsLabel) {
        instructionsLabel.textContent = isGuide
          ? "Guide instructions (optional)"
          : "Additional instructions (optional)";
      }
      if (instructionsInput) {
        instructionsInput.placeholder = isGuide
          ? "Cover each chapter in order. Emphasize cellular organization, replication, and worked examples."
          : "Focus on chapters 3–5. Emphasize application questions rather than definitions.";
      }
      if (instructionsHelp) {
        instructionsHelp.textContent = isGuide
          ? "Guide coverage, topic order, examples, or visual emphasis. Jot will keep the guide grounded in the selected course materials."
          : "Guide quiz or exam topics and style. Generated questions remain grounded in the selected course materials.";
      }
      languageBlock.hidden = !isAssessment;
    }
    updateTypeFields("Quiz");
    form.addEventListener("change", (event) => {
      if (event.target.name === "type") {
        if (instructionsInput) {
          instructionDrafts.set(activeMaterialType, instructionsInput.value || "");
        }
        activeMaterialType = event.target.value;
        if (instructionsInput) {
          instructionsInput.value = instructionDrafts.get(activeMaterialType) || "";
        }
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
      const selectedUploads = checked
        .map((input) => klass.uploads.find((upload) => upload.id === input.value))
        .filter(Boolean);
      const failedCount = selectedUploads.filter((upload) => upload.status === "failed").length;
      const processingCount = selectedUploads.filter((upload) => upload.status === "processing").length;
      const readyCount = selectedUploads.length - failedCount - processingCount;
      const scopeDescription = checked.length
        ? `${checked.length} of ${klass.uploads.length} selected; only selected sources will be used.`
        : "Select at least one source to continue.";
      const statusDescription = failedCount
        ? ` ${failedCount} selected ${failedCount === 1 ? "source has" : "sources have"} failed; unselect it or retry it in Library.`
        : processingCount
          ? ` ${readyCount} ready; ${processingCount} still processing and will be checked before generation.`
          : checked.length ? ` ${readyCount} ready.` : "";
      sourceCount.textContent = `${scopeDescription}${statusDescription}`;
      sourceCount.classList.toggle("is-error", failedCount > 0);
      generateBtn.disabled = generating || !checked.length || failedCount > 0;
      sourcePicker.querySelector('[data-source-filter="all"]').disabled = !hasUploads || checked.length === klass.uploads.length;
      sourcePicker.querySelector('[data-source-filter="none"]').disabled = !checked.length;
      for (const kind of ["notes", "syllabus"]) {
        sourcePicker.querySelector(`[data-source-filter="${kind}"]`).disabled = !klass.uploads.some((upload) => String(upload.kind).toLowerCase() === kind);
      }
    }

    function watchLiveGuidePreview(material, controller, viewer) {
      const materialId = String(material.id);
      const savedFolderId = material.folderId || null;
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
            { ...detail, folder_id: detail.folder_id || savedFolderId },
            klass.id,
            "Study guide",
            klass.uploads,
            backendClassId
          ));
        }
      }).then((detail) => {
        const updated = formatRemoteMaterial(
          { ...detail, folder_id: detail.folder_id || savedFolderId },
          klass.id,
          "Study guide",
          klass.uploads,
          backendClassId
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
      viewer = openMaterialViewer(material, {
        currentUserAvatar,
        ...(canRetryStudyGuide(material, klass, currentUserId) ? {
          onRetry: async (failedMaterial, updateViewer) => {
            const summary = await retryStudyMaterialApi(failedMaterial.id);
            const pending = formatRemoteMaterial(
              summary, klass.id, "Study guide", klass.uploads, backendClassId
            );
            pending.folderId = failedMaterial.folderId || null;
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
        } : {})
      });
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

    function renderFolderToolbar() {
      if (!folderToolbar) return;
      if (folderLoadFailed) {
        folderToolbar.innerHTML = `
          <div class="study-folder-load-error" role="status">
            <span>Folders could not be loaded.</span>
            <button class="btn btn-secondary btn-small" type="button" data-reload-study-folders>Try again</button>
          </div>`;
        return;
      }
      const counts = new Map(studyMaterialFolders.map((folder) => [folder.id, 0]));
      let unfiledCount = 0;
      for (const material of klass.materials) {
        if (material.folderId && counts.has(material.folderId)) counts.set(material.folderId, counts.get(material.folderId) + 1);
        else unfiledCount += 1;
      }
      const allCount = klass.materials.length;
      const selected = (value) => selectedStudyFolder === value ? 'aria-pressed="true"' : 'aria-pressed="false"';
      folderToolbar.innerHTML = `
        <div class="study-folder-toolbar-head">
          <div>
            <h3>Folders</h3>
            <p class="muted">Private to you. AI uses titles and short outlines; review suggestions before anything moves.</p>
          </div>
          <div class="study-folder-toolbar-actions">
            <button class="btn btn-primary btn-small" type="button" data-organize-study-materials ${!klass.materials.length || organizationBusy ? "disabled" : ""}>
              ${icon("sparkles", { size: 16 })} ${organizationBusy ? "Organizing…" : "Organize with AI"}
            </button>
            <button class="btn btn-secondary btn-small" type="button" data-create-study-folder>
              ${icon("plus", { size: 16 })} New folder
            </button>
          </div>
        </div>
        <nav class="study-folder-nav" aria-label="Saved material folders">
          <button class="study-folder-chip" type="button" data-study-folder-filter="all" ${selected("all")}>
            <span>All materials</span><span class="study-folder-count">${allCount}</span>
          </button>
          <button class="study-folder-chip" type="button" data-study-folder-filter="unfiled" ${selected("unfiled")}>
            <span>Unfiled</span><span class="study-folder-count">${unfiledCount}</span>
          </button>
          ${studyMaterialFolders.map((folder) => `
            <div class="study-folder-entry ${selectedStudyFolder === folder.id ? "is-selected" : ""}">
              <button class="study-folder-chip" type="button" data-study-folder-filter="${escapeHtml(folder.id)}" ${selected(folder.id)}>
                <span class="study-folder-name">${icon("folder", { size: 16 })}${escapeHtml(folder.name)}</span>
                <span class="study-folder-count">${counts.get(folder.id) || 0}</span>
              </button>
              <details class="study-folder-options">
                <summary aria-label="Options for ${escapeHtml(folder.name)}">${icon("more", { size: 16 })}</summary>
                <div class="study-folder-options-menu">
                  <button type="button" data-rename-study-folder="${escapeHtml(folder.id)}">Rename</button>
                  <button type="button" data-delete-study-folder="${escapeHtml(folder.id)}">Delete</button>
                </div>
              </details>
            </div>`).join("")}
      </nav>`;
    }

    function renderOrganizationReview() {
      if (!organizationPreview) return;
      if (!organizationProposal) {
        organizationPreview.innerHTML = "";
        return;
      }
      const sourceById = new Map((organizationProposal.materials || []).map((item) => [String(item.id), item]));
      const proposedFolders = organizationProposal.folders || [];
      const proposalMaterialCount = (organizationProposal.materials || []).length;
      organizationPreview.innerHTML = `
        <section class="study-organizer-review" aria-labelledby="study-organizer-title">
          <header class="study-organizer-review-head">
            <div>
              <span class="study-organizer-eyebrow">${icon("sparkles", { size: 14 })} Suggested by AI</span>
              <h3 id="study-organizer-title">Review your folder plan</h3>
              <p class="muted">Rename folders or change destinations. Nothing moves until you apply the plan.</p>
            </div>
            <div class="study-organizer-summary">
              <strong>${proposalMaterialCount}</strong>
              <span>${proposalMaterialCount === 1 ? "material" : "materials"}</span>
              <span class="study-organizer-draft-badge">Not applied</span>
            </div>
            <button class="btn btn-ghost btn-icon" type="button" data-organization-cancel aria-label="Discard AI folder suggestions">${icon("x", { size: 17 })}</button>
          </header>
          <div class="study-organizer-groups">
            ${proposedFolders.map((folder, index) => `
              <section class="study-organizer-group" data-organization-group="${index}">
                <header class="study-organizer-group-head">
                  <span class="study-organizer-folder-mark">${icon("folder", { size: 17 })}</span>
                  <span class="study-organizer-group-label">Suggested folder ${index + 1}</span>
                  <span class="study-organizer-group-count" data-organization-group-count="${index}">${(folder.material_ids || []).length} ${(folder.material_ids || []).length === 1 ? "item" : "items"}</span>
                </header>
                <label class="field study-organizer-folder-name">
                  <span>Folder name</span>
                  <input type="text" maxlength="80" value="${escapeHtml(folder.name)}" data-organization-folder-name="${index}" aria-label="Name for suggested folder ${index + 1}" />
                </label>
                <ul class="study-organizer-items" data-organization-material-list>
                  ${(folder.material_ids || []).map((materialId) => {
                    const material = sourceById.get(String(materialId)) || {};
                    const sources = Array.isArray(material.sources) ? material.sources : [];
                    return `
                      <li class="study-organizer-item">
                        <div class="study-organizer-item-copy">
                          <strong>${escapeHtml(material.title || "Study material")}</strong>
                          <span class="study-organizer-item-meta">${escapeHtml(material.kind || "Material")}</span>
                          ${sources.length ? `<span class="study-organizer-item-sources">${escapeHtml(sources.slice(0, 2).join(", "))}</span>` : ""}
                        </div>
                        <label class="study-organizer-destination">
                          <span>Move to</span>
                          <select data-organization-material="${escapeHtml(materialId)}" aria-label="Folder for ${escapeHtml(material.title || "study material")}">
                            <option value="">Leave unfiled</option>
                            ${proposedFolders.map((target, targetIndex) => `<option value="${targetIndex}" ${targetIndex === index ? "selected" : ""}>${escapeHtml(target.name)}</option>`).join("")}
                          </select>
                        </label>
                      </li>`;
                  }).join("")}
                </ul>
              </section>`).join("")}
            <section class="study-organizer-group study-organizer-unfiled" data-organization-unfiled hidden>
              <header class="study-organizer-group-head">
                <span class="study-organizer-folder-mark">${icon("folder", { size: 17 })}</span>
                <span class="study-organizer-group-label">Leave unfiled</span>
                <span class="study-organizer-group-count" data-organization-group-count="">0 items</span>
              </header>
              <ul class="study-organizer-items" data-organization-material-list></ul>
            </section>
          </div>
          <footer class="study-organizer-review-actions">
            <span class="muted">Review all ${proposalMaterialCount} ${proposalMaterialCount === 1 ? "material" : "materials"} before saving.</span>
            <div>
              <button class="btn btn-secondary" type="button" data-organization-cancel>Cancel</button>
              <button class="btn btn-primary" type="button" data-organization-apply>Apply organization</button>
            </div>
          </footer>
        </section>`;
    }

    function renderMaterials() {
      renderFolderToolbar();
      renderOrganizationReview();
      const allMaterials = [...klass.materials].reverse();
      const latestReady = lastOpenedStudyMaterial(allMaterials, { classId: backendClassId || klass.id, userId: currentUserId });
      const lastOpenedId = (() => {
        try { return globalThis.localStorage?.getItem(continueStudyKey(backendClassId || klass.id, currentUserId)); }
        catch { return null; }
      })();
      const isResuming = Boolean(latestReady && String(latestReady.id) === String(lastOpenedId));
      const nextActionsMarkup = `
        <section class="study-next-actions" aria-label="Study next">
          <div class="study-next-action-primary">
            <span class="eyebrow">${isResuming ? "Pick up where you left off" : "Continue studying"}</span>
            ${latestReady ? `<h2>${escapeHtml(displayStudyMaterialTitle(latestReady.title || latestReady.type, klass.name))}</h2><p class="muted">${escapeHtml(latestReady.type || "Study material")} · ${escapeHtml(latestReady.scope || "Class material")}${isResuming ? " · Last opened" : " · Latest ready set"}</p><button class="btn btn-primary" type="button" data-open-material="${escapeHtml(latestReady.id)}">${latestReady.type === "Flashcards" ? "Continue flashcards" : "Continue studying"}</button>` : `<h2>No study set yet</h2><p class="muted">Make a study set from ready course sources when you are ready.</p><button class="btn btn-primary" type="button" data-open-customizer>Create study materials</button>`}
          </div>
          <div class="study-next-action-secondary">
            <a class="btn btn-secondary" href="#/progress">Review due topics</a>
            <button class="btn btn-ghost" type="button" data-open-customizer>Create or customize</button>
          </div>
        </section>`;
      const visibleMaterials = selectedStudyFolder === "all"
        ? allMaterials
        : allMaterials.filter((material) => selectedStudyFolder === "unfiled"
          ? !material.folderId
          : material.folderId === selectedStudyFolder);
      if (!allMaterials.length || !visibleMaterials.length) {
        list.innerHTML = `
          ${nextActionsMarkup}
          <div class="panel empty-state">
            <span class="empty-icon">${icon("sparkles", { size: 26 })}</span>
            <strong>${allMaterials.length ? "This folder is empty" : "No study materials yet"}</strong>
            <span>${allMaterials.length ? "Move saved materials here with the Folder menu on each card." : "Create a review set from available class material."}</span>
          </div>`;
        return;
      }

      list.innerHTML = `${nextActionsMarkup}<div class="material-grid">${visibleMaterials.map((material) => {
        material.classId = klass.id;
        const questions = Array.isArray(material.questions) ? material.questions : [];
        const count = material.items?.length || material.cards?.length || questions.length || material.count || 0;
        const lastAttempt = material.attempts?.at(-1);
        const isAssessment = ["Quiz", "Practice test"].includes(material.type);
        const isFlashcards = material.type === "Flashcards";
        const canPlay = isAssessment || isFlashcards;
        const title = displayStudyMaterialTitle(material.title || material.type || "Study material", klass.name);
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
            ${material.isRemote && backendClassId && currentUserId && !folderLoadFailed ? `
              <label class="material-folder-select">
                <span>${icon("folder", { size: 15 })} Folder</span>
                <select data-material-folder="${escapeHtml(material.id)}" aria-label="Move ${escapeHtml(title)} to a folder">
                  <option value="" ${material.folderId ? "" : "selected"}>Unfiled</option>
                  ${studyMaterialFolders.map((folder) => `<option value="${escapeHtml(folder.id)}" ${material.folderId === folder.id ? "selected" : ""}>${escapeHtml(folder.name)}</option>`).join("")}
                </select>
              </label>` : ""}
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

    onRegisterAssistantMaterialsRefresh?.(() => {
      if (document.contains(list)) renderMaterials();
    });

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
      const additionalInstructions = ["Quiz", "Practice test", "Study guide"].includes(uiType)
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

    folderToolbar?.addEventListener("click", async (event) => {
      if (event.target.closest("[data-organize-study-materials]")) {
        organizationBusy = true;
        renderFolderToolbar();
        try {
          organizationProposal = await proposeStudyMaterialOrganizationApi(backendClassId);
          renderOrganizationReview();
        } catch (error) {
          toast(error.message || "Could not organize these materials.", { tone: "error" });
        } finally {
          organizationBusy = false;
          renderFolderToolbar();
        }
        return;
      }

      const filter = event.target.closest("[data-study-folder-filter]")?.dataset.studyFolderFilter;
      if (filter) {
        selectedStudyFolder = filter;
        renderMaterials();
        return;
      }

      if (event.target.closest("[data-reload-study-folders]")) {
        try {
          studyMaterialFolders = await getStudyMaterialFoldersApi(backendClassId);
          folderLoadFailed = false;
          renderMaterials();
        } catch (error) {
          toast(error.message || "Could not load folders.", { tone: "error" });
        }
        return;
      }

      if (event.target.closest("[data-create-study-folder]")) {
        const name = await openDialog({
          title: "Create folder",
          message: "Folders are private to you in this class.",
          input: { label: "Folder name", maxLength: 80 },
          confirmLabel: "Create folder"
        });
        if (typeof name !== "string") return;
        try {
          const folder = await createStudyMaterialFolderApi(backendClassId, name);
          studyMaterialFolders = [...studyMaterialFolders, folder];
          selectedStudyFolder = folder.id;
          renderMaterials();
          toast(`Created “${folder.name}”.`);
        } catch (error) {
          toast(error.message || "Could not create folder.", { tone: "error" });
        }
        return;
      }

      const renameId = event.target.closest("[data-rename-study-folder]")?.dataset.renameStudyFolder;
      if (renameId) {
        const folder = studyMaterialFolders.find((item) => item.id === renameId);
        if (!folder) return;
        const name = await openDialog({
          title: "Rename folder",
          input: { label: "Folder name", value: folder.name, maxLength: 80 },
          confirmLabel: "Save name"
        });
        if (typeof name !== "string") return;
        try {
          const updated = await renameStudyMaterialFolderApi(backendClassId, renameId, name);
          studyMaterialFolders = studyMaterialFolders.map((item) => item.id === renameId ? updated : item);
          renderMaterials();
          toast("Folder renamed.");
        } catch (error) {
          toast(error.message || "Could not rename folder.", { tone: "error" });
        }
        return;
      }

      const deleteId = event.target.closest("[data-delete-study-folder]")?.dataset.deleteStudyFolder;
      if (deleteId) {
        const folder = studyMaterialFolders.find((item) => item.id === deleteId);
        if (!folder) return;
        const confirmed = await openDialog({
          title: `Delete “${folder.name}”?`,
          message: "Materials in this folder will become unfiled. The materials themselves will stay saved.",
          confirmLabel: "Delete folder",
          danger: true
        });
        if (confirmed !== true) return;
        try {
          await deleteStudyMaterialFolderApi(backendClassId, deleteId);
          studyMaterialFolders = studyMaterialFolders.filter((item) => item.id !== deleteId);
          for (const material of klass.materials) {
            if (material.folderId === deleteId) material.folderId = null;
          }
          if (selectedStudyFolder === deleteId) selectedStudyFolder = "unfiled";
          renderMaterials();
          toast("Folder deleted. Its materials are now unfiled.");
        } catch (error) {
          toast(error.message || "Could not delete folder.", { tone: "error" });
        }
      }
    });

    organizationPreview?.addEventListener("input", (event) => {
      const input = event.target.closest("[data-organization-folder-name]");
      if (!input) return;
      const index = input.dataset.organizationFolderName;
      const label = input.value.trim() || `Folder ${Number(index) + 1}`;
      for (const select of organizationPreview.querySelectorAll("[data-organization-material]")) {
        const option = [...select.options].find((item) => item.value === index);
        if (option) option.textContent = label;
      }
    });

    organizationPreview?.addEventListener("change", (event) => {
      const select = event.target.closest("[data-organization-material]");
      if (!select) return;
      const item = select.closest(".study-organizer-item");
      const destination = select.value === ""
        ? organizationPreview.querySelector("[data-organization-unfiled]")
        : organizationPreview.querySelector(`[data-organization-group="${select.value}"]`);
      const destinationList = destination?.querySelector("[data-organization-material-list]");
      if (!item || !destinationList) return;
      destinationList.append(item);

      for (const group of organizationPreview.querySelectorAll(".study-organizer-group")) {
        const count = group.querySelector("[data-organization-group-count]");
        const list = group.querySelector("[data-organization-material-list]");
        const itemCount = list?.children.length || 0;
        if (count) count.textContent = `${itemCount} ${itemCount === 1 ? "item" : "items"}`;
        if (group.hasAttribute("data-organization-unfiled")) group.hidden = itemCount === 0;
      }
    });

    organizationPreview?.addEventListener("click", async (event) => {
      if (event.target.closest("[data-organization-cancel]")) {
        organizationProposal = null;
        renderOrganizationReview();
        return;
      }
      const applyButton = event.target.closest("[data-organization-apply]");
      if (!applyButton || applyButton.disabled || !organizationProposal) return;

      const folderNames = [...organizationPreview.querySelectorAll("[data-organization-folder-name]")]
        .map((input) => input.value.trim());
      if (folderNames.some((name) => !name)) {
        toast("Give every proposed folder a name before applying.", { tone: "error" });
        return;
      }
      const folders = folderNames.map((name) => ({ name, material_ids: [] }));
      const unfiledMaterialIds = [];
      for (const select of organizationPreview.querySelectorAll("[data-organization-material]")) {
        const materialId = select.dataset.organizationMaterial;
        const target = select.value;
        if (target === "") unfiledMaterialIds.push(materialId);
        else if (folders[Number(target)]) folders[Number(target)].material_ids.push(materialId);
      }
      const nonEmptyFolders = folders.filter((folder) => folder.material_ids.length);
      if (!nonEmptyFolders.length) {
        toast("Keep at least one material in a folder, or cancel without applying.", { tone: "error" });
        return;
      }

      applyButton.disabled = true;
      applyButton.textContent = "Applying…";
      try {
        const result = await applyStudyMaterialOrganizationApi(
          backendClassId,
          nonEmptyFolders,
          unfiledMaterialIds
        );
        const folderById = new Map(studyMaterialFolders.map((folder) => [String(folder.id), folder]));
        for (const folder of result.folders || []) folderById.set(String(folder.id), folder);
        studyMaterialFolders = [...folderById.values()];
        const assignmentByMaterial = new Map(
          (result.assignments || []).map((assignment) => [String(assignment.material_id), String(assignment.folder_id)])
        );
        const affected = new Set([
          ...(organizationProposal.materials || []).map((item) => String(item.id)),
          ...unfiledMaterialIds
        ]);
        for (const material of klass.materials) {
          if (affected.has(String(material.id))) material.folderId = assignmentByMaterial.get(String(material.id)) || null;
        }
        organizationProposal = null;
        selectedStudyFolder = "all";
        renderMaterials();
        toast("Study materials organized.");
      } catch (error) {
        toast(error.message || "Could not apply this folder plan.", { tone: "error" });
        applyButton.disabled = false;
        applyButton.textContent = "Apply organization";
      }
    });

    list.addEventListener("click", async (event) => {
      if (event.target.closest("[data-open-customizer]")) {
        const details = root.querySelector("#study-create-details");
        if (details) {
          details.open = true;
          details.scrollIntoView?.({ block: "start", behavior: "smooth" });
          root.querySelector('#study-create-details input[name="source_upload"]')?.focus({ preventScroll: true });
        }
        return;
      }

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
          pending.folderId = material.folderId || null;
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
      rememberOpenedStudyMaterial(material.id, { classId: backendClassId || klass.id, userId: currentUserId });

      if (material.isRemote && (material.status !== "ready" || (!material.questions?.length && !material.cards?.length && !material.content))) {
        try {
          const detail = await getStudyMaterialDetailApi(materialId);
          const refreshed = formatRemoteMaterial(
            { ...detail, folder_id: detail.folder_id || material.folderId || null },
            klass.id,
            material.type,
            klass.uploads,
            backendClassId
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
        openMaterialViewer(material, { currentUserAvatar });
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

    list.addEventListener("change", async (event) => {
      const folderSelect = event.target.closest?.("[data-material-folder]");
      if (folderSelect) {
        const material = klass.materials.find((item) => item.id === folderSelect.dataset.materialFolder);
        if (!material) return;
        folderSelect.disabled = true;
        try {
          const folderId = folderSelect.value || null;
          await assignStudyMaterialFolderApi(backendClassId, material.id, folderId);
          material.folderId = folderId;
          renderMaterials();
          toast(folderId ? "Material moved to folder." : "Material moved to Unfiled.");
        } catch (error) {
          renderMaterials();
          toast(error.message || "Could not move material.", { tone: "error" });
        }
        return;
      }
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
