import { icon } from "./icons.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { renderFlashcards } from "./flashcards.js";
import { escapeHtml, toast } from "./ui.js";
import { openMaterialViewer } from "./material-viewer.js";
import { updateClass } from "./classes.js";
import { getBackendClassId, getBackendUploadId, prepareStudySources, waitForStudyMaterial } from "./course-sync.js";
import {
  getStudyMaterialsApi,
  getUploadsApi,
  createStudyMaterialApi,
  getStudyMaterialDetailApi,
  deleteStudyMaterialApi
} from "./api.js";

const TYPES = [
  { value: "Quiz", icon: "quiz", hint: "Quick check" },
  { value: "Practice test", icon: "exam", hint: "Timed, exam-style" },
  { value: "Study guide", icon: "guide", hint: "Key ideas summary" },
  { value: "Flashcards", icon: "cards", hint: "Terms and recall" }
];

const TYPE_ICONS = Object.fromEntries(TYPES.map((type) => [type.value, type.icon]));

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

function formatRemoteMaterial(detail, klassId, uiTypeFallback, uploads = []) {
  const uiType = detail.kind ? fromApiKind(detail.kind) : (uiTypeFallback || "Quiz");
  const uploadNames = new Map(uploads.map((upload) => [getBackendUploadId(upload), upload.name]));
  const withSources = (item) => ({ ...item, sources: (item.sources || []).map((source) =>
    typeof source === "object" && source !== null ? { ...source, name: uploadNames.get(source.upload_id) || source.name } : source) });
  const sections = Array.isArray(detail.content?.sections) ? detail.content.sections.map(withSources) : [];
  const cards = Array.isArray(detail.content?.cards) ? detail.content.cards.map(withSources) : [];
  const rawQuestions = Array.isArray(detail.content?.questions) ? detail.content.questions : [];
  const questions = rawQuestions.map((q, idx) => ({
    id: q.id || `q-${idx + 1}`,
    kind: q.kind || (Array.isArray(q.choices) && q.choices.length ? "multiple_choice" : "short_answer"),
    prompt: q.prompt,
    choices: Array.isArray(q.choices) ? q.choices : [],
    sources: withSources(q).sources,
    ...(q.answerIndex !== undefined ? { answerIndex: q.answerIndex } : {}),
    ...(q.answer !== undefined ? { answer: q.answer } : {}),
    explanation: q.explanation || ""
  }));

  const guideContent = sections.length
    ? sections.map((s) => `## ${s.heading}\n\n${s.body}`).join("\n\n")
    : (typeof detail.content === "string" ? detail.content : null);

  const count = detail.item_count
    || questions.length
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
    downloadUrls: detail.download_urls || {},
    instructions: detail.content?.instructions || "",
    sections,
    content: guideContent,
    cards,
    questions,
    attempts: detail.latest_attempt ? [{ scorePercent: Math.round(detail.latest_attempt.score_percent) }] : []
  };
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

