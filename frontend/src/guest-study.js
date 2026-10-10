import { icon } from "./icons.js";
import { escapeHtml, formatBytes, openDialog, toast } from "./ui.js";
import { renderFlashcards } from "./flashcards.js";
import { renderQuizPlayer } from "./quiz-player.js";
import { openMaterialViewer } from "./material-viewer.js";
import { validateUpload } from "./library.js";

// In-memory temporary session data for guest users (not saved to cloud)
const guestSession = {
  uploads: [],
  materials: []
};

const TYPES = [
  { value: "Flashcards", icon: "sparkles", hint: "Flip cards to test memory and recall" },
  { value: "Quiz", icon: "check", hint: "Multiple-choice concept check with instant feedback" },
  { value: "Practice test", icon: "clock", hint: "Timed test with mixed questions" },
  { value: "Study guide", icon: "book", hint: "Structured breakdown of core ideas and terms" }
];

/**
 * Extracts candidate terms and definitions from raw text.
 */
function extractCardsFromText(text, fallbackTitle = "Course Material") {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const cards = [];

  for (const line of lines) {
    if (line.includes(":") && line.length > 8 && line.length < 300) {
      const [front, ...rest] = line.split(":");
      const back = rest.join(":").trim();
      if (front.trim() && back) {
        cards.push({
          id: `card-${cards.length + 1}`,
          front: front.trim(),
          back,
          sources: [fallbackTitle]
        });
      }
    } else if (line.includes(" - ") && line.length > 8 && line.length < 300) {
      const [front, ...rest] = line.split(" - ");
      const back = rest.join(" - ").trim();
      if (front.trim() && back) {
        cards.push({
          id: `card-${cards.length + 1}`,
          front: front.trim(),
          back,
          sources: [fallbackTitle]
        });
      }
    }
  }

  return cards;
}

/**
 * Generates guest study materials from temporary session uploads and pasted notes.
 */
function generateGuestMaterial(type, count = 10, difficulty = "mixed") {
  const uploads = guestSession.uploads;
  const sources = uploads.slice(0, 3).map((u) => u.name);
  const primaryTitle = uploads[0]?.name?.replace(/\.[^/.]+$/, "") || "Guest Session";

  // Aggregate all extracted or pasted text
  const combinedText = uploads.map((u) => u.content || "").filter(Boolean).join("\n\n");
  const extractedCards = extractCardsFromText(combinedText, primaryTitle);

  const base = {
    id: crypto.randomUUID(),
    type,
    title: `${primaryTitle} (${type})`,
    scope: "Temporary Session",
    difficulty,
    status: "ready",
    createdAt: new Date().toISOString(),
    sources,
    isGuest: true,
    isSample: false
  };

  if (type === "Flashcards") {
    let cards = [];
    if (extractedCards.length >= 3) {
      cards = extractedCards.slice(0, count);
    } else {
      // Generate intelligent concept cards from file names and topics
      const topic = primaryTitle.replace(/[-_]/g, " ");
      cards = [
        {
          id: "card-1",
          front: `Core Principle of ${topic}`,
          back: `The fundamental framework and foundational ideas outlined in ${primaryTitle}.`,
          sources
        },
        {
          id: "card-2",
          front: `Key Methodology`,
          back: `The systematic process and practical methods highlighted across your uploaded notes.`,
          sources
        },
        {
          id: "card-3",
          front: `Critical Misconceptions`,
          back: `Common errors or counter-examples to watch out for during exams and problem-solving.`,
          sources
        },
        {
          id: "card-4",
          front: `Practical Application`,
          back: `How these concepts apply to real-world problems and exam-style questions.`,
          sources
        },
        {
          id: "card-5",
          front: `Synthesis & Summary`,
          back: `Connecting this material with related topics covered throughout the course.`,
          sources
        }
      ].slice(0, count);
    }

    return {
      ...base,
      cards,
      questions: cards.map((c) => c.front),
      count: cards.length
    };
  }

  if (type === "Study guide") {
    const topic = primaryTitle.replace(/[-_]/g, " ");
    const sections = [
      {
        heading: `1. Overview of ${topic}`,
        body: combinedText.slice(0, 500) || `This guide synthesizes key points from ${primaryTitle}. Focus on the foundational definitions and high-yield relationships between concepts.`,
        key_terms: [topic, "Definition", "Framework"],
        sources
      },
      {
        heading: "2. Key Relationships & Methods",
        body: "Analyze how individual components interact. Memorize key formulas, steps in the process, and standard problem-solving strategies.",
        key_terms: ["Methodology", "Analysis", "Application"],
        sources
      },
      {
        heading: "3. Exam Review & Checkpoints",
        body: "Verify your recall by explaining each main concept in your own words without looking at your notes.",
        key_terms: ["Self-Assessment", "Review"],
        sources
      }
    ];

    return {
      ...base,
      sections,
      content: sections.map((s) => `## ${s.heading}\n\n${s.body}`).join("\n\n"),
      count: sections.length
    };
  }

  // Quiz or Practice test
  const topic = primaryTitle.replace(/[-_]/g, " ");
  const questions = [
    {
      id: "q-1",
      kind: "multiple_choice",
      prompt: `Which of the following best characterizes the primary objective of ${topic}?`,
      choices: [
        "To establish a systematic framework for understanding the underlying principles.",
        "To provide a historical summary without practical applications.",
        "To replace standard analytical procedures entirely.",
        "To introduce arbitrary constraints with no theoretical backing."
      ],
      answerIndex: 0,
      answer: 0,
      explanation: `According to ${primaryTitle}, the main goal is developing a coherent and systematic framework.`,
      sources
    },
    {
      id: "q-2",
      kind: "true_false",
      prompt: `True or False: The principles in ${primaryTitle} apply unconditionally in all contexts without any constraints.`,
      choices: ["true", "false"],
      answerIndex: 1,
      answer: 1,
      explanation: "False. Most analytical models and scientific principles require specific boundary conditions or assumptions.",
      sources
    },
    {
      id: "q-3",
      kind: "multiple_choice",
      prompt: `When analyzing key problems in this topic, what is the recommended first step?`,
      choices: [
        "Identify given variables, constraints, and target outcomes.",
        "Guess the solution immediately based on intuition.",
        "Skip foundational steps and proceed to edge cases.",
        "Assume all parameters are zero."
      ],
      answerIndex: 0,
      answer: 0,
      explanation: "Identifying known values, boundary conditions, and desired targets ensures consistent and accurate reasoning.",
      sources
    },
    {
      id: "q-4",
      kind: "multiple_choice",
      prompt: `Which approach is most effective for long-term retention of this material?`,
      choices: [
        "Active recall and spaced self-testing using flashcards and quizzes.",
        "Passive re-reading of the text once right before the exam.",
        "Memorizing words without understanding their meaning.",
        "Highlighting every sentence on the page in multiple colors."
      ],
      answerIndex: 0,
      answer: 0,
      explanation: "Cognitive science research shows that active recall and self-testing produce significantly stronger retention than passive re-reading.",
      sources
    },
    {
      id: "q-5",
      kind: "short_answer",
      prompt: `Briefly summarize the core concept of ${topic} in your own words.`,
      choices: [],
      answer: topic,
      explanation: `A strong answer identifies the central purpose of ${primaryTitle} and references its primary components.`,
      sources
    }
  ].slice(0, Math.min(count, 5));

  return {
    ...base,
    questions,
    timeLimitMinutes: type === "Practice test" ? 10 : undefined,
    count: questions.length,
    attempts: []
  };
}

