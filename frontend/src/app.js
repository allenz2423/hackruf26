import "./styles.css";
import { AnnotationController } from "./annotations.js";
import { AudioController } from "./audio.js";
import { getClasses, getClassById, createClass, updateClass, deleteClass, resetSampleData } from "./classes.js";
import { GeminiLiveController } from "./gemini-live.js";
import { icon } from "./icons.js";
import { renderLibraryTab } from "./library.js";
import { createRouter } from "./router.js";
import { ScreenCaptureController } from "./screen-capture.js";
import { renderStudyTab } from "./study.js";
import { deleteUploadedFile, deleteUploadedFiles } from "./file-store.js";
import { escapeHtml, openDialog, toast } from "./ui.js";

const app = document.querySelector("#app");

const state = {
  session: "idle",
  connection: "offline",
  screen: "not sharing",
  mic: "off",
  messages: []
};

const TONES = {
  session: { active: "good", connecting: "busy", selecting_screen: "busy", screen_paused: "busy", reconnecting: "busy", error: "bad" },
  connection: { connected: "good", connecting: "busy", error: "bad" },
  screen: { sharing: "live", selecting: "busy", paused: "busy" },
  mic: { on: "live" }
};

const STATUS_LABELS = { session: "Session", connection: "Live", screen: "Screen", mic: "Mic" };

function statusPill(key) {
  const value = state[key];
  return `<span class="pill" data-status="${key}" data-tone="${TONES[key][value] || ""}">
    ${STATUS_LABELS[key]} <strong>${escapeHtml(value.replace(/_/g, " "))}</strong>
  </span>`;
}

function setStatus(patch) {
  Object.assign(state, patch);
  for (const key of Object.keys(STATUS_LABELS)) {
    const pill = document.querySelector(`[data-status="${key}"]`);
    if (pill) pill.outerHTML = statusPill(key);
  }
  syncTutorControls();
}

function syncTutorControls() {
  const share = document.querySelector("#share-screen");
  if (!share) return;

  const sharing = state.screen === "sharing" || state.screen === "selecting";
  const inSession = !["idle", "ended", "error"].includes(state.session);
  share.disabled = sharing;
  document.querySelector("#pause-screen").disabled = !sharing;
  document.querySelector("#end-session").disabled = !inSession;
  document.querySelector("#mic-toggle").setAttribute("aria-pressed", String(state.mic === "on"));
}

function shell(content, { section = "classes" } = {}) {
  app.innerHTML = `
    <header class="app-header">
      <a class="brand" href="#/classes">
        <span class="brand-mark">${icon("monitor", { size: 18 })}</span>
        Screen Tutor
      </a>
      <nav class="primary-nav" aria-label="Primary">
        <a href="#/classes" ${section === "classes" ? 'aria-current="page"' : ""}>Classes</a>
        <a href="#/tutor" ${section === "tutor" ? 'aria-current="page"' : ""}>Quick tutor</a>
      </nav>
      <div class="status-strip" role="status" aria-live="polite" aria-label="Session status">
        ${Object.keys(STATUS_LABELS).map(statusPill).join("")}
      </div>
    </header>
    <main>${content}</main>
  `;
}