export async function renderStudyTab({ root, klass, onMaterialAdded, onAttemptCompleted }) {
  const backendClassId = getBackendClassId(klass);
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
          isRemote: true
        }));
        const mappedIds = new Set(remoteUploads.map((upload) => upload.id));
        klass.uploads = [...remoteUploads, ...klass.uploads.filter((upload) => !mappedIds.has(upload.id))];
      }
      const remoteMaterials = materialResult.status === "fulfilled" ? materialResult.value : klass.materials.filter((material) => material.isRemote);
      const existing = new Map(klass.materials.map((material) => [material.id, material]));
      const merged = remoteMaterials.map((summary) => {
        const cached = existing.get(summary.id);
        return materialResult.status === "fulfilled"
          ? formatRemoteMaterial({ ...summary, download_urls: cached?.downloadUrls, content: cached?.status === "ready" ? cached : null }, klass.id, undefined, klass.uploads)
          : summary;
      });
      const localMaterials = klass.materials.filter((material) => !material.isRemote);
      klass.materials = [...localMaterials, ...merged];
      await updateClass(klass.id, { uploads: klass.uploads, materials: klass.materials });
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
            <div class="field-grid">
              <div class="field-block">
                <label for="generate-count" id="generate-count-label">Question count</label>
                <select id="generate-count" name="count">
                  <option value="5">5 questions</option>
                  <option value="10" selected>10 questions</option>
                  <option value="15">15 questions</option>
                  <option value="20">20 questions</option>
                </select>
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
          </div>
          <button id="generate-btn" class="btn btn-primary generate-submit" type="submit" ${hasUploads ? "" : "disabled"}>
            ${icon("sparkles", { size: 18 })} Generate study set
          </button>
          <p id="generation-status" class="field-hint" role="status" aria-live="polite"></p>
          ${hasUploads ? "" : `<p class="field-hint">Add course material in the Library tab first.</p>`}
        </form>
        <div class="section-stack">
          <div>
            <h2>Saved materials</h2>
            <p class="muted">Open study guides and flashcards, or start a quiz to see explanations and saved scores.</p>
          </div>
          <div class="account-status-banner signed-in-banner">
            ${icon("sparkles", { size: 20 })}
            <div>
              <strong>AI-generated study materials</strong>
              <span>Your selected course files are uploaded for processing before generation. Generated materials and quiz scores are saved by the backend.</span>
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
    let generating = false;
    form.addEventListener("change", (event) => {
      if (event.target.name !== "type") return;
      root.querySelector("#generate-count-label").textContent = event.target.value === "Study guide"
        ? "Section count" : event.target.value === "Flashcards" ? "Card count" : "Question count";
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

    sourcePicker.addEventListener("change", updateSourceSelection);
    sourcePicker.addEventListener("click", (event) => {
      const filter = event.target.closest("[data-source-filter]")?.dataset.sourceFilter;
      if (!filter || generating) return;
      const matches = new Set(klass.uploads.filter((upload) => filter === "all" || String(upload.kind).toLowerCase() === filter).map((upload) => upload.id));
      for (const input of sourcePicker.querySelectorAll('input[name="source_upload"]')) input.checked = matches.has(input.value);
      updateSourceSelection();
    });
    updateSourceSelection();

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
              ${lastAttempt ? `<span class="row-meta">Last score: <strong>${lastAttempt.scorePercent}%</strong> &middot; ${(material.attempts || []).length} ${(material.attempts || []).length === 1 ? "attempt" : "attempts"}</span>` : ""}
            </div>
            <footer>
              <span class="row-meta">${formatDate(material.createdAt)}${material.timeLimitMinutes ? ` &middot; ${material.timeLimitMinutes} min` : ""}</span>
              <div style="display: flex; gap: 0.5rem; align-items: center;">
                <button class="btn ${canPlay ? "btn-primary" : "btn-secondary"} material-open" type="button" data-open-material="${escapeHtml(material.id)}">
                  ${isFlashcards ? "Study cards" : isAssessment ? "Start" : "Open"}
                </button>
                <button class="btn btn-ghost btn-icon" type="button" data-delete-material="${escapeHtml(material.id)}" aria-label="Delete material">
                  ${icon("trash", { size: 16 })}
                </button>
              </div>
            </footer>
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
      const count = Number(data.get("count")) || 10;
      const difficulty = data.get("difficulty") || "mixed";
      const selectedUploadIds = data.getAll("source_upload");
      const controller = new AbortController();
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
        controller.signal.throwIfAborted();
        generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generating…`;
        generationStatus.textContent = uiType === "Study guide"
          ? "Generating your study guide and PDF from the selected materials…"
          : "Generating from your selected course materials…";
        const summary = await createStudyMaterialApi(classId, {
          kind: toApiKind(uiType),
          scope: { kind: "uploads", upload_ids: uploadIds },
          options: { count, difficulty, time_limit_minutes: uiType === "Practice test" ? 15 : undefined }
        });
        // Persist the pending job so reloads and polling failures do not lose it.
        await onMaterialAdded(formatRemoteMaterial(summary, klass.id, uiType, klass.uploads));
        renderMaterials();
        const detail = await waitForStudyMaterial(summary.id, { signal: controller.signal });
        await onMaterialAdded(formatRemoteMaterial(detail, klass.id, uiType, klass.uploads));
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
        await updateClass(klass.id, { materials: klass.materials });
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
          Object.assign(material, formatRemoteMaterial(detail, klass.id, material.type, klass.uploads));
          await updateClass(klass.id, { materials: klass.materials });
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

    renderMaterials();
  }

  renderStudyView();
}
