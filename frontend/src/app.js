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
  syncClassesFromApi,
  claimLocalClassData,
  clearLocalClassData,
  hasLocalClassData
} from "./classes.js";
import { LiveSessionController } from "./live-session.js";
import { getBackendClassId, prepareTutorClass } from "./course-sync.js";
import { icon } from "./icons.js";
import { renderLibraryTab } from "./library.js";
import { createRouter } from "./router.js";
import { ScreenCaptureController } from "./screen-capture.js";
import { renderStudyTab } from "./study.js";
import { renderGuestStudy } from "./guest-study.js";
import { clearUploadedFiles, deleteUploadedFile, deleteUploadedFiles } from "./file-store.js";
import { escapeHtml, openDialog, toast } from "./ui.js";
import {
  getCurrentUser,
  signIn,
  signUp,
  signOut,
  onAuthStateChange
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
  tutorMode: "conversation",
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
  share.disabled = sharing || ["connecting", "reconnecting"].includes(state.session);
  document.querySelector("#pause-screen").disabled = !sharing;
  document.querySelector("#end-session").disabled = !inSession;
  const resetMemory = document.querySelector("#reset-tutor-memory");
  if (resetMemory) {
    resetMemory.disabled = state.connection !== "connected" || !["active", "screen_paused"].includes(state.session);
  }
  const coachingToggle = document.querySelector("#check-steps");
  if (coachingToggle) {
    const coaching = state.tutorMode === "check_steps";
    const modeLocked = inSession || ["connecting", "connected"].includes(state.connection);
    coachingToggle.checked = coaching;
    coachingToggle.disabled = modeLocked;
    document.querySelector("#coaching-description").textContent = coaching
      ? `Explain one step at a time and pause for a short corrective hint. Feedback can miss mistakes.${modeLocked ? " End the session to change modes." : ""}`
      : modeLocked
        ? "Conversation mode is active. End the session to enable Check my steps."
        : "Enable before sharing for corrective hints at pauses as you explain your solution.";
  }
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
      on: state.tutorMode === "check_steps"
        ? "Listening to your steps. Pause for feedback; speaking again interrupts the tutor."
        : "Listening. Speak your question, then pause for the tutor to reply.",
      muted: "Your microphone is temporarily muted or unavailable. Check your device.",
      off: state.connection === "connected"
        ? state.tutorMode === "check_steps"
          ? "Microphone off. Turn it on to walk through your solution."
          : "Microphone off. Turn it on to ask a question aloud."
        : "Share your screen to connect, then turn on the microphone."
    };
    document.querySelector("#mic-description").textContent = descriptions[state.mic] || descriptions.off;
    document.querySelector("#mic-device").textContent = state.micDevice;
  }
  const sendButton = document.querySelector('#composer button[type="submit"]');
  if (sendButton) sendButton.disabled = state.connection !== "connected";
}

const displayName = (user) => user?.display_name || user?.username || "";

// Make the browser reflect exactly who is logged in. Anything cached for an
// account is removed the moment that account is no longer the one signed in.
async function applyAuth(user) {
  currentUser = user;
  if (user) {
    claimLocalClassData(user.id);
    await syncClassesFromApi();
  } else if (hasLocalClassData()) {
    clearLocalClassData();
    // Files in browser storage are cleaned up too, but never wait on it for long.
    await Promise.race([
      clearUploadedFiles().catch(() => {}),
      new Promise((resolve) => setTimeout(resolve, 1500))
    ]);
  }
}

function redirect(hash, message) {
  if (message) toast(message);
  if (window.location.hash === hash) {
    router.render();
  } else {
    window.location.hash = hash;
  }
}

async function logOut() {
  try {
    await signOut();
  } catch (error) {
    toast(error.message || "Could not log out. Please try again.", { tone: "error" });
    return;
  }
  // The account is gone from the page right away; cleanup of browser-stored
  // files continues in the background and can never keep you on a signed-in page.
  const cleanup = applyAuth(null);
  toast("You're logged out.");
  redirect("#/home");
  await cleanup;
}

