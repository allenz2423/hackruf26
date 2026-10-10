import { icon } from "./icons.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { renderFlashcards } from "./flashcards.js";
import { escapeHtml, toast } from "./ui.js";
import { openMaterialViewer } from "./material-viewer.js";
import {
  getAccessToken,
  getStudyMaterialsApi,
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

function formatRemoteMaterial(detail, klassId, uiTypeFallback) {
  const uiType = detail.kind ? fromApiKind(detail.kind) : (uiTypeFallback || "Quiz");
  const sections = Array.isArray(detail.content?.sections) ? detail.content.sections : [];
  const cards = Array.isArray(detail.content?.cards) ? detail.content.cards : [];
  const rawQuestions = Array.isArray(detail.content?.questions) ? detail.content.questions : [];
  const questions = rawQuestions.map((q, idx) => ({
    id: q.id || `q-${idx + 1}`,
    kind: q.kind || (Array.isArray(q.choices) && q.choices.length ? "multiple_choice" : "short_answer"),
    prompt: q.prompt,
    choices: Array.isArray(q.choices) ? q.choices : [],
    sources: Array.isArray(q.sources) ? q.sources : [],
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

  return {
    id: detail.id,
    classId: klassId,
    title: detail.title || `${uiType}: Material`,
    type: uiType,
    scope: detail.scope?.kind === "all" ? "Whole class" : "Class material",
    status: detail.status || "ready",
    count,
    createdAt: detail.created_at || new Date().toISOString(),
    timeLimitMinutes: detail.options?.time_limit_minutes || (uiType === "Practice test" ? 15 : undefined),
    isRemote: true,
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

function sampleQuestions(type, klass) {
  const subject = klass.focus || klass.name;
  if (type === "Flashcards") return [`Key term from ${subject}`, "Common misconception", "Source-backed definition"];
  if (type === "Study guide") return [`Core concepts in ${subject}`, "Important examples", "What to review next"];
  if (type === "Practice test") return [`Timed free response on ${subject}`, "Explain your reasoning", "Mixed review problem"];
  return [`Quick check on ${subject}`, "Multiple choice concept check", "Short answer with explanation"];
}

function createLocalMaterial(type, klass, requestedCount) {
  const bank = klass.materials.flatMap((material) => material.items || []);
  const sources = klass.uploads.slice(0, 2).map((upload) => upload.name);
  const content = klass.materials.find((material) => material.type === "Study guide")?.content
    || klass.uploads.map((upload) => upload.content || upload.summary).filter(Boolean).join("\n\n");
  const base = {
    id: crypto.randomUUID(),
    classId: klass.id,
    type,
    scope: "Whole class",
    difficulty: "Mixed",
    status: "ready",
    createdAt: new Date().toISOString(),
    sources,
    isSample: false
  };

  if (type === "Flashcards") {
    const cards = klass.materials.flatMap((material) => material.cards || []).slice(0, 12);
    const usableCards = cards.length ? cards : sampleQuestions(type, klass).map((front, index) => ({
      front,
      back: bank[index]?.answer || bank[index]?.explanation || `Review this concept in ${sources[0] || klass.name}.`
    }));
    return { ...base, cards: usableCards, questions: usableCards.map((card) => card.front), count: usableCards.length };
  }

  if (type === "Study guide") {
    const items = bank.slice(0, 12);
    return {
      ...base,
      content: content || `Review focus: ${klass.focus || klass.name}\n\nUse the questions below to guide your review of the class material.`,
      items,
      questions: items.map((item) => item.prompt),
      count: items.length || 1
    };
  }

  const items = bank.filter((item) => item.prompt && Array.isArray(item.choices)).slice(0, Math.max(1, requestedCount));
  const questions = items.length
    ? scoreableQuestions({ id: base.id, items })
    : sampleQuestions(type, klass);
  return {
    ...base,
    ...(type === "Practice test" ? { timeLimitMinutes: 10 } : {}),
    items,
    questions,
    count: questions.length
  };
}

export async function renderStudyTab({ root, klass, onMaterialAdded, onAttemptCompleted }) {
  // Sync remote study materials from backend if authenticated
  const token = await getAccessToken();
  if (token) {
    try {
      const remoteMaterials = await getStudyMaterialsApi(klass.id);
      if (Array.isArray(remoteMaterials) && remoteMaterials.length > 0) {
        const merged = remoteMaterials.map((rm) => {
          const uiType = fromApiKind(rm.kind);
          return {
            id: rm.id,
            classId: klass.id,
            title: rm.title,
            type: uiType,
            scope: rm.scope?.kind === "all" ? "Whole class" : "Class material",
            status: rm.status,
            count: rm.item_count || 10,
            createdAt: rm.created_at,
            timeLimitMinutes: rm.options?.time_limit_minutes || (uiType === "Practice test" ? 15 : undefined),
            isRemote: true,
            attempts: rm.latest_attempt ? [{ scorePercent: Math.round(rm.latest_attempt.score_percent) }] : []
          };
        });
        const localSamples = klass.materials.filter((m) => m.isSample);
        klass.materials = [...localSamples, ...merged];
      }
    } catch (e) {
      console.warn("Could not sync remote study materials:", e);
    }
  }

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
            <div class="field-grid">
              <div class="field-block">
                <label for="generate-scope">Source scope</label>
                <select id="generate-scope" name="scope">
                  <option value="all">Whole class (${klass.uploads.length} materials)</option>
                  <option value="notes">Notes only</option>
                  <option value="syllabus">Syllabus outline topics</option>
                </select>
              </div>
              <div class="field-block">
                <label for="generate-count">Question / card count</label>
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
          ${hasUploads ? "" : `<p class="field-hint">Add course material in the Library tab first.</p>`}
        </form>
        <div class="section-stack">
          <div>
            <h2>Saved materials</h2>
            <p class="muted">Open study guides and flashcards, or start a quiz to see explanations and saved scores.</p>
          </div>
          <div id="material-list"></div>
        </div>
      </section>
    `;

    const form = root.querySelector("#generate-form");
    const list = root.querySelector("#material-list");
    const generateBtn = root.querySelector("#generate-btn");

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
              ${material.isSample ? `<span class="sample-badge">Sample material</span>` : `<span class="ai-label">${escapeHtml(material.status || "Ready")}</span>`}
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
      const data = new FormData(form);
      const uiType = data.get("type");
      const count = Number(data.get("count")) || 10;
      const difficulty = data.get("difficulty") || "mixed";

      const token = await getAccessToken();
      if (token) {
        generateBtn.disabled = true;
        generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generating with Gemini...`;
        try {
          const apiKind = toApiKind(uiType);
          const res = await createStudyMaterialApi(klass.id, {
            kind: apiKind,
            scope: { kind: "all" },
            options: { count, difficulty, time_limit_minutes: uiType === "Practice test" ? 15 : undefined }
          });

          // If generation finished synchronously
          if (res.status === "ready" && res.content) {
            generateBtn.disabled = false;
            generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;
            const formatted = formatRemoteMaterial(res, klass.id, uiType);
            onMaterialAdded(formatted);
            toast(`Generated ${uiType} ready to practice!`);
            renderMaterials();
            return;
          }

          toast("Generating study material with Gemini in the background...", { tone: "busy" });

          // Poll until ready
          let attempts = 0;
          const pollInterval = setInterval(async () => {
            attempts++;
            try {
              const detail = await getStudyMaterialDetailApi(res.id);
              if (detail.status === "ready") {
                clearInterval(pollInterval);
                generateBtn.disabled = false;
                generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;

                const formatted = formatRemoteMaterial(detail, klass.id, uiType);
                onMaterialAdded(formatted);
                toast(`Generated ${uiType} ready to practice!`);
                renderMaterials();
              } else if (detail.status === "failed" || attempts >= 30) {
                clearInterval(pollInterval);
                generateBtn.disabled = false;
                generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;
                toast(detail.status === "failed"
                  ? "Study generation failed on the server. Falling back to local template."
                  : "Generation timed out. Falling back to local template.", { tone: "busy" });
                const localMat = createLocalMaterial(uiType, klass, count);
                onMaterialAdded(localMat);
                renderMaterials();
              }
            } catch {
              if (attempts >= 30) {
                clearInterval(pollInterval);
                generateBtn.disabled = false;
                generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;
                toast("Could not check generation status. Falling back to local template.", { tone: "busy" });
                const localMat = createLocalMaterial(uiType, klass, count);
                onMaterialAdded(localMat);
                renderMaterials();
              }
            }
          }, 3000);
          return;
        } catch (err) {
          generateBtn.disabled = false;
          generateBtn.innerHTML = `${icon("sparkles", { size: 18 })} Generate study set`;
          toast(`Backend generation unavailable (${err.message}). Created local review set.`);
        }
      }

      const material = createLocalMaterial(uiType, klass, count);
      onMaterialAdded(material);
      toast(`${material.type} saved as a review set.`);
      renderMaterials();
    });

    list.addEventListener("click", async (event) => {
      const deleteId = event.target.closest("[data-delete-material]")?.dataset.deleteMaterial;
      if (deleteId) {
        const token = await getAccessToken();
        if (token) {
          deleteStudyMaterialApi(deleteId).catch(() => {});
        }
        klass.materials = klass.materials.filter((m) => m.id !== deleteId);
        toast("Material deleted.");
        renderMaterials();
        return;
      }

      const materialId = event.target.closest("[data-open-material]")?.dataset.openMaterial;
      const material = klass.materials.find((item) => item.id === materialId);
      if (!material) return;

      material.classId = klass.id;

      // If remote and content not yet populated, fetch detail
      const token = await getAccessToken();
      if (token && material.isRemote && !material.questions?.length && !material.cards?.length && !material.content) {
        try {
          const detail = await getStudyMaterialDetailApi(materialId);
          if (detail.status) material.status = detail.status;
          if (detail.content) {
            const parsed = formatRemoteMaterial(detail, klass.id, material.type);
            material.cards = parsed.cards;
            material.sections = parsed.sections;
            material.content = parsed.content;
            material.questions = parsed.questions;
            if (parsed.timeLimitMinutes) material.timeLimitMinutes = parsed.timeLimitMinutes;
          }
        } catch (e) {
          console.warn("Could not load material detail:", e);
        }
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