function renderClasses() {
  const classes = getClasses();
  const totalUploads = classes.reduce((sum, klass) => sum + klass.uploads.length, 0);
  const totalMaterials = classes.reduce((sum, klass) => sum + klass.materials.length, 0);
  shell(`
    <section>
      <div class="page-heading">
        <div>
          <h1>Your classes</h1>
          <p class="muted">Upload materials to a class, then study or get tutoring from them.</p>
        </div>
        <div class="page-actions">
          <button id="restore-sample-data" class="btn btn-secondary" type="button">${icon("sparkles", { size: 18 })} Restore sample data</button>
          <button id="new-class" class="btn btn-primary" type="button">${icon("plus", { size: 18 })} New class</button>
        </div>
      </div>
      <div class="metric-strip" aria-label="Workspace summary">
        <div class="metric"><strong>${classes.length}</strong><span>Classes</span></div>
        <div class="metric"><strong>${totalUploads}</strong><span>Uploads</span></div>
        <div class="metric"><strong>${totalMaterials}</strong><span>Study sets</span></div>
      </div>
      <div class="class-grid" id="class-grid"></div>
    </section>
  `);

  const grid = document.querySelector("#class-grid");
  grid.innerHTML = classes.map((klass) => `
    <article class="class-card" style="--class-color: ${escapeHtml(klass.color)}">
      <a href="#/classes/${encodeURIComponent(klass.id)}/library" class="class-card-main">
        <span class="class-icon" aria-hidden="true">${escapeHtml(klass.icon)}</span>
        <span class="class-name">${escapeHtml(klass.name)}</span>
        <span class="class-focus">${escapeHtml(klass.focus || "Study workspace")}</span>
        <span class="class-meta">
          <span>${klass.uploads.length} ${klass.uploads.length === 1 ? "upload" : "uploads"}</span>
          <span>${klass.materials.length} study ${klass.materials.length === 1 ? "set" : "sets"}</span>
        </span>
        ${klass.nextExam ? `<span class="class-next">${icon("syllabus", { size: 15 })}${escapeHtml(klass.nextExam)}</span>` : ""}
      </a>
      <div class="card-actions">
        <button class="btn btn-ghost" type="button" data-rename="${escapeHtml(klass.id)}">${icon("pencil", { size: 16 })} Rename</button>
        <button class="btn btn-danger-ghost" type="button" data-delete="${escapeHtml(klass.id)}">${icon("trash", { size: 16 })} Delete</button>
      </div>
    </article>
  `).join("") + `
    <button class="new-class-card" type="button" data-new-class>
      ${icon("plus", { size: 28 })}
      Add a class
    </button>
  `;

  async function addClass() {
    const name = await openDialog({ title: "New class", input: { label: "Class name" }, confirmLabel: "Create class" });
    if (!name) return;
    const nextClass = createClass(name);
    window.location.hash = `#/classes/${encodeURIComponent(nextClass.id)}/library`;
  }

  document.querySelector("#new-class").addEventListener("click", addClass);
  document.querySelector("#restore-sample-data").addEventListener("click", async () => {
    const confirmed = await openDialog({
      title: "Restore sample data?",
      message: "Restore the built-in sample content. Your classes and uploaded files will stay in place.",
      confirmLabel: "Restore"
    });
    if (!confirmed) return;
    resetSampleData();
    toast("Sample class data restored.");
    renderClasses();
  });

  grid.addEventListener("click", async (event) => {
    if (event.target.closest("[data-new-class]")) {
      addClass();
      return;
    }

    const renameId = event.target.closest("[data-rename]")?.dataset.rename;
    const deleteId = event.target.closest("[data-delete]")?.dataset.delete;

    if (renameId) {
      const klass = getClassById(renameId);
      const name = await openDialog({ title: "Rename class", input: { label: "Class name", value: klass.name } });
      if (name) {
        updateClass(renameId, { name, icon: name.charAt(0).toUpperCase() });
        renderClasses();
      }
    }

    if (deleteId) {
      const klass = getClassById(deleteId);
      const confirmed = await openDialog({
        title: `Delete ${klass.name}?`,
        message: "This removes the class, its uploads, and its study materials. This can't be undone.",
        confirmLabel: "Delete class",
        danger: true
      });
      if (confirmed) {
        await Promise.allSettled([deleteUploadedFiles(klass.uploads.map((upload) => upload.id))]);
        deleteClass(deleteId);
        toast(`Deleted ${klass.name}.`);
        renderClasses();
      }
    }
  });
}

const CLASS_TABS = [
  { id: "library", label: "Library", icon: "book" },
  { id: "study", label: "Study", icon: "sparkles" },
  { id: "tutor", label: "Tutor", icon: "monitor" }
];

