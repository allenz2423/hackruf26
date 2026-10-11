import "./styles.css";
import jotMascot from "./assets/jot-mascot.png";
import { AnnotationController } from "./annotations.js";
import { AudioController } from "./audio.js";
import {
  getClasses,
  getClassById,
  createClass,
  updateClass,
  deleteClass,
  resetSampleData,
  syncClassesFromApi
} from "./classes.js";
import { LiveSessionController } from "./live-session.js";
import { icon } from "./icons.js";
import { renderLibraryTab } from "./library.js";
import { createRouter } from "./router.js";
import { ScreenCaptureController } from "./screen-capture.js";
import { renderStudyTab } from "./study.js";
import { renderGuestStudy } from "./guest-study.js";
import { deleteUploadedFile, deleteUploadedFiles } from "./file-store.js";
import { escapeHtml, openDialog, toast } from "./ui.js";
import {
  getCurrentUser,
  getAccessToken,
  signInWithEmail,
  signUpWithEmail,
  signOut,
  onAuthStateChange,
  checkBackendHealth
} from "./api.js";

const app = document.querySelector("#app");

let currentUser = null;
let disposeTutor = null;

const state = {
  session: "idle",
  connection: "offline",
  screen: "not sharing",
  mic: "off",
  micDevice: "",
  messages: []
};

const TONES = {
  session: { active: "good", connecting: "busy", selecting_screen: "busy", screen_paused: "busy", reconnecting: "busy", error: "bad" },
  connection: { connected: "good", connecting: "busy", error: "bad" },
  screen: { sharing: "live", selecting: "busy", paused: "busy" },
  mic: { on: "live", requesting: "busy", muted: "busy" }
};

const STATUS_LABELS = { session: "Session", connection: "Live", screen: "Screen", mic: "Mic" };