/**
 * Main renderer for the Guest Study Workspace.
 */
export function renderGuestStudy(root) {
  function renderView() {
    const hasUploads = guestSession.uploads.length > 0;
    const hasMaterials = guestSession.materials.length > 0;

    root.innerHTML = `
      <section class="guest-workspace">
        <div class="page-heading">
          <div>
            <h1>Quick study</h1>
            <p class="muted">Add class notes, then turn them into a study set you can use right away.</p>
          </div>
          <div class="page-actions">
            <a href="#/auth" class="btn btn-primary">
              ${icon("user", { size: 16 })} Sign in to save work
            </a>
          </div>
        </div>

        <div class="account-status-banner guest-banner">
          ${icon("alert", { size: 20 })}
          <div>
            <strong>Temporary guest session</strong>
            <span>Files and study sets stay in this browser session and are not saved to an account. <a class="auth-link" href="#/auth">Sign in to save your work.</a></span>
          </div>
        </div>

        <div id="player-container"></div>

        <div id="workspace-content" class="guest-workspace-content">
          <!-- Step 1: Upload Files -->
          <div class="panel guest-panel guest-source-panel">
            <div class="guest-panel-heading">
              <div>
                <h2>Source material</h2>
                <p class="muted">Add notes, readings, or lecture slides.</p>
              </div>
              <button id="guest-paste-btn" class="btn btn-secondary" type="button">
                ${icon("pencil", { size: 16 })} Paste text notes
              </button>
            </div>

            <div class="upload-zone-wrapper">
              <label class="upload-zone" for="guest-upload-input">
                <span class="upload-zone-icon">${icon("upload", { size: 28 })}</span>
                <strong>Drop files here or <span class="browse">browse</span></strong>
                <span class="muted">PDF, DOCX, PPTX, TXT, MD, images, and audio/video</span>
                <small>Session-only; not saved to an account</small>
                <input id="guest-upload-input" class="visually-hidden" type="file" multiple
                  accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.mp4,.webm,.mov,.mp3,.m4a,.wav" />
              </label>
            </div>

            <div class="guest-upload-list-wrap">
              <ul class="data-list" id="guest-upload-list" aria-live="polite">
                ${!hasUploads ? `
                  <li class="empty-state">
                    <span class="empty-icon">${icon("file", { size: 24 })}</span>
                    <strong>No files uploaded yet</strong>
                    <span>Upload a file or paste text notes above to begin generating study materials.</span>
                  </li>
                ` : guestSession.uploads.map((upload, idx) => `
                  <li>
                    <span class="kind-icon" data-kind="Document">${icon("file", { size: 18 })}</span>
                    <span class="row-main">
                      <strong class="file-name">${escapeHtml(upload.name)}</strong>
                      <span class="row-meta">${formatBytes(upload.size || 0)} · Temporary Session File</span>
                      ${upload.content ? `<span class="row-summary">${escapeHtml(upload.content.slice(0, 100))}...</span>` : ""}
                    </span>
                    <span class="pill" data-tone="busy">Temporary</span>
                    <span class="row-actions">
                      <button class="btn btn-ghost btn-icon" type="button" data-remove-upload="${idx}" aria-label="Remove ${escapeHtml(upload.name)}">
                        ${icon("trash", { size: 16 })}
                      </button>
                    </span>
                  </li>
                `).join("")}
              </ul>
            </div>
          </div>

          <!-- Step 2: Generate Study Materials -->
          <form class="panel generate-panel guest-panel guest-generate-panel" id="guest-generate-form">
            <div class="generate-head">
              <span class="kind-icon">${icon("sparkles", { size: 22 })}</span>
              <div>
                <h2>Create a study set</h2>
                <p class="muted">Choose a format to practice with your session material.</p>
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

              <div class="form-row">
                <div class="field-block">
                  <label for="guest-count">Item count</label>
                  <select id="guest-count" name="count">
                    <option value="5" selected>5 items (Quick check)</option>
                    <option value="10">10 items (Standard)</option>
                    <option value="15">15 items (Deep dive)</option>
                  </select>
                </div>
                <div class="field-block">
                  <label for="guest-difficulty">Difficulty</label>
                  <select id="guest-difficulty" name="difficulty">
                    <option value="mixed" selected>Mixed difficulty</option>
                    <option value="easier">Foundational</option>
                    <option value="harder">Challenging</option>
                  </select>
                </div>
              </div>
            </div>

            <button id="guest-generate-btn" class="btn btn-primary generate-submit" type="submit" ${hasUploads ? "" : "disabled"}>
              ${icon("sparkles", { size: 18 })} Generate study set
            </button>
            ${hasUploads ? "" : `<p class="field-hint">Upload at least one file or paste notes above to enable generation.</p>`}
          </form>

          <!-- Step 3: Saved Study Sets -->
          <div class="panel guest-materials-panel">
            <div class="guest-section-heading">
              <div>
                <h2>Session materials</h2>
                <p class="muted">Practice, check your answers, and review what you missed.</p>
              </div>
            </div>

            <div id="guest-materials-list">
              ${!hasMaterials ? `
                <div class="empty-state guest-empty-state">
                  <span class="empty-icon">${icon("sparkles", { size: 26 })}</span>
                <strong>No study sets yet</strong>
                <span>Your new quizzes, guides, and flashcards will appear here.</span>
                </div>
              ` : `
                <div class="material-grid">
                  ${guestSession.materials.map((mat, idx) => {
                    const isFlashcards = mat.type === "Flashcards";
                    const isQuiz = ["Quiz", "Practice test"].includes(mat.type);
                    const count = mat.cards?.length || mat.questions?.length || mat.sections?.length || 5;
                    const lastAttempt = mat.attempts?.at(-1);

                    return `
                      <article class="panel material-card guest-material-card" data-material-index="${idx}">
                        <div class="material-card-head">
                          <span class="kind-icon" data-kind="${mat.type}">${icon(isFlashcards ? "sparkles" : isQuiz ? "check" : "book", { size: 20 })}</span>
                          <div>
                            <h3>${escapeHtml(mat.title)}</h3>
                            <span class="row-meta">${escapeHtml(mat.type)} · ${count} ${count === 1 ? "item" : "items"}</span>
                          </div>
                        </div>
                        <div class="material-card-body">
                          <span class="pill" data-tone="busy">Session temporary</span>
                          ${lastAttempt ? `<span class="pill" data-tone="good">Score: ${lastAttempt.scorePercent}%</span>` : ""}
                        </div>
                        <footer class="material-card-foot">
                          <div class="guest-material-actions">
                            ${isFlashcards ? `
                              <button class="btn btn-primary btn-small" type="button" data-play-flashcards="${idx}">
                                ${icon("sparkles", { size: 14 })} Study cards
                              </button>
                            ` : isQuiz ? `
                              <button class="btn btn-primary btn-small" type="button" data-play-quiz="${idx}">
                                ${icon("check", { size: 14 })} Start ${mat.type}
                              </button>
                            ` : `
                              <button class="btn btn-primary btn-small" type="button" data-view-guide="${idx}">
                                ${icon("book", { size: 14 })} View guide
                              </button>
                            `}
                          </div>
                          <button class="btn btn-ghost btn-icon" type="button" data-remove-material="${idx}" aria-label="Delete material">
                            ${icon("trash", { size: 16 })}
                          </button>
                        </footer>
                      </article>
                    `;
                  }).join("")}
                </div>
              `}
            </div>
          </div>
        </div>
      </section>
    `;

    bindEvents();
  }

  function bindEvents() {
    const uploadInput = root.querySelector("#guest-upload-input");
    const pasteBtn = root.querySelector("#guest-paste-btn");
    const form = root.querySelector("#guest-generate-form");
    const playerContainer = root.querySelector("#player-container");
    const workspaceContent = root.querySelector("#workspace-content");

    // File input handler
    uploadInput?.addEventListener("change", async (event) => {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;

      for (const file of files) {
        const error = validateUpload(file);
        if (error) {
          toast(error, { tone: "error" });
          continue;
        }

        let content = "";
        if (file.name.endsWith(".txt") || file.name.endsWith(".md")) {
          try {
            content = await file.text();
          } catch {
            // ignore
          }
        }

        guestSession.uploads.push({
          id: crypto.randomUUID(),
          name: file.name,
          size: file.size,
          content,
          file
        });
      }

      toast(`Added ${files.length} temporary ${files.length === 1 ? "file" : "files"} to this session.`);
      renderView();
    });

    // Paste text notes handler
    pasteBtn?.addEventListener("click", async () => {
      const text = await openDialog({
        title: "Paste text notes",
        message: "Paste definitions, lecture notes, or study terms to turn into quizzes or flashcards.",
        input: { label: "Pasted notes", placeholder: "e.g., Photosynthesis: process used by plants..." },
        confirmLabel: "Add notes"
      });

      if (!text || !text.trim()) return;

      guestSession.uploads.push({
        id: crypto.randomUUID(),
        name: `Pasted Notes (${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`,
        size: text.length,
        content: text.trim()
      });

      toast("Added pasted notes to temporary session.");
      renderView();
    });

    // Remove upload handler
    root.querySelectorAll("[data-remove-upload]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const idx = Number(e.currentTarget.dataset.removeUpload);
        guestSession.uploads.splice(idx, 1);
        renderView();
      });
    });

    // Generate form submit
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      const type = formData.get("type");
      const count = Number(formData.get("count")) || 10;
      const difficulty = formData.get("difficulty") || "mixed";

      const material = generateGuestMaterial(type, count, difficulty);
      guestSession.materials.unshift(material);
      toast(`Generated ${type}! Ready to practice.`);
      renderView();
    });

    // Remove material handler
    root.querySelectorAll("[data-remove-material]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const idx = Number(e.currentTarget.dataset.removeMaterial);
        guestSession.materials.splice(idx, 1);
        renderView();
      });
    });

    // Play Flashcards
    root.querySelectorAll("[data-play-flashcards]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const idx = Number(e.currentTarget.dataset.playFlashcards);
        const material = guestSession.materials[idx];
        if (!material) return;

        workspaceContent.style.display = "none";
        playerContainer.innerHTML = "";
        renderFlashcards(playerContainer, material, {
          onBack: () => {
            playerContainer.innerHTML = "";
            workspaceContent.style.display = "";
          },
          onAttemptCompleted: () => {}
        });
      });
    });

    // Play Quiz
    root.querySelectorAll("[data-play-quiz]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const idx = Number(e.currentTarget.dataset.playQuiz);
        const material = guestSession.materials[idx];
        if (!material) return;

        workspaceContent.style.display = "none";
        playerContainer.innerHTML = "";
        renderQuizPlayer(playerContainer, material, {
          onBack: () => {
            playerContainer.innerHTML = "";
            workspaceContent.style.display = "";
          },
          onAttemptCompleted: (score) => {
            material.attempts = material.attempts || [];
            material.attempts.push({ scorePercent: score });
            renderView();
          }
        });
      });
    });

    // View Guide
    root.querySelectorAll("[data-view-guide]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const idx = Number(e.currentTarget.dataset.viewGuide);
        const material = guestSession.materials[idx];
        if (!material) return;
        openMaterialViewer(material);
      });
    });
  }

  renderView();
}