function renderClassTab({ id, tab }) {
  const klass = getClassById(id);
  if (!klass) {
    window.location.hash = "#/classes";
    return;
  }

  shell(`
    <section>
      <a class="back-link" href="#/classes">${icon("arrowLeft", { size: 16 })} All classes</a>
      <div class="class-header">
        <div class="class-title" style="--class-color: ${escapeHtml(klass.color)}">
          <span class="class-icon" aria-hidden="true">${escapeHtml(klass.icon)}</span>
          <div>
            <h1>${escapeHtml(klass.name)}</h1>
            <p class="muted">${escapeHtml(klass.focus || "Class study workspace")}</p>
          </div>
        </div>
        <nav class="tabs" aria-label="${escapeHtml(klass.name)} sections">
          ${CLASS_TABS.map((item) => `
            <a href="#/classes/${encodeURIComponent(klass.id)}/${item.id}" ${tab === item.id ? 'aria-current="page"' : ""}>
              ${icon(item.icon, { size: 18 })} ${item.label}
            </a>
          `).join("")}
        </nav>
      </div>
      <div id="class-tab"></div>
    </section>
  `);

  const root = document.querySelector("#class-tab");

  if (tab === "study") {
    renderStudyTab({
      root,
      klass,
      onMaterialAdded: (material) => {
        updateClass(klass.id, { materials: [...klass.materials, material] });
        renderClassTab({ id, tab });
      },
      onAttemptCompleted: (materialId, attempt) => {
        const currentClass = getClassById(klass.id);
        if (!currentClass) return;
        const materials = currentClass.materials.map((material) => material.id === materialId
          ? { ...material, attempts: [...(material.attempts || []), attempt] }
          : material);
        updateClass(klass.id, { materials });
      }
    });
    return;
  }

  if (tab === "tutor") {
    renderTutor(klass);
    return;
  }

  renderLibraryTab({
    root,
    klass,
    onUploadsAdded: (uploads) => {
      updateClass(klass.id, { uploads: [...klass.uploads, ...uploads] });
      renderClassTab({ id, tab });
    },
    onUploadRemoved: (uploadId) => {
      deleteUploadedFile(uploadId).catch(() => toast("Could not remove the cached file from this browser.", { tone: "error" }));
      updateClass(klass.id, { uploads: klass.uploads.filter((upload) => upload.id !== uploadId) });
      renderClassTab({ id, tab });
    }
  });
}

function appendMessage(role, text) {
  state.messages.push({ role, text });
  const list = document.querySelector("#conversation-list");
  if (!list) return;

  const item = document.createElement("li");
  item.className = role === "student" ? "message student" : "message tutor";
  item.textContent = text;
  list.append(item);
  list.scrollTop = list.scrollHeight;
}