function accountMenuMarkup() {
  const chevron = icon("chevronDown", { size: 16 });

  if (!currentUser) {
    return `
      <div class="account-menu" data-account-menu>
        <button class="btn btn-secondary account-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu-list">
          Join Jot ${chevron}
        </button>
        <div class="account-menu-list" id="account-menu-list" role="menu" aria-label="Join Jot" hidden>
          <a role="menuitem" href="#/login">${icon("user", { size: 16 })} Log in</a>
          <a role="menuitem" href="#/signup">${icon("plus", { size: 16 })} Create account</a>
        </div>
      </div>
    `;
  }

  const name = displayName(currentUser);
  return `
    <div class="account-menu" data-account-menu>
      <button class="btn btn-secondary account-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu-list">
        <span class="account-initial" aria-hidden="true">${escapeHtml(name.charAt(0).toUpperCase())}</span>
        <span class="account-name">${escapeHtml(name)}</span>
        ${chevron}
      </button>
      <div class="account-menu-list" id="account-menu-list" role="menu" aria-label="Account" hidden>
        <a role="menuitem" href="#/account">${icon("user", { size: 16 })} Account</a>
        <a role="menuitem" href="#/classes">${icon("book", { size: 16 })} Your classes</a>
        <button role="menuitem" type="button" data-logout>${icon("logOut", { size: 16 })} Log out</button>
      </div>
    </div>
  `;
}

let accountMenuAbort = null;

function wireAccountMenu() {
  accountMenuAbort?.abort();
  const menu = document.querySelector("[data-account-menu]");
  if (!menu) return;

  accountMenuAbort = new AbortController();
  const { signal } = accountMenuAbort;
  const trigger = menu.querySelector(".account-trigger");
  const list = menu.querySelector(".account-menu-list");
  const items = () => [...list.querySelectorAll('[role="menuitem"]')];

  const open = (focusFirst) => {
    list.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusFirst) items()[0]?.focus();
  };
  const close = ({ restoreFocus = false } = {}) => {
    list.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  };

  trigger.addEventListener("click", () => (list.hidden ? open(false) : close()));
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      open(true);
    }
  });

  menu.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    const all = items();
    const index = all.indexOf(document.activeElement);

    if (event.key === "Escape") {
      event.stopPropagation();
      close({ restoreFocus: true });
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      all[(index + 1) % all.length].focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      all[(index - 1 + all.length) % all.length].focus();
    } else if (event.key === "Tab") {
      close();
    }
  });

  list.addEventListener("click", (event) => {
    const target = event.target.closest("a, button");
    if (!target) return;
    close();
    if (target.matches("[data-logout]")) logOut();
  });

  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) close();
  }, { signal });
}

function shell(content, { section = "classes" } = {}) {
  const isGuest = !currentUser;
  const brandHref = "#/home";
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
      </nav>
      ${accountMenuMarkup()}
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

  wireAccountMenu();
}

