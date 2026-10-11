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
  setDemoContent,
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
import { AVATAR_ACCESSORIES, AVATAR_COLORS, AVATAR_FACES, avatarSvg, describeAvatar, normalizeAvatar, randomAvatar } from "./avatar.js";
import { JotBuddy } from "./jot-buddy.js";
import { renderMathInto } from "./math.js";
import { TUTOR_LANGUAGES, languageLabel, loadTutorLanguage, saveTutorLanguage } from "./tutor-language.js";
import { initTheme, themeToggleMarkup } from "./theme.js";
import { renderGuestStudy } from "./guest-study.js";
import { clearUploadedFiles, deleteUploadedFile, deleteUploadedFiles } from "./file-store.js";
import { escapeHtml, openDialog, toast } from "./ui.js";
import { classBadge, classColor, classInitial, openClassEditor } from "./class-style.js";
import {
  getCurrentUser,
  signIn,
  signUp,
  signOut,
  updateProfile,
  changePassword,
  onAuthStateChange
} from "./api.js";

const app = document.querySelector("#app");
initTheme();

let currentUser = null;
let disposeTutor = null;

const state = {
  session: "idle",
  connection: "offline",
  screen: "not sharing",
  mic: "off",
  micDevice: "",
  tutorMode: "conversation",
  tutorLanguage: loadTutorLanguage(),
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
  const languageSelect = document.querySelector("#tutor-language");
  if (languageSelect) {
    const languageLocked = inSession || ["connecting", "connected"].includes(state.connection);
    languageSelect.value = state.tutorLanguage;
    languageSelect.disabled = languageLocked;
    const locked = state.tutorLanguage !== "auto";
    document.querySelector("#language-description").textContent = locked
      ? `The tutor speaks and writes only in ${languageLabel(state.tutorLanguage)}.${languageLocked ? " End the session to change it." : ""}`
      : languageLocked
        ? "The tutor matches your language. End the session to lock one."
        : "The tutor matches your language. Pick one to keep it in that language.";
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
    // A plain mic while it listens; a mic with a red slash while it does not.
    const micIcon = ["on", "requesting"].includes(state.mic) ? "mic" : "micOff";
    if (micToggle.dataset.icon !== micIcon) {
      micToggle.innerHTML = icon(micIcon, { size: 20 });
      micToggle.dataset.icon = micIcon;
    }
    micToggle.dataset.mic = state.mic;
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
  setDemoContent(user?.is_demo);
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
        <span class="account-avatar">${avatarSvg(currentUser.avatar, { size: 32 })}</span>
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
  wireMenu(menu, {
    signal: accountMenuAbort.signal,
    onSelect: (target) => {
      if (target.matches("[data-logout]")) logOut();
    }
  });
}

/** Keyboard and outside-click behavior for a `[aria-haspopup=menu]` trigger and its `[role=menu]` list. */
function wireMenu(menu, { signal, onSelect } = {}) {
  const trigger = menu.querySelector('[aria-haspopup="menu"]');
  const list = menu.querySelector('[role="menu"]');
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
    onSelect?.(target);
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
      ${themeToggleMarkup()}
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
      ${themeToggleMarkup({ floating: true })}
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

let classMenuAbort = null;

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
          ${currentUser.is_demo ? `<button id="restore-sample-data" class="btn btn-secondary" type="button">${icon("sparkles", { size: 18 })} Restore sample data</button>` : ""}
          <button id="new-class" class="btn btn-primary" type="button">${icon("plus", { size: 18 })} New class</button>
        </div>
      </div>
      <div class="class-grid" id="class-grid"></div>
    </section>
  `, { section: "classes" });

  const grid = document.querySelector("#class-grid");
  if (!classes.length) {
    grid.classList.add("is-empty");
    grid.innerHTML = `
      <div class="panel empty-state">
        <span class="empty-icon">${icon("book", { size: 26 })}</span>
        <strong>No classes yet</strong>
        <span>Create your first class, then add notes, a syllabus, or lectures to study from.</span>
        <button class="btn btn-primary" type="button" data-new-class>${icon("plus", { size: 18 })} Create your first class</button>
      </div>`;
  } else grid.innerHTML = classes.map((klass, index) => {
    const uploads = klass.uploads?.length || 0;
    const materials = klass.materials?.length || 0;
    return `
    <article class="class-card" style="--class-color: ${escapeHtml(classColor(klass))}">
      <a href="#/classes/${encodeURIComponent(klass.id)}/library" class="class-card-main">
        <span class="class-cover" aria-hidden="true">
          <span class="class-icon">${classBadge(klass, { size: 26 })}</span>
        </span>
        <span class="class-body">
          <span class="class-name">${escapeHtml(klass.name)}</span>
          ${klass.focus ? `<span class="class-focus">${escapeHtml(klass.focus)}</span>` : ""}
          <span class="class-meta">
            ${uploads || materials ? `
              <span>${uploads} ${uploads === 1 ? "upload" : "uploads"}</span>
              <span>${materials} study ${materials === 1 ? "set" : "sets"}</span>`
              : `<span>No materials yet</span>`}
          </span>
          ${klass.nextExam ? `<span class="class-next">${icon("syllabus", { size: 15 })}${escapeHtml(klass.nextExam)}</span>` : ""}
        </span>
      </a>
      <div class="class-menu" data-class-menu>
        <button class="class-menu-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="class-menu-${index}" aria-label="Options for ${escapeHtml(klass.name)}">
          ${icon("more", { size: 20 })}
        </button>
        <div class="class-menu-list" id="class-menu-${index}" role="menu" aria-label="${escapeHtml(klass.name)} options" hidden>
          <button role="menuitem" type="button" data-edit="${escapeHtml(klass.id)}">${icon("pencil", { size: 16 })} Edit class</button>
          <button role="menuitem" type="button" data-delete="${escapeHtml(klass.id)}">${icon("trash", { size: 16 })} Delete</button>
        </div>
      </div>
    </article>`;
  }).join("") + `
    <button class="new-class-card" type="button" data-new-class>
      ${icon("plus", { size: 28 })}
      Add a class
    </button>
  `;

  classMenuAbort?.abort();
  classMenuAbort = new AbortController();
  grid.querySelectorAll("[data-class-menu]").forEach((menu) => wireMenu(menu, { signal: classMenuAbort.signal }));

  async function addClass() {
    const name = await openDialog({ title: "New class", input: { label: "Class name" }, confirmLabel: "Create class" });
    if (!name) return;
    const nextClass = await createClass(name);
    window.location.hash = `#/classes/${encodeURIComponent(nextClass.id)}/library`;
  }

  document.querySelector("#new-class").addEventListener("click", addClass);
  document.querySelector("#restore-sample-data")?.addEventListener("click", async () => {
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

    const editId = event.target.closest("[data-edit]")?.dataset.edit;
    const deleteId = event.target.closest("[data-delete]")?.dataset.delete;

    if (editId) {
      const klass = getClassById(editId);
      const next = await openClassEditor(klass);
      if (next) {
        const patch = { color: next.color, glyph: next.glyph };
        if (next.name !== klass.name) Object.assign(patch, { name: next.name, icon: classInitial(next.name) });
        try {
          await updateClass(editId, patch);
          renderClasses();
        } catch (error) {
          toast(`Could not update this class: ${error.message}`, { tone: "error" });
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
        <div class="class-title" style="--class-color: ${escapeHtml(classColor(klass))}">
          <span class="class-icon" aria-hidden="true">${classBadge(klass)}</span>
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
  renderMathInto(item, text);
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
        <div id="jot-buddy"></div>
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
            <label class="language-lock-control" for="tutor-language">
              <span>${icon("lock", { size: 16 })} Language lock</span>
              <select id="tutor-language" aria-describedby="language-description">
                ${TUTOR_LANGUAGES.map((item) => `<option value="${item.id}">${escapeHtml(item.label)}</option>`).join("")}
              </select>
            </label>
            <p id="language-description" role="status" aria-live="polite"></p>
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
          <button type="button" id="mic-toggle" class="btn btn-secondary btn-icon" aria-pressed="false" aria-label="Turn microphone on" aria-describedby="mic-description" title="Turn microphone on" data-mic="off" data-icon="micOff">${icon("micOff", { size: 20 })}</button>
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
  let buddy = null;
  const live = new LiveSessionController({
    classId: klass ? getBackendClassId(klass) : null,
    onMessage: (message) => {
      appendMessage(message.role, message.text);
      if (message.role === "tutor") {
        buddy?.turnComplete();
        buddy?.react(message.text);
      }
    },
    onStatusChange: (connection, errorMessage) => {
      if (disposed) return;
      setStatus({ connection });
      buddy?.setConnection(connection);
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
    onTutorText: (textSoFar) => buddy?.hear(textSoFar),
    onAudioData: (data, mimeType) => {
      audioController?.playPcm(data, mimeType);
      buddy?.audioArrived();
    },
    onAudioInterrupted: () => {
      audioController?.stopPlayback();
      buddy?.interrupted();
    },
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
      buddy?.setMicOn(mic === "on");
      if (wasSendingAudio && ["off", "muted"].includes(mic)) live.endAudioInput();
    },
    onAudioChunk: (chunk) => {
      if (!live.sendAudio(chunk)) audioController.disable();
    },
    onAudioLevel: (level) => {
      if (disposed) return;
      micLevel.style.transform = `scaleX(${level})`;
      buddy?.micLevel(level);
    },
    onError: (error) => {
      if (!disposed) toast(error.message, { tone: "error" });
    }
  });

  buddy = new JotBuddy({
    root: document.querySelector("#jot-buddy"),
    avatar: currentUser?.avatar,
    getSpeechLevel: () => audioController.getOutputLevel(),
    isSpeaking: () => audioController.isSpeaking()
  });
  buddy.setConnection(state.connection);

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
    buddy?.dispose();
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
        await live.connect({ mode: state.tutorMode, language: state.tutorLanguage });
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
      await live.connect({ mode: state.tutorMode, language: state.tutorLanguage });
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
  document.querySelector("#tutor-language").addEventListener("change", (event) => {
    if (event.currentTarget.disabled) return;
    setStatus({ tutorLanguage: saveTutorLanguage(event.currentTarget.value) });
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
      buddy?.questionSent();
      input.value = "";
    } catch (error) {
      toast(error.message || "The question could not be sent.", { tone: "error" });
    }
  });
}

const USERNAME_RULE = /^[A-Za-z0-9_.-]{3,32}$/;

// Log in and Join Jot are full-screen pages of their own. They always ask for
// credentials and let the server decide whether they are valid; nothing here
// ever signs anyone in without a successful answer from the backend.
function renderLogin({ mode = "signin" } = {}) {
  const signup = mode === "signup";
  accountMenuAbort?.abort();

  const alreadyIn = `
    <div class="login-heading">
      <h1>You're already logged in</h1>
      <p class="muted">You're logged in as <strong>${escapeHtml(displayName(currentUser))}</strong>. Log out first if you want to ${signup ? "create a different account" : "use another account"}.</p>
    </div>
    <div class="account-actions">
      <a class="btn btn-primary" href="#/classes">Go to your classes</a>
      <button id="login-logout" class="btn btn-danger-ghost" type="button">${icon("logOut", { size: 18 })} Log out</button>
    </div>
  `;

  const form = `
    <div class="login-heading">
      <h1>${signup ? "Join Jot" : "Log in"}</h1>
      <p class="muted">${signup ? "Create an account to keep your classes, uploads, and study sets." : "Enter your details to open your classes."}</p>
    </div>
    <form id="login-form" class="login-form" novalidate>
      <div class="field">
        <label for="login-identifier">${signup ? "Username" : "Username or email"}</label>
        <input id="login-identifier" name="identifier" type="text" required autocomplete="username" autocapitalize="none" spellcheck="false" aria-describedby="login-identifier-msg"${signup ? ' maxlength="32"' : ""} />
        <small id="login-identifier-msg" class="field-hint">${signup ? "3 to 32 letters, numbers, dots, dashes, or underscores." : ""}</small>
      </div>
      ${signup ? `
        <div class="field">
          <label for="login-email">Email <span class="muted">(optional)</span></label>
          <input id="login-email" name="email" type="email" autocomplete="email" />
        </div>
      ` : ""}
      <div class="field">
        <label for="login-password">Password</label>
        <div class="password-row">
          <input id="login-password" name="password" type="password" required autocomplete="${signup ? "new-password" : "current-password"}" aria-describedby="login-password-msg" />
          <button id="login-toggle" class="btn btn-secondary password-toggle" type="button" aria-controls="login-password" aria-pressed="false">Show</button>
        </div>
        <small id="login-password-msg" class="field-hint">${signup ? "At least 8 characters." : ""}</small>
      </div>
      <p id="login-error" class="form-error" role="alert" hidden></p>
      <button class="btn btn-primary btn-large" type="submit">${signup ? "Create account" : "Log in"}</button>
    </form>
    <p class="login-switch">${signup ? 'Already have an account? <a href="#/login">Log in</a>' : 'New to Jot? <a href="#/signup">Create an account</a>'}</p>
    <a class="home-guest" href="#/study">Continue as guest</a>
  `;

  app.innerHTML = `
    <main id="main-content" class="home auth-screen" tabindex="-1">
      ${themeToggleMarkup({ floating: true })}
      <div class="auth-screen-inner">
        <a class="brand" href="#/home">
          <span class="brand-mark" aria-hidden="true"><img src="${jotMascot}" alt="" /></span>
          Jot
        </a>
        <div class="panel login-card">${currentUser ? alreadyIn : form}</div>
        <a class="auth-back" href="#/home">${icon("arrowLeft", { size: 16 })} Back to welcome</a>
      </div>
    </main>
  `;

  if (currentUser) {
    document.querySelector("#login-logout").addEventListener("click", logOut);
    return;
  }

  const formEl = document.querySelector("#login-form");
  const errorBox = document.querySelector("#login-error");
  const submit = formEl.querySelector('button[type="submit"]');
  const idleLabel = submit.textContent;
  const { identifier, password } = formEl.elements;

  const hints = new Map([
    [identifier, document.querySelector("#login-identifier-msg")],
    [password, document.querySelector("#login-password-msg")]
  ]);
  const defaultHints = new Map([...hints].map(([field, hint]) => [field, hint.textContent]));

  const markInvalid = (field, message) => {
    field.setAttribute("aria-invalid", "true");
    const hint = hints.get(field);
    hint.textContent = message;
    hint.classList.add("is-error");
    field.focus();
  };
  const clearInvalid = (field) => {
    field.removeAttribute("aria-invalid");
    const hint = hints.get(field);
    hint.textContent = defaultHints.get(field);
    hint.classList.remove("is-error");
  };
  const clearAll = () => {
    errorBox.hidden = true;
    clearInvalid(identifier);
    clearInvalid(password);
  };
  identifier.addEventListener("input", () => clearInvalid(identifier));
  password.addEventListener("input", () => clearInvalid(password));

  const toggle = document.querySelector("#login-toggle");
  toggle.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    toggle.textContent = show ? "Hide" : "Show";
    toggle.setAttribute("aria-pressed", String(show));
  });

  const setBusy = (busy) => {
    for (const element of formEl.elements) element.disabled = busy;
    submit.textContent = busy ? (signup ? "Creating your account..." : "Checking your details...") : idleLabel;
  };

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearAll();

    const name = identifier.value.trim();
    if (!name) return markInvalid(identifier, signup ? "Choose a username." : "Enter your username or email.");
    if (signup && !USERNAME_RULE.test(name)) {
      return markInvalid(identifier, "Usernames are 3 to 32 characters: letters, numbers, dots, dashes, or underscores.");
    }
    if (!password.value) return markInvalid(password, "Enter your password.");
    if (signup && password.value.length < 8) return markInvalid(password, "Use at least 8 characters.");

    setBusy(true);
    try {
      // The server checks the details. Only a successful answer logs anyone in.
      const user = signup
        ? await signUp({ username: name, password: password.value, email: formEl.elements.email.value.trim() })
        : await signIn(name, password.value);
      await applyAuth(user);
      toast(signup ? `Welcome to Jot, ${displayName(user)}!` : `Welcome back, ${displayName(user)}!`);
      redirect("#/classes");
    } catch (error) {
      setBusy(false);
      password.value = "";
      errorBox.textContent = error.message || "Something went wrong. Please try again.";
      errorBox.hidden = false;
      if (error.code === "username_taken") {
        markInvalid(identifier, "That username is taken.");
      } else if (error.code === "invalid_credentials") {
        identifier.setAttribute("aria-invalid", "true");
        markInvalid(password, "Check your password and try again.");
      } else {
        password.focus();
      }
    }
  });

  identifier.focus();
}