function renderTutor(klass = null) {
  const container = klass ? document.querySelector("#class-tab") : null;
  const content = `
    <section class="tutor-workspace">
      <div class="panel stage-panel">
        <div class="stage-topline">
          <span class="pill no-dot">${icon("book", { size: 14 })} Using <strong>${klass ? escapeHtml(klass.name) : "no class"}</strong></span>
          <div class="stage-controls">
            <button id="share-screen" class="btn btn-primary" type="button">${icon("monitor", { size: 18 })}<span class="btn-label">Share screen</span></button>
            <button id="pause-screen" class="btn btn-secondary" type="button" disabled aria-label="Pause screen sharing">${icon("pause", { size: 18 })}<span class="btn-label">Pause</span></button>
            <button id="end-session" class="btn btn-danger-ghost" type="button" disabled aria-label="End session">${icon("stop", { size: 18 })}<span class="btn-label">End</span></button>
          </div>
        </div>
        <div class="screen-stage">
          <video id="screen-preview" autoplay muted playsinline aria-label="Shared screen preview"></video>
          <svg id="annotation-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg>
          <div class="empty-stage">
            <span class="empty-icon">${icon("monitor", { size: 30 })}</span>
            <strong>Share a screen to start</strong>
            <span>Pick a window, tab, or display. The tutor sees only what you share, and you can pause anytime.</span>
          </div>
        </div>
        <div class="annotation-bar">
          <ul id="annotation-list" aria-label="Marks on screen"></ul>
          <button id="clear-marks" class="btn btn-ghost" type="button">${icon("eraser", { size: 18 })} Clear marks</button>
        </div>
      </div>
      <aside class="panel conversation-panel" aria-label="Tutor conversation">
        <div class="conversation-head">
          <h2>Conversation</h2>
        </div>
        <ol id="conversation-list" class="conversation-list" aria-live="polite"></ol>
        <form id="composer" class="composer">
          <label class="visually-hidden" for="question-input">Ask the tutor a question</label>
          <input id="question-input" name="question" autocomplete="off" placeholder="Ask a question…" />
          <button type="button" id="mic-toggle" class="btn btn-secondary btn-icon" aria-pressed="false" aria-label="Microphone">${icon("mic", { size: 20 })}</button>
          <button type="submit" class="btn btn-primary btn-icon" aria-label="Send">${icon("send", { size: 20 })}</button>
        </form>
      </aside>
    </section>
  `;

  if (container) {
    container.innerHTML = content;
  } else {
    shell(`
      <div class="page-heading">
        <div>
          <h1>Quick tutor</h1>
          <p class="muted">Get help with anything on your screen. Open a class to use its materials too.</p>
        </div>
      </div>
      ${content}
    `, { section: "tutor" });
  }

  syncTutorControls();

  const video = document.querySelector("#screen-preview");
  const emptyStage = document.querySelector(".empty-stage");
  const live = new GeminiLiveController({
    onMessage: (message) => appendMessage(message.role, message.text),
    onStatusChange: (connection) => setStatus({ connection }),
    onAnnotationRequest: (args) => annotations.addMany(args?.items || args?.annotations || [])
  });
  const capture = new ScreenCaptureController({
    video,
    onEnded: () => {
      emptyStage.hidden = false;
      setStatus({ session: "screen_paused", screen: "paused" });
    },
    onFrame: (frame) => live.sendFrame(frame)
  });
  const annotations = new AnnotationController({
    layer: document.querySelector("#annotation-layer"),
    list: document.querySelector("#annotation-list"),
    getFrames: () => capture.getFrames()
  });
  const audio = new AudioController({
    onStatusChange: (mic) => setStatus({ mic })
  });

  document.querySelector("#share-screen").addEventListener("click", async () => {
    try {
      setStatus({ session: "selecting_screen", screen: "selecting" });
      await capture.start();
      emptyStage.hidden = true;
      setStatus({ session: "connecting", screen: "sharing" });
      await live.connect();
      setStatus({ session: "active" });
    } catch (error) {
      capture.stop();
      emptyStage.hidden = false;
      setStatus({ session: "error", screen: "not sharing", connection: "offline" });
      toast(error.message, { tone: "error" });
    }
  });

  document.querySelector("#pause-screen").addEventListener("click", () => {
    capture.stop();
    annotations.clear();
    emptyStage.hidden = false;
    setStatus({ session: "screen_paused", screen: "paused" });
  });

  document.querySelector("#end-session").addEventListener("click", () => {
    capture.stop();
    audio.disable();
    live.close();
    annotations.clear();
    emptyStage.hidden = false;
    setStatus({ session: "ended", screen: "not sharing", connection: "offline" });
  });

  document.querySelector("#clear-marks").addEventListener("click", () => annotations.clear());
  document.querySelector("#mic-toggle").addEventListener("click", async () => {
    try {
      await audio.toggle();
    } catch (error) {
      toast(error.message, { tone: "error" });
    }
  });

  document.querySelector("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.querySelector("#question-input");
    const text = input.value.trim();
    if (!text) return;

    appendMessage("student", text);
    live.sendText(text);
    input.value = "";
  });
}

const router = createRouter(
  [
    { path: "/classes", render: renderClasses },
    { path: "/classes/:id/:tab", render: renderClassTab },
    { path: "/tutor", render: () => renderTutor() }
  ],
  { render: renderClasses }
);

router.render();