function renderHome() {
  const name = displayName(currentUser);

  const copy = currentUser
    ? `<p class="home-greeting">Welcome back, ${escapeHtml(name)}! Pick up where you left off, or open a class to study or get tutoring from its materials.</p>
        <div class="home-actions">
          <a class="btn btn-primary btn-large" href="#/classes">Go to your classes</a>
          <a class="home-guest" href="#/account">Account</a>
        </div>`
    : `<p class="home-greeting">Welcome! Share your screen and ask me anything out loud, or drop in your notes and lectures and I'll turn them into quizzes, flashcards, and study guides.</p>
        <div class="home-actions">
          <a class="btn btn-primary btn-large" href="#/login">Log in</a>
          <a class="home-guest" href="#/study">Continue as guest</a>
          <p class="home-note">Guests can try everything, but nothing is saved.</p>
        </div>`;

  app.innerHTML = `
    <main id="main-content" class="home" tabindex="-1">
      <div class="home-inner">
        <div class="home-logo">
          <span class="home-mascot" aria-hidden="true"><img src="${jotMascot}" alt="" /></span>
          <h1>Jot</h1>
        </div>
        ${copy}
      </div>
    </main>
  `;
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
        try {
          await updateClass(renameId, { name, icon: name.charAt(0).toUpperCase() });
          renderClasses();
        } catch (error) {
          toast(`Could not rename this class: ${error.message}`, { tone: "error" });
        }
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
        try {
          await deleteClass(deleteId);
          await Promise.allSettled([deleteUploadedFiles((klass.uploads || []).map((u) => u.id))]);
          toast(`Deleted ${klass.name}.`);
          renderClasses();
        } catch (error) {
          toast(`Could not delete this class: ${error.message}`, { tone: "error" });
        }
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
          <svg id="annotation-layer" preserveAspectRatio="xMidYMid meet" aria-hidden="true"></svg>
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
          <div class="conversation-title-row">
            <h2>Conversation</h2>
            <button id="reset-tutor-memory" class="btn btn-ghost" type="button" disabled aria-label="Reset tutor memory" aria-describedby="reset-memory-description">${icon("refresh", { size: 16 })} Reset memory</button>
          </div>
          <p id="reset-memory-description" class="tutor-memory-hint">Clear the conversation and marks if the tutor refers to old work. Your screen stays shared; the microphone turns off.</p>
          <div class="coaching-controls">
            <label class="check-steps-control" for="check-steps">
              <input id="check-steps" type="checkbox" aria-describedby="coaching-description" />
              <span>Check my steps</span>
            </label>
            <p id="coaching-description" role="status" aria-live="polite"></p>
          </div>
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
  let classPreparation = null;
  const live = new LiveSessionController({
    classId: klass ? getBackendClassId(klass) : null,
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
      live.pauseScreen();
      annotations.clear();
      emptyStage.hidden = false;
      setStatus({ session: "screen_paused", screen: "paused" });
    },
    onFrame: (frame) => live.sendFrame(frame)
  });
  const annotations = new AnnotationController({
    layer: document.querySelector("#annotation-layer"),
    list: document.querySelector("#annotation-list"),
    video,
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
    classPreparation?.abort();
    classPreparation = null;
    capture.stop();
    void audioController.close().catch(() => {});
    live.close();
    annotations.clear();
    emptyStage.hidden = false;
  }

  disposeTutor = () => {
    disposed = true;
    stopSession();
    annotations.destroy();
    Object.assign(state, {
      session: "idle", connection: "offline", screen: "not sharing",
      mic: "off", micDevice: "", tutorMode: "conversation", messages: []
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
      if (state.connection !== "connected") {
        if (klass) {
          classPreparation = new AbortController();
          live.classId = await prepareTutorClass(klass, { signal: classPreparation.signal });
          if (disposed || requestId !== sessionRequestId) return;
          classPreparation = null;
        }
        await live.connect({ mode: state.tutorMode });
      }
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
    live.pauseScreen();
    annotations.clear();
    emptyStage.hidden = false;
    setStatus({ session: "screen_paused", screen: "paused" });
  });

  document.querySelector("#end-session").addEventListener("click", () => {
    stopSession();
    setStatus({ session: "ended", screen: "not sharing", connection: "offline" });
  });

  document.querySelector("#clear-marks").addEventListener("click", () => annotations.clear());
  document.querySelector("#reset-tutor-memory").addEventListener("click", async () => {
    if (state.connection !== "connected" || !["active", "screen_paused"].includes(state.session)) return;
    const requestId = ++sessionRequestId;
    // A fresh ticket creates a new provider conversation. No old messages or
    // frames are replayed, and no new capture permission is requested.
    audioController.disable();
    live.close();
    annotations.clear();
    capture.clearFrames();
    state.messages = [];
    document.querySelector("#conversation-list").replaceChildren();
    setStatus({ session: "connecting", connection: "connecting", mic: "off", micDevice: "" });
    try {
      await audioController.preparePlayback();
      if (disposed || requestId !== sessionRequestId) return;
      await live.connect({ mode: state.tutorMode });
      if (disposed || requestId !== sessionRequestId) return;
      if (capture.stream) {
        capture.sampleFrame();
        setStatus({ session: "active", screen: "sharing" });
      } else {
        live.pauseScreen();
        setStatus({ session: "screen_paused", screen: "paused" });
      }
      toast("Tutor memory reset. Turn on the microphone when you're ready to continue.");
    } catch (error) {
      if (disposed || requestId !== sessionRequestId) return;
      stopSession();
      setStatus({ session: "error", screen: "not sharing", connection: "offline" });
      toast(`Could not reset the tutor: ${error.message}`, { tone: "error" });
    }
  });
  document.querySelector("#check-steps").addEventListener("change", (event) => {
    if (event.currentTarget.disabled) return;
    setStatus({ tutorMode: event.currentTarget.checked ? "check_steps" : "conversation" });
  });
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

const USERNAME_RULE = /^[A-Za-z0-9_.-]{3,32}$/;

// Log in and Join Jot are for guests only; a logged-in visitor never reaches them.
function renderLogin({ mode = "signin" } = {}) {
  const signup = mode === "signup";

  shell(`
    <section class="login-page">
      <div class="panel login-card">
        <div class="login-heading">
          <h1>${signup ? "Join Jot" : "Log in"}</h1>
          <p class="muted">${signup ? "Create an account to keep your classes, uploads, and study sets." : "Welcome back! Log in to open your classes."}</p>
        </div>
        <form id="login-form" class="login-form" novalidate>
          <label class="field">
            <span>${signup ? "Username" : "Username or email"}</span>
            <input name="identifier" type="text" required autocomplete="username" autocapitalize="none" spellcheck="false"${signup ? ' maxlength="32" aria-describedby="login-username-hint"' : ""} />
            ${signup ? '<small id="login-username-hint" class="field-hint">3 to 32 letters, numbers, dots, dashes, or underscores.</small>' : ""}
          </label>
          ${signup ? `
            <label class="field">
              <span>Email <span class="muted">(optional)</span></span>
              <input name="email" type="email" autocomplete="email" />
            </label>
          ` : ""}
          <label class="field">
            <span>Password</span>
            <input name="password" type="password" required autocomplete="${signup ? "new-password" : "current-password"}"${signup ? ' aria-describedby="login-password-hint"' : ""} />
            ${signup ? '<small id="login-password-hint" class="field-hint">At least 8 characters.</small>' : ""}
          </label>
          <p id="login-error" class="form-error" role="alert" hidden></p>
          <button class="btn btn-primary btn-large" type="submit">${signup ? "Create account" : "Log in"}</button>
        </form>
        <p class="login-switch">${signup ? 'Already have an account? <a href="#/login">Log in</a>' : 'New to Jot? <a href="#/signup">Create an account</a>'}</p>
        <a class="home-guest" href="#/study">Continue as guest</a>
      </div>
    </section>
  `, { section: "login" });

  const form = document.querySelector("#login-form");
  const errorBox = document.querySelector("#login-error");
  const submit = form.querySelector('button[type="submit"]');
  const idleLabel = submit.textContent;

  const fail = (message, field) => {
    errorBox.textContent = message;
    errorBox.hidden = false;
    field?.focus();
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;

    const { identifier, password } = form.elements;
    const name = identifier.value.trim();

    if (!name) return fail(signup ? "Choose a username." : "Enter your username or email.", identifier);
    if (signup && !USERNAME_RULE.test(name)) {
      return fail("Usernames are 3 to 32 characters: letters, numbers, dots, dashes, or underscores.", identifier);
    }
    if (!password.value) return fail("Enter your password.", password);
    if (signup && password.value.length < 8) return fail("Use at least 8 characters for your password.", password);

    submit.disabled = true;
    submit.textContent = signup ? "Creating account..." : "Logging in...";

    try {
      const user = signup
        ? await signUp({ username: name, password: password.value, email: form.elements.email.value.trim() })
        : await signIn(name, password.value);
      await applyAuth(user);
      toast(signup ? `Welcome to Jot, ${displayName(user)}!` : `Welcome back, ${displayName(user)}!`);
      redirect("#/classes");
    } catch (error) {
      submit.disabled = false;
      submit.textContent = idleLabel;
      password.value = "";
      fail(error.message || "Something went wrong. Please try again.", signup && error.code === "username_taken" ? identifier : password);
    }
  });

  form.elements.identifier.focus();
}

// The account page is for logged-in users only; it is where they log out.
function renderAccount() {
  const user = currentUser;

  shell(`
    <section class="account-page section-stack">
      <div class="page-heading">
        <div>
          <h1>Account</h1>
          <p class="muted">Your Jot profile.</p>
        </div>
      </div>
      <div class="panel account-card">
        <dl class="account-details">
          <div><dt>Display name</dt><dd>${escapeHtml(user.display_name)}</dd></div>
          <div><dt>Username</dt><dd>${escapeHtml(user.username)}</dd></div>
          <div><dt>Email</dt><dd>${user.email ? escapeHtml(user.email) : '<span class="muted">Not provided</span>'}</dd></div>
        </dl>
        <div class="account-actions">
          <a class="btn btn-secondary" href="#/classes">${icon("book", { size: 18 })} Your classes</a>
          <button id="account-logout" class="btn btn-danger-ghost" type="button">${icon("logOut", { size: 18 })} Log out</button>
        </div>
      </div>
      <p class="field-hint">Your classes, uploads, and study sets are saved to this account.</p>
    </section>
  `, { section: "account" });

  document.querySelector("#account-logout").addEventListener("click", logOut);
}

// Who may open what. The server enforces the same rules; these only keep the UI honest.
const requireUser = (render, notice = "Log in to see your classes.") => (params) =>
  currentUser ? render(params) : redirect("#/login", notice);
const requireGuest = (render) => (params) =>
  currentUser ? redirect("#/classes") : render(params);

const router = createRouter(
  [
    { path: "/home", render: renderHome },
    { path: "/study", render: requireGuest(renderGuestWorkspace) },
    { path: "/guest", render: requireGuest(renderGuestWorkspace) },
    { path: "/login", render: requireGuest(() => renderLogin({ mode: "signin" })) },
    { path: "/signup", render: requireGuest(() => renderLogin({ mode: "signup" })) },
    { path: "/classes", render: requireUser(renderClasses) },
    { path: "/classes/:id/:tab", render: requireUser(renderClassTab) },
    { path: "/account", render: requireUser(renderAccount, "Log in to see your account.") },
    { path: "/tutor", render: () => renderTutor() },
    { path: "/auth", render: () => redirect(currentUser ? "#/account" : "#/login") }
  ],
  { render: () => (currentUser ? renderClasses() : renderHome()) },
  { beforeRender: () => disposeTutor?.() }
);

window.addEventListener("pagehide", () => disposeTutor?.());
window.addEventListener("pageshow", (event) => {
  if (event.persisted) router.render();
});

// Find out who is logged in before showing anything.
(async () => {
  try {
    await applyAuth(await getCurrentUser());
  } catch (err) {
    console.warn("Initial auth check:", err);
  }

  // The server ended the session while the page was open (for example it expired).
  onAuthStateChange(async () => {
    if (!currentUser) return;
    await applyAuth(null);
    redirect("#/login", "Your session ended. Please log in again.");
  });

  router.render();
})();