function statusPill(key) {
  const value = state[key];
  return `<span class="pill" data-status="${key}" data-tone="${TONES[key][value] || ""}">
    <span class="status-name">${STATUS_LABELS[key]}</span><strong>${escapeHtml(value.replace(/_/g, " "))}</strong>
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
  const micToggle = document.querySelector("#mic-toggle");
  if (micToggle) {
    const micActive = ["on", "muted", "requesting"].includes(state.mic);
    micToggle.disabled = state.connection !== "connected" && !micActive;
    micToggle.setAttribute("aria-pressed", String(micActive));
    const action = state.mic === "requesting"
      ? "Cancel microphone request"
      : micActive ? "Turn microphone off" : "Turn microphone on";
    micToggle.setAttribute("aria-label", action);
    micToggle.title = action;
  }
  const micFeedback = document.querySelector("#mic-feedback");
  if (micFeedback) {
    micFeedback.dataset.mic = state.mic;
    const descriptions = {
      requesting: "Allow microphone access in your browser, or click the mic again to cancel.",
      on: "Listening. Speak your question, then pause for the tutor to reply.",
      muted: "Your microphone is temporarily muted or unavailable. Check your device.",
      off: state.connection === "connected"
        ? "Microphone off. Turn it on to ask a question aloud."
        : "Share your screen to connect, then turn on the microphone."
    };
    document.querySelector("#mic-description").textContent = descriptions[state.mic] || descriptions.off;
    document.querySelector("#mic-device").textContent = state.micDevice;
  }
  const sendButton = document.querySelector('#composer button[type="submit"]');
  if (sendButton) sendButton.disabled = state.connection !== "connected";
}

function shell(content, { section = "classes" } = {}) {
  const isGuest = !currentUser?.email;
  const userLabel = currentUser?.email ? currentUser.email.split("@")[0] : "Guest";
  const brandHref = isGuest ? "#/study" : "#/classes";
  const showSessionStatus = section === "tutor" || !["idle", "ended"].includes(state.session);

  app.innerHTML = `
    <a class="skip-link" href="#main-content">Skip to content</a>
    <header class="app-header">
      <a class="brand" href="${brandHref}">
        <span class="brand-mark" aria-hidden="true"><img src="${jotMascot}" alt="" /></span>
        Jot
      </a>
      <nav class="primary-nav" aria-label="Primary">
        ${isGuest ? `
          <a href="#/study" ${section === "study" ? 'aria-current="page"' : ""}>Quick study</a>
        ` : `
          <a href="#/classes" ${section === "classes" ? 'aria-current="page"' : ""}>Classes</a>
        `}
        <a href="#/tutor" ${section === "tutor" ? 'aria-current="page"' : ""}>Quick tutor</a>
        <a href="#/auth" ${section === "auth" ? 'aria-current="page"' : ""} class="nav-account-link">
          ${icon("user", { size: 16 })}
          ${isGuest ? `<span class="nav-account-name">Guest</span>` : `<span class="nav-account-name">${escapeHtml(userLabel)}</span><span class="tier-badge badge-cloud">SQLite</span>`}
        </a>
      </nav>
    </header>
    ${showSessionStatus ? `<div class="session-status-row">
      <div class="status-strip" role="status" aria-live="polite" aria-label="Session status">
        ${Object.keys(STATUS_LABELS).map(statusPill).join("")}
      </div>
    </div>` : ""}
    <main id="main-content" tabindex="-1">${content}</main>
  `;

  app.querySelector(".skip-link")?.addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelector("#main-content")?.focus();
  });
}

async function renderGuestWorkspace() {
  if (currentUser) {
    window.location.hash = "#/classes";
    return;
  }
  shell(`
    <div id="guest-study-mount"></div>
  `, { section: "study" });
  renderGuestStudy(document.querySelector("#guest-study-mount"));
}

async function renderClasses() {
  if (!currentUser) {
    window.location.hash = "#/study";
    return;
  }
  const classes = getClasses();
  const totalUploads = classes.reduce((sum, klass) => sum + (klass.uploads?.length || 0), 0);
  const totalMaterials = classes.reduce((sum, klass) => sum + (klass.materials?.length || 0), 0);

  shell(`
    <section>
      <div class="page-heading">
        <div>
          <h1>Your classes</h1>
          <p class="muted">Upload materials to a class, then study or get tutoring from them.</p>
          <p class="desk-summary">${classes.length} ${classes.length === 1 ? "class" : "classes"} · ${totalUploads} ${totalUploads === 1 ? "upload" : "uploads"} · ${totalMaterials} study ${totalMaterials === 1 ? "set" : "sets"}</p>
        </div>
        <div class="page-actions">
          <button id="restore-sample-data" class="btn btn-secondary" type="button">${icon("sparkles", { size: 18 })} Restore sample data</button>
          <button id="new-class" class="btn btn-primary" type="button">${icon("plus", { size: 18 })} New class</button>
        </div>
      </div>
      <div class="class-grid" id="class-grid"></div>
    </section>
  `, { section: "classes" });

  const grid = document.querySelector("#class-grid");
  grid.innerHTML = classes.map((klass) => `
    <article class="class-card" style="--class-color: ${escapeHtml(klass.color || "#3f6577")}">
      <a href="#/classes/${encodeURIComponent(klass.id)}/library" class="class-card-main">
        <span class="class-icon" aria-hidden="true">${escapeHtml(klass.icon || "C")}</span>
        <span class="class-name">${escapeHtml(klass.name)}</span>
        <span class="class-focus">${escapeHtml(klass.focus || "Study workspace")}</span>
        <span class="class-meta">
          <span>${klass.uploads?.length || 0} ${(klass.uploads?.length || 0) === 1 ? "upload" : "uploads"}</span>
          <span>${klass.materials?.length || 0} study ${(klass.materials?.length || 0) === 1 ? "set" : "sets"}</span>
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
    const nextClass = await createClass(name);
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
        await updateClass(renameId, { name, icon: name.charAt(0).toUpperCase() });
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
        await Promise.allSettled([deleteUploadedFiles((klass.uploads || []).map((u) => u.id))]);
        await deleteClass(deleteId);
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

async function renderClassTab({ id, tab }) {
  if (!currentUser) {
    window.location.hash = "#/study";
    return;
  }
  const klass = getClassById(id);
  if (!klass) {
    window.location.hash = "#/classes";
    return;
  }

  shell(`
    <section>
      <a class="back-link" href="#/classes">${icon("arrowLeft", { size: 16 })} All classes</a>
      <div class="class-header">
        <div class="class-title" style="--class-color: ${escapeHtml(klass.color || "#3f6577")}">
          <span class="class-icon" aria-hidden="true">${escapeHtml(klass.icon || "C")}</span>
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
  `, { section: tab === "tutor" ? "tutor" : "classes" });

  const root = document.querySelector("#class-tab");

  if (tab === "study") {
    await renderStudyTab({
      root,
      klass,
      onMaterialAdded: (material) => {
        const currentClass = getClassById(klass.id) || klass;
        const materials = currentClass.materials.filter((item) => item.id !== material.id);
        klass.materials = [...materials, material];
        return updateClass(klass.id, { materials: klass.materials });
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

  await renderLibraryTab({
    root,
    klass,
    onUploadsAdded: (uploads) => {
      updateClass(klass.id, { uploads: [...klass.uploads, ...uploads] });
      renderClassTab({ id, tab });
    },
    onUploadRemoved: (uploadId) => {
      deleteUploadedFile(uploadId).catch(() => toast("Could not remove the cached file from this browser.", { tone: "error" }));
      updateClass(klass.id, { uploads: klass.uploads.filter((u) => u.id !== uploadId) });
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
  disposeTutor?.();
  const container = klass ? document.querySelector("#class-tab") : null;
  const content = `
    <section class="tutor-workspace">
      <div class="panel stage-panel">
        <div class="stage-topline">
          <span class="pill no-dot">${icon("book", { size: 14 })} Using <strong>${klass ? escapeHtml(klass.name) : "no class"}</strong></span>
          <div class="stage-controls">
            <button id="share-screen" class="btn btn-primary" type="button" aria-label="Share screen" title="Share screen">${icon("monitor", { size: 18 })}<span class="btn-label">Share screen</span></button>
            <button id="pause-screen" class="btn btn-secondary" type="button" disabled aria-label="Pause screen sharing" title="Pause screen sharing">${icon("pause", { size: 18 })}<span class="btn-label">Pause</span></button>
            <button id="end-session" class="btn btn-danger-ghost" type="button" disabled aria-label="End session" title="End session">${icon("stop", { size: 18 })}<span class="btn-label">End</span></button>
          </div>
        </div>
        <p class="tutor-privacy-note">${icon("monitor", { size: 16 })}<span>Screen frames and enabled microphone audio pass through Jot's server for tutoring. They are not saved.</span></p>
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
        <ol id="conversation-list" class="conversation-list" data-empty-prompt="${escapeHtml(klass ? `Ask about your shared screen or ${klass.name}'s course materials.` : "Share your screen or ask a general question to get started.")}" aria-live="polite"></ol>
        <div id="mic-feedback" class="mic-feedback" data-mic="off">
          <div class="mic-feedback-text" role="status" aria-live="polite">
            <span id="mic-description"></span>
            <span id="mic-device" class="mic-device"></span>
          </div>
          <div class="mic-meter" aria-hidden="true"><span id="mic-level"></span></div>
        </div>
        <form id="composer" class="composer">
          <label class="visually-hidden" for="question-input">Ask the tutor a question</label>
          <input id="question-input" name="question" autocomplete="off" placeholder="Ask a question…" />
          <button type="button" id="mic-toggle" class="btn btn-secondary btn-icon" aria-pressed="false" aria-label="Turn microphone on" aria-describedby="mic-description" title="Turn microphone on">${icon("mic", { size: 20 })}</button>
          <button type="submit" class="btn btn-primary btn-icon" aria-label="Send" title="Send question">${icon("send", { size: 20 })}</button>
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

  // Check if a question was queued from quiz player ("Ask tutor about this")
  const queuedQuestion = sessionStorage.getItem("tutor_initial_question");
  if (queuedQuestion) {
    const questionInput = document.querySelector("#question-input");
    if (questionInput) {
      questionInput.value = queuedQuestion;
      sessionStorage.removeItem("tutor_initial_question");
    }
  }

  const video = document.querySelector("#screen-preview");
  const emptyStage = document.querySelector(".empty-stage");
  const micLevel = document.querySelector("#mic-level");
  let disposed = false;
  let sessionRequestId = 0;
  let audioController = null;
  const live = new LiveSessionController({
    classId: klass?.id || null,
    onMessage: (message) => appendMessage(message.role, message.text),
    onStatusChange: (connection, errorMessage) => {
      if (disposed) return;
      setStatus({ connection });
      if (connection === "error") {
        const wasActive = ["active", "screen_paused"].includes(state.session);
        if (wasActive) sessionRequestId += 1;
        capture.stop();
        void audioController?.close().catch(() => {});
        annotations.clear();
        emptyStage.hidden = false;
        setStatus({ session: "error", screen: "not sharing" });
        if (wasActive) {
          toast(errorMessage || "The tutoring session disconnected. Start it again to continue.", { tone: "error" });
        }
      }
    },
    onAudioData: (data, mimeType) => audioController?.playPcm(data, mimeType),
    onAudioInterrupted: () => audioController?.stopPlayback(),
    onAnnotationRequest: (payload) => {
      const frameId = payload?.frame_id;
      const items = (payload?.items || []).map((item) => ({ ...item, frame_id: frameId }));
      return annotations.addMany(items);
    }
  });
  const capture = new ScreenCaptureController({
    video,
    onEnded: () => {
      if (disposed) return;
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
  audioController = new AudioController({
    onStatusChange: (mic, deviceLabel = "") => {
      if (disposed) return;
      const wasSendingAudio = state.mic === "on";
      setStatus({ mic, micDevice: deviceLabel });
      if (wasSendingAudio && ["off", "muted"].includes(mic)) live.endAudioInput();
    },
    onAudioChunk: (chunk) => {
      if (!live.sendAudio(chunk)) audioController.disable();
    },
    onAudioLevel: (level) => {
      if (!disposed) micLevel.style.transform = `scaleX(${level})`;
    },
    onError: (error) => {
      if (!disposed) toast(error.message, { tone: "error" });
    }
  });

  function stopSession() {
    sessionRequestId += 1;
    capture.stop();
    void audioController.close().catch(() => {});
    live.close();
    annotations.clear();
    emptyStage.hidden = false;
  }

  disposeTutor = () => {
    disposed = true;
    stopSession();
    Object.assign(state, {
      session: "idle", connection: "offline", screen: "not sharing",
      mic: "off", micDevice: "", messages: []
    });
    disposeTutor = null;
  };

  document.querySelector("#share-screen").addEventListener("click", async () => {
    const requestId = ++sessionRequestId;
    try {
      setStatus({ session: "selecting_screen", screen: "selecting" });
      const captureRequest = capture.start();
      const playbackReady = audioController.preparePlayback();
      const [stream] = await Promise.all([captureRequest, playbackReady]);
      if (disposed || requestId !== sessionRequestId || !stream) return;
      emptyStage.hidden = true;
      setStatus({ session: "connecting", screen: "sharing" });
      if (state.connection !== "connected") await live.connect();
      if (disposed || requestId !== sessionRequestId) return;
      setStatus({ session: "active" });
    } catch (error) {
      if (disposed || requestId !== sessionRequestId) return;
      stopSession();
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
    stopSession();
    setStatus({ session: "ended", screen: "not sharing", connection: "offline" });
  });

  document.querySelector("#clear-marks").addEventListener("click", () => annotations.clear());
  document.querySelector("#mic-toggle")?.addEventListener("click", async () => {
    try {
      if (state.connection !== "connected" && !audioController.enabled && !audioController.requesting) return;
      await audioController.toggle();
    } catch (error) {
      if (!disposed) toast(error.message, { tone: "error" });
    }
  });

  document.querySelector("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.querySelector("#question-input");
    const text = input.value.trim();
    if (!text) return;

    try {
      if (!live.sendText(text)) return;
      appendMessage("student", text);
      input.value = "";
    } catch (error) {
      toast(error.message || "The question could not be sent.", { tone: "error" });
    }
  });
}

async function renderAuth() {
  const user = await getCurrentUser();
  const health = await checkBackendHealth();
  const backendReady = health.status === "ok";

  shell(`
    <section class="account-page section-stack">
      <div class="page-heading account-heading">
        <div>
          <h1>Account</h1>
          <p class="muted">Save classes, uploads, and study history across browser sessions.</p>
        </div>
      </div>

      ${user ? `
        <div class="account-status-banner signed-in-banner">
          ${icon("check", { size: 20 })}
          <div>
            <strong>Signed in as ${escapeHtml(user.email)} · SQLite Database Sync Active</strong>
            <span>Your classes, uploaded course documents, syllabus roadmap, and generated study sets are automatically saved to the backend SQLite database (<code>screen_tutor.sqlite3</code>).</span>
          </div>
        </div>
      ` : `
        <div class="account-status-banner guest-banner">
          ${icon("alert", { size: 20 })}
          <div>
            <strong>Guest Mode Active · Files Are Not Saved to Database</strong>
            <span>You are currently using Jot as a guest. Any uploaded notes, slides, or study sets are stored <strong>only in this browser's temporary storage</strong> and will not be saved to the SQLite database. Sign in or create an account to save your files permanently to the database.</span>
          </div>
        </div>
      `}

      <div class="auth-comparison-grid">
        <div class="auth-tier-card tier-guest">
          <div class="auth-tier-header">
            <h3>Guest Mode</h3>
            <span class="tier-badge badge-guest">Local Only</span>
          </div>
          <p class="auth-tier-desc">Quick, zero-setup study sessions without an account. Best for exploring features.</p>
          <ul class="auth-tier-features">
            <li><span class="feature-check">✓</span> Live voice and screen tutoring</li>
            <li><span class="feature-check">✓</span> Interactive flashcards and quiz players</li>
            <li><span class="feature-check">✓</span> Built-in scratchpad & scientific calculator</li>
            <li><span class="feature-check">✓</span> Sample study sets and quick review</li>
            <li><span class="feature-warn">✕</span> <strong>Files NOT saved to database</strong></li>
            <li><span class="feature-warn">✕</span> <strong>Lost on browser cache clear or reload</strong></li>
            <li><span class="feature-warn">✕</span> No persistent multi-class library</li>
          </ul>
          <a href="#/study" class="btn btn-secondary" style="justify-content: center; width: 100%;">
            Continue as Guest
          </a>
        </div>

        <div class="auth-tier-card tier-cloud">
          <div class="auth-tier-header">
            <h3>Signed-in Account</h3>
            <span class="tier-badge badge-cloud">SQLite Saved</span>
          </div>
          <p class="auth-tier-desc">Full persistence powered by backend SQLite database (<code>screen_tutor.sqlite3</code>) and local private file storage.</p>
          <ul class="auth-tier-features">
            <li><span class="feature-check">✓</span> <strong>Permanent SQLite database storage</strong> for all course files</li>
            <li><span class="feature-check">✓</span> Sync classes & syllabus maps automatically via FastAPI</li>
            <li><span class="feature-check">✓</span> Background lecture transcription & extraction</li>
            <li><span class="feature-check">✓</span> Study sets, practice tests & score history preserved in SQLite</li>
            <li><span class="feature-check">✓</span> Voice and screen tutoring grounded in course files</li>
            <li><span class="feature-check">✓</span> High performance local WAL-mode storage</li>
          </ul>
          ${user ? `
            <button id="auth-signout-btn" class="btn btn-danger-ghost" type="button" style="justify-content: center; width: 100%;">
              ${icon("stop", { size: 16 })} Sign out (${escapeHtml(user.email.split("@")[0])})
            </button>
          ` : `
            <button id="account-focus-auth" class="btn btn-primary account-jump-action" type="button">
              Sign in or create account
            </button>
          `}
        </div>
      </div>

      <div class="panel overview-card" style="margin-bottom: 1.5rem;">
        <span class="kind-icon">${icon("monitor", { size: 22 })}</span>
        <div>
          <strong>Backend & SQLite Database: <span class="pill" data-tone="${backendReady ? 'good' : 'busy'}">${backendReady ? 'Connected (SQLite)' : health.status}</span></strong>
            <span class="row-meta">FastAPI server connected to SQLite (<code>screen_tutor.sqlite3</code>), runs live tutoring sessions, and processes course materials.</span>
        </div>
      </div>

      ${user ? `
        <div class="panel">
          <h2>Active Database Session</h2>
          <p class="muted">Your classes and study sets are synced to the backend SQLite database.</p>
          <div class="metric-strip" style="margin: 1.25rem 0;">
            <div class="metric">
              <strong>${escapeHtml(user.email)}</strong>
              <span>User ID: ${escapeHtml(user.id.slice(0, 8))}...</span>
            </div>
            <div class="metric">
              <strong>SQLite Database</strong>
              <span>Persistence active · Local WAL storage</span>
            </div>
          </div>
          <div style="display: flex; gap: 0.75rem;">
            <a href="#/classes" class="btn btn-primary">
              Go to my classes
            </a>
            <button id="auth-signout-btn-2" class="btn btn-secondary" type="button">
              ${icon("stop", { size: 16 })} Sign out
            </button>
          </div>
        </div>
      ` : `
        <div class="panel auth-panel">
          <div class="tabs" style="margin-bottom: 1.5rem; justify-content: center; display: flex; gap: 0.5rem;">
            <button id="auth-tab-signin" class="btn btn-secondary" type="button" aria-pressed="true">Sign in</button>
            <button id="auth-tab-signup" class="btn btn-ghost" type="button" aria-pressed="false">Create account</button>
          </div>

          <form id="auth-form" class="section-stack" action="" novalidate>
            <div class="field-block" id="auth-name-block" hidden>
              <label for="auth-name">Full name or nickname</label>
              <input id="auth-name" name="name" type="text" autocomplete="name" placeholder="Ada Lovelace" />
            </div>
            <div class="field-block">
              <label for="auth-email">Email address</label>
              <input id="auth-email" name="email" type="email" required autocomplete="email" placeholder="student@example.com" />
            </div>
            <div class="field-block">
              <label for="auth-password">Password</label>
              <input id="auth-password" name="password" type="password" required minlength="6" autocomplete="current-password" placeholder="At least 6 characters" />
            </div>
            <button id="auth-submit-btn" class="btn btn-primary" type="submit" style="width: 100%; justify-content: center; margin-top: 1rem;">
              Sign in
            </button>
          </form>
          <p class="field-hint" style="text-align: center; margin-top: 1.25rem;">
            Local workspace mode · Database storage powered by backend SQLite (<code>screen_tutor.sqlite3</code>)
          </p>
        </div>
      `}
    </section>
  `, { section: "auth" });

  if (user) {
    const handleSignOut = async () => {
      try {
        await signOut();
        currentUser = null;
        toast("Signed out successfully.");
        renderAuth();
      } catch (err) {
        toast(`Sign out failed: ${err.message}`, { tone: "error" });
      }
    };
    document.querySelector("#auth-signout-btn")?.addEventListener("click", handleSignOut);
    document.querySelector("#auth-signout-btn-2")?.addEventListener("click", handleSignOut);
  } else {
    let mode = "signin";
    const tabSignin = document.querySelector("#auth-tab-signin");
    const tabSignup = document.querySelector("#auth-tab-signup");
    const nameBlock = document.querySelector("#auth-name-block");
    const submitBtn = document.querySelector("#auth-submit-btn");
    const form = document.querySelector("#auth-form");
    document.querySelector("#account-focus-auth")?.addEventListener("click", () => {
      form.scrollIntoView({ block: "center", behavior: "smooth" });
      document.querySelector("#auth-email")?.focus();
    });

    tabSignin.addEventListener("click", () => {
      mode = "signin";
      tabSignin.className = "btn btn-secondary";
      tabSignup.className = "btn btn-ghost";
      tabSignin.setAttribute("aria-pressed", "true");
      tabSignup.setAttribute("aria-pressed", "false");
      nameBlock.hidden = true;
      submitBtn.textContent = "Sign in";
      document.querySelector("#auth-password").autocomplete = "current-password";
    });

    tabSignup.addEventListener("click", () => {
      mode = "signup";
      tabSignup.className = "btn btn-secondary";
      tabSignin.className = "btn btn-ghost";
      tabSignup.setAttribute("aria-pressed", "true");
      tabSignin.setAttribute("aria-pressed", "false");
      nameBlock.hidden = false;
      submitBtn.textContent = "Create account";
      document.querySelector("#auth-password").autocomplete = "new-password";
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!form.reportValidity()) return;

      const email = document.querySelector("#auth-email").value.trim();
      const password = document.querySelector("#auth-password").value;
      const name = document.querySelector("#auth-name")?.value.trim() || "";

      submitBtn.disabled = true;
      submitBtn.textContent = "Connecting...";

      try {
        if (mode === "signin") {
          await signInWithEmail(email, password);
          toast("Signed in successfully!");
        } else {
          await signUpWithEmail(email, password, name);
          toast("Account created and signed in!");
        }
        currentUser = await getCurrentUser();
        await syncClassesFromApi();
        window.location.hash = "#/classes";
      } catch (err) {
        toast(err.message, { tone: "error" });
        submitBtn.disabled = false;
        submitBtn.textContent = mode === "signin" ? "Sign in" : "Create account";
      }
    });
  }
}

const router = createRouter(
  [
    { path: "/study", render: renderGuestWorkspace },
    { path: "/guest", render: renderGuestWorkspace },
    { path: "/classes", render: renderClasses },
    { path: "/classes/:id/:tab", render: renderClassTab },
    { path: "/tutor", render: () => renderTutor() },
    { path: "/auth", render: renderAuth }
  ],
  {
    render: () => {
      if (currentUser) {
        renderClasses();
      } else {
        renderGuestWorkspace();
      }
    }
  },
  { beforeRender: () => disposeTutor?.() }
);

window.addEventListener("pagehide", () => disposeTutor?.());
window.addEventListener("pageshow", (event) => {
  if (event.persisted) router.render();
});

// Initialize authentication and startup sync
(async () => {
  try {
    currentUser = await getCurrentUser();
    if (currentUser) {
      await syncClassesFromApi();
    }
  } catch (err) {
    console.warn("Initial auth/sync check:", err);
  }

  onAuthStateChange(async (event, session) => {
    const wasGuest = !currentUser;
    currentUser = session?.user || null;
    if (currentUser) {
      await syncClassesFromApi();
      if (wasGuest && (window.location.hash.includes("/study") || window.location.hash.includes("/guest"))) {
        window.location.hash = "#/classes";
      }
    }
    router.render();
  });

  if (!currentUser && (!window.location.hash || window.location.hash === "#" || window.location.hash === "#/classes")) {
    window.location.hash = "#/study";
  } else {
    router.render();
  }
})();