// Inline field messages shared by the account forms. Each field has a small
// ".field-hint" under it; an error replaces the hint until the field is edited.
function showFieldError(input, message) {
  input.setAttribute("aria-invalid", "true");
  const hint = input.closest(".field").querySelector(".field-hint");
  hint.dataset.default ??= hint.textContent;
  hint.textContent = message;
  hint.classList.add("is-error");
  input.focus();
}

function clearFieldError(input) {
  input.removeAttribute("aria-invalid");
  const hint = input.closest(".field").querySelector(".field-hint");
  if (hint.dataset.default !== undefined) hint.textContent = hint.dataset.default;
  hint.classList.remove("is-error");
}

const EMAIL_RULE = /^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$/;

// The account page is for logged-in users only. It is where they change their
// details and password, and where they log out.
function renderAccount() {
  const user = currentUser;
  const currentAvatar = normalizeAvatar(user.avatar);

  shell(`
    <section class="account-page section-stack">
      <div class="page-heading">
        <div>
          <h1>Account</h1>
          <p class="muted">Signed in as <strong>${escapeHtml(user.username)}</strong>${user.is_demo ? ' <span class="pill no-dot">Demo account</span>' : ""}</p>
        </div>
        <div class="page-actions">
          <a class="btn btn-secondary" href="#/classes">${icon("book", { size: 18 })} Your classes</a>
          <button id="account-logout" class="btn btn-danger-ghost" type="button">${icon("logOut", { size: 18 })} Log out</button>
        </div>
      </div>

      <section id="jot-builder" class="panel jot-builder" aria-labelledby="jot-builder-title">
        <div class="jot-preview">
          <div class="jot-stage"></div>
          <p class="jot-caption" aria-live="polite"></p>
          <div class="jot-actions">
            <button id="jot-shuffle" class="btn btn-secondary" type="button">${icon("sparkles", { size: 18 })} Surprise me</button>
            <button id="jot-save" class="btn btn-primary" type="button">Save my Jot</button>
          </div>
          <p class="form-error" role="alert" hidden></p>
        </div>
        <div class="jot-options">
          <div>
            <h2 id="jot-builder-title">Create your own Jot</h2>
            <p class="muted">Pick a color, a face, and an accessory. Your Jot shows up next to your name.</p>
          </div>
          <fieldset class="jot-group">
            <legend>Color</legend>
            <div class="jot-swatches">
              ${AVATAR_COLORS.map((item) => `
                <label class="jot-swatch">
                  <input type="radio" name="jot-color" value="${item.id}"${item.id === currentAvatar.color ? " checked" : ""} />
                  <span class="jot-swatch-dot" style="--swatch: ${item.body}"></span>
                  <span class="visually-hidden">${item.name}</span>
                </label>`).join("")}
            </div>
          </fieldset>
          <fieldset class="jot-group">
            <legend>Face</legend>
            <div class="jot-tiles">
              ${AVATAR_FACES.map((item) => `
                <label class="jot-tile">
                  <input type="radio" name="jot-face" value="${item.id}"${item.id === currentAvatar.face ? " checked" : ""} />
                  <span class="jot-tile-art" data-face-art="${item.id}"></span>
                  <span class="jot-tile-name">${item.name}</span>
                </label>`).join("")}
            </div>
          </fieldset>
          <fieldset class="jot-group">
            <legend>Accessory</legend>
            <div class="jot-tiles">
              ${AVATAR_ACCESSORIES.map((item) => `
                <label class="jot-tile">
                  <input type="radio" name="jot-accessory" value="${item.id}"${item.id === currentAvatar.accessory ? " checked" : ""} />
                  <span class="jot-tile-art" data-accessory-art="${item.id}"></span>
                  <span class="jot-tile-name">${item.name}</span>
                </label>`).join("")}
            </div>
          </fieldset>
        </div>
      </section>

      <div class="account-grid">
        <form id="profile-form" class="panel account-section" novalidate>
          <div>
            <h2>Your details</h2>
            <p class="muted">Change the username or email you log in with.</p>
          </div>
          <div class="field">
            <label for="profile-username">Username</label>
            <input id="profile-username" name="username" type="text" required autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="32" value="${escapeHtml(user.username)}" />
            <small class="field-hint">3 to 32 letters, numbers, dots, dashes, or underscores.</small>
          </div>
          <div class="field">
            <label for="profile-email">Email <span class="muted">(optional)</span></label>
            <input id="profile-email" name="email" type="email" autocomplete="email" value="${escapeHtml(user.email || "")}" />
            <small class="field-hint">Clear this to remove your email.</small>
          </div>
          <p class="form-error" role="alert" hidden></p>
          <button class="btn btn-primary" type="submit">Save changes</button>
        </form>

        <form id="password-form" class="panel account-section" novalidate>
          <div>
            <h2>Change password</h2>
            <p class="muted">You'll stay logged in here. Your other devices will be logged out.</p>
          </div>
          <div class="field">
            <label for="password-current">Current password</label>
            <input id="password-current" name="current" type="password" required autocomplete="current-password" />
            <small class="field-hint"></small>
          </div>
          <div class="field">
            <label for="password-new">New password</label>
            <input id="password-new" name="next" type="password" required autocomplete="new-password" />
            <small class="field-hint">At least 8 characters.</small>
          </div>
          <div class="field">
            <label for="password-confirm">Confirm new password</label>
            <input id="password-confirm" name="confirm" type="password" required autocomplete="new-password" />
            <small class="field-hint"></small>
          </div>
          <p class="form-error" role="alert" hidden></p>
          <button class="btn btn-primary" type="submit">Change password</button>
        </form>
      </div>

      <p class="field-hint">Your classes, uploads, and study sets are saved to this account.</p>
    </section>
  `, { section: "account" });

  document.querySelector("#account-logout").addEventListener("click", logOut);

  for (const input of document.querySelectorAll("#profile-form input, #password-form input")) {
    input.addEventListener("input", () => clearFieldError(input));
  }

  // --- Your Jot ---------------------------------------------------------------
  const builder = document.querySelector("#jot-builder");
  const stage = builder.querySelector(".jot-stage");
  const caption = builder.querySelector(".jot-caption");
  const jotSave = builder.querySelector("#jot-save");
  const jotError = builder.querySelector(".form-error");
  let draft = { ...currentAvatar };

  const sameAvatar = (a, b) => a.color === b.color && a.face === b.face && a.accessory === b.accessory;

  // The big preview and every face and accessory tile are drawn in the current color.
  const paintJot = ({ pop = true } = {}) => {
    stage.innerHTML = avatarSvg(draft, { size: 176 });
    caption.textContent = describeAvatar(draft);
    for (const art of builder.querySelectorAll("[data-face-art]")) {
      art.innerHTML = avatarSvg({ ...draft, face: art.dataset.faceArt, accessory: "none" }, { size: 52 });
    }
    for (const art of builder.querySelectorAll("[data-accessory-art]")) {
      art.innerHTML = avatarSvg({ ...draft, accessory: art.dataset.accessoryArt }, { size: 52 });
    }
    if (pop) {
      stage.classList.remove("is-popping");
      void stage.offsetWidth;
      stage.classList.add("is-popping");
    }
  };

  builder.addEventListener("change", (event) => {
    const input = event.target.closest('input[type="radio"]');
    if (!input) return;
    draft[input.name.replace("jot-", "")] = input.value;
    jotError.hidden = true;
    paintJot();
  });

  builder.querySelector("#jot-shuffle").addEventListener("click", () => {
    draft = randomAvatar();
    for (const input of builder.querySelectorAll('input[type="radio"]')) {
      input.checked = draft[input.name.replace("jot-", "")] === input.value;
    }
    jotError.hidden = true;
    paintJot();
  });

  jotSave.addEventListener("click", async () => {
    if (sameAvatar(draft, currentAvatar)) {
      toast("This is already your Jot.");
      return;
    }
    jotError.hidden = true;
    jotSave.disabled = true;
    jotSave.textContent = "Saving...";
    try {
      currentUser = await updateProfile({ avatar: draft });
      toast("Your Jot is saved.");
      renderAccount();
    } catch (error) {
      jotSave.disabled = false;
      jotSave.textContent = "Save my Jot";
      jotError.textContent = error.message || "Could not save your Jot. Please try again.";
      jotError.hidden = false;
    }
  });

  paintJot({ pop: false });

  // --- Username and email -------------------------------------------------
  const profileForm = document.querySelector("#profile-form");
  const profileError = profileForm.querySelector(".form-error");
  const profileSave = profileForm.querySelector('button[type="submit"]');

  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    profileError.hidden = true;
    profileForm.querySelectorAll("input").forEach(clearFieldError);
    const { username, email } = profileForm.elements;
    const nextName = username.value.trim();
    const nextEmail = email.value.trim();

    if (!USERNAME_RULE.test(nextName)) {
      return showFieldError(username, "Usernames are 3 to 32 characters: letters, numbers, dots, dashes, or underscores.");
    }
    if (nextEmail && !EMAIL_RULE.test(nextEmail)) return showFieldError(email, "Enter a valid email address.");

    const changes = {};
    if (nextName !== user.username) changes.username = nextName;
    if (nextEmail !== (user.email || "")) changes.email = nextEmail || null;
    if (!Object.keys(changes).length) {
      toast("Nothing to save. Your details are already up to date.");
      return;
    }

    profileSave.disabled = true;
    profileSave.textContent = "Saving...";
    try {
      currentUser = await updateProfile(changes);
      toast("Your details were updated.");
      renderAccount();
    } catch (error) {
      profileSave.disabled = false;
      profileSave.textContent = "Save changes";
      if (error.code === "username_taken") return showFieldError(username, "That username is taken.");
      if (error.code === "email_taken") return showFieldError(email, "That email is already used by another account.");
      profileError.textContent = error.message || "Could not save your details. Please try again.";
      profileError.hidden = false;
    }
  });

  // --- Password -------------------------------------------------------------
  const passwordForm = document.querySelector("#password-form");
  const passwordError = passwordForm.querySelector(".form-error");
  const passwordSave = passwordForm.querySelector('button[type="submit"]');

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    passwordError.hidden = true;
    passwordForm.querySelectorAll("input").forEach(clearFieldError);
    const { current, next, confirm } = passwordForm.elements;

    if (!current.value) return showFieldError(current, "Enter your current password.");
    if (next.value.length < 8) return showFieldError(next, "Use at least 8 characters.");
    if (next.value === current.value) return showFieldError(next, "Choose a password that is different from your current one.");
    if (confirm.value !== next.value) return showFieldError(confirm, "The passwords don't match.");

    passwordSave.disabled = true;
    passwordSave.textContent = "Changing...";
    try {
      await changePassword(current.value, next.value);
      passwordForm.reset();
      toast("Password changed. Your other devices were logged out.");
    } catch (error) {
      if (error.code === "incorrect_password") {
        showFieldError(current, "That isn't your current password.");
      } else {
        passwordError.textContent = error.message || "Could not change your password. Please try again.";
        passwordError.hidden = false;
      }
    } finally {
      passwordSave.disabled = false;
      passwordSave.textContent = "Change password";
    }
  });
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
    { path: "/login", render: () => renderLogin({ mode: "signin" }) },
    { path: "/signup", render: () => renderLogin({ mode: "signup" }) },
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
