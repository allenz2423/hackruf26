import "./styles.css";
import jotMascot from "./assets/jot-mascot.png";
import { AnnotationController } from "./annotations.js";
import { AudioController } from "./audio.js";
import {
  getClasses,
  getClassById,
  classHasCapability,
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
import { TutorMemoryPanel, tutorMemoryMarkup } from "./tutor-memory.js";
import { getBackendClassId, prepareTutorSources } from "./course-sync.js";
import { MaterialStage } from "./material-stage.js";
import { readQuizTutorContext } from "./quiz-tutor-context.js";
import { chooseMaterial, chooseTutorSources, loadTutorSources, saveTutorSources, sourcesSummary } from "./tutor-materials.js";
import { icon } from "./icons.js";
import { renderLibraryTab } from "./library.js";
import { renderConceptGraphTab } from "./concept-graph.js";
import { createRouter } from "./router.js";
import { ScreenCaptureController } from "./screen-capture.js";
import { renderStudyTab } from "./study.js";
import { renderLearningProgress } from "./progress.js";
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
  getAuthOptions,
  signIn,
  signUp,
  signOut,
  getSignupRequestsApi,
  getAIProviderSettingsApi,
  updateAIProviderSettingsApi,
  resetAIProviderSettingsApi,
  testAIProviderSettingsApi,
  approveSignupRequestApi,
  rejectSignupRequestApi,
  createClassInvitationApi,
  getClassInvitationsApi,
  revokeClassInvitationApi,
  redeemClassInvitationApi,
  getClassMembersApi,
  updateClassMemberCapabilitiesApi,
  removeClassMemberApi,
  leaveClassApi,
  updateProfile,
  changePassword,
  onAuthStateChange
} from "./api.js";

const app = document.querySelector("#app");
initTheme();

let currentUser = null;
let disposeTutor = null;
let tutorMemoryPanel = null;

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
  screen: { sharing: "live", material: "live", selecting: "busy", paused: "busy" },
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
  const onStage = sharing || state.screen === "material";
  const inSession = !["idle", "ended", "error"].includes(state.session);
  const starting = ["connecting", "reconnecting", "ending"].includes(state.session) || !!tutorMemoryPanel?.writing;
  // A material on the stage can be swapped for the screen and back without pausing.
  share.disabled = sharing || starting;
  const openMaterial = document.querySelector("#open-material");
  if (openMaterial) openMaterial.disabled = state.screen === "selecting" || starting;
  document.querySelector("#pause-screen").disabled = !onStage;
  document.querySelector("#end-session").disabled = !inSession || state.session === "ending";
  tutorMemoryPanel?.setLocked(inSession || ["connecting", "connected"].includes(state.connection));
  const sources = document.querySelector("#tutor-sources");
  if (sources) {
    const sourcesLocked = inSession || ["connecting", "connected"].includes(state.connection);
    sources.disabled = sourcesLocked;
    sources.title = sourcesLocked
      ? "End the session to change which materials the tutor reads"
      : "Choose which materials the tutor reads";
  }
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
  const access = getAuthOptions();

  if (!currentUser) {
    return `
      <div class="account-menu" data-account-menu>
        <button class="btn btn-secondary account-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu-list">
          Join Jot ${chevron}
        </button>
        <div class="account-menu-list" id="account-menu-list" role="menu" aria-label="Join Jot" hidden>
          <a role="menuitem" href="#/login">${icon("user", { size: 16 })} Log in</a>
          ${access.signupMode !== "closed" ? `<a role="menuitem" href="#/signup">${icon("plus", { size: 16 })} ${access.signupMode === "approval" ? "Request an account" : "Create account"}</a>` : ""}
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
        ${currentUser.is_admin ? `<a role="menuitem" href="#/admin">${icon("user", { size: 16 })} Admin</a>` : ""}
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

// `section` picks the highlighted nav link; `tutor` marks any tutor page, including a class's Tutor tab.
function shell(content, { section = "classes", tutor = section === "tutor", compact = tutor } = {}) {
  const isGuest = !currentUser;
  const guestAccess = getAuthOptions().guestAccessEnabled;
  const brandHref = "#/home";
  const showSessionStatus = tutor || !["idle", "ended"].includes(state.session);
  const sessionStatus = showSessionStatus ? `
    <div class="status-strip" role="status" aria-live="polite" aria-label="Session status">
      ${Object.keys(STATUS_LABELS).map(statusPill).join("")}
    </div>` : "";

  app.innerHTML = `
    <a class="skip-link" href="#main-content">Skip to content</a>
    <header class="app-header${compact ? " app-header--compact" : ""}">
      <a class="brand" href="${brandHref}">
        <span class="brand-mark" aria-hidden="true"><img src="${jotMascot}" alt="" /></span>
        Jot
      </a>
      ${compact ? sessionStatus : ""}
      <nav class="primary-nav" aria-label="Primary">
        ${isGuest && guestAccess ? `
          <a href="#/study" ${section === "study" ? 'aria-current="page"' : ""}>Quick study</a>
        ` : currentUser ? `
          <a href="#/classes" ${section === "classes" ? 'aria-current="page"' : ""}>Classes</a>
          <a href="#/progress" ${section === "progress" ? 'aria-current="page"' : ""}>Progress</a>
        ` : ""}
        ${currentUser || guestAccess ? `<a href="#/tutor" ${section === "tutor" ? 'aria-current="page"' : ""}>Quick tutor</a>` : ""}
      </nav>
      ${themeToggleMarkup()}
      ${accountMenuMarkup()}
    </header>
    ${showSessionStatus && !compact ? `<div class="session-status-row">${sessionStatus}</div>` : ""}
    <main id="main-content" class="${compact ? "workspace-main" : ""}${tutor ? " tutor-main" : ""}" tabindex="-1">${content}</main>
  `;

  app.querySelector(".skip-link")?.addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelector("#main-content")?.focus();
  });

  wireAccountMenu();
}

function renderLearningProgressPage() {
  shell(`<div id="learning-progress-root" class="learning-progress-page" aria-live="polite"></div>`, { section: "progress" });
  void renderLearningProgress({ root: document.querySelector("#learning-progress-root") });
}

function renderHome() {
  const name = displayName(currentUser);
  const access = getAuthOptions();

  const copy = currentUser
    ? `<p class="home-greeting">Welcome back, ${escapeHtml(name)}! Pick up where you left off, or open a class to study or get tutoring from its materials.</p>
        <div class="home-actions">
          <a class="btn btn-primary btn-large" href="#/classes">Go to your classes</a>
          <a class="home-guest" href="#/account">Account</a>
        </div>`
    : `<p class="home-greeting">${access.guestAccessEnabled
      ? "Welcome! Share your screen and ask me anything out loud, or drop in your notes and lectures and I'll turn them into quizzes, flashcards, and study guides."
      : "Sign in to use Jot. Your classes and study materials are available only to your account."}</p>
        <div class="home-actions">
          <a class="btn btn-primary btn-large" href="#/login">Log in</a>
          ${access.signupMode !== "closed" ? `<a class="home-guest" href="#/signup">${access.signupMode === "approval" ? "Request an account" : "Create an account"}</a>` : ""}
          ${access.guestAccessEnabled
            ? `<a class="home-guest" href="#/study">Continue as guest</a><p class="home-note">Guests can try quick study and tutoring, but nothing is saved.</p>`
            : access.signupMode === "approval" ? `<p class="home-note">New accounts require administrator approval.</p>` : ""}
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
  if (!getAuthOptions().guestAccessEnabled) {
    redirect("#/login", "Guest study is disabled on this Jot server.");
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
      <form class="panel class-join-panel" id="join-class-form">
        <div>
          <h2>Join a class</h2>
          <p class="muted">Enter a temporary invitation code from a class manager.</p>
        </div>
        <label class="class-join-field" for="class-invitation-code">
          <span class="visually-hidden">Invitation code</span>
          <input id="class-invitation-code" name="code" autocomplete="off" required minlength="24" maxlength="128" placeholder="Paste invitation code" />
        </label>
        <button class="btn btn-secondary" type="submit">${icon("arrowRight", { size: 16 })} Join class</button>
        <p class="field-hint class-join-status" id="class-join-status" role="status" aria-live="polite"></p>
      </form>
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
          ${(klass.isOwner || classHasCapability(klass, "manage_permissions")) ? `<button role="menuitem" type="button" data-edit="${escapeHtml(klass.id)}">${icon("pencil", { size: 16 })} Edit class</button>` : ""}
          ${klass.isOwner ? `<button role="menuitem" type="button" data-delete="${escapeHtml(klass.id)}">${icon("trash", { size: 16 })} Delete</button>` : `<button role="menuitem" type="button" data-leave="${escapeHtml(klass.id)}">${icon("arrowLeft", { size: 16 })} Leave class</button>`}
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
    const name = await openDialog({ title: "New class", input: { label: "Class name" }, confirmLabel: "Create class", confirmIcon: "plus" });
    if (!name) return;
    const nextClass = await createClass(name);
    window.location.hash = `#/classes/${encodeURIComponent(nextClass.id)}/library`;
  }

  document.querySelector("#new-class").addEventListener("click", addClass);
  document.querySelector("#join-class-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.code;
    const status = form.querySelector("#class-join-status");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = "Checking invitation…";
    try {
      const result = await redeemClassInvitationApi(input.value);
      await syncClassesFromApi();
      const joined = getClasses().find((item) => (item.backendId || item.id) === result.class_id);
      if (!joined) throw new Error("You joined, but the class list has not refreshed yet. Reload this page to continue.");
      input.value = "";
      toast(result.status === "already_member" ? "You already belong to this class." : `Joined ${result.class_name || joined.name}.`);
      window.location.hash = `#/classes/${encodeURIComponent(joined.id)}/library`;
    } catch (error) {
      status.textContent = error.message || "That invitation could not be used.";
      status.dataset.tone = "bad";
    } finally {
      button.disabled = false;
    }
  });
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
    const leaveId = event.target.closest("[data-leave]")?.dataset.leave;

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

    if (leaveId) {
      const klass = getClassById(leaveId);
      const confirmed = await openDialog({
        title: `Leave ${klass.name}?`,
        message: "You will immediately lose access to this class and its shared materials. Your personal study materials remain yours.",
        confirmLabel: "Leave class",
        danger: true
      });
      if (!confirmed) return;
      try {
        await leaveClassApi(klass.backendId || klass.id);
        await syncClassesFromApi();
        toast(`You left ${klass.name}.`);
        renderClasses();
      } catch (error) {
        toast(`Could not leave this class: ${error.message}`, { tone: "error" });
      }
    }
  });
}

const CLASS_TABS = [
  { id: "library", label: "Library", icon: "book" },
  { id: "concepts", label: "Concepts", icon: "chart" },
  { id: "study", label: "Study", icon: "sparkles" },
  { id: "tutor", label: "Tutor", icon: "monitor" },
  { id: "people", label: "People", icon: "user" }
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
  const backendClassId = getBackendClassId(klass);
  if (tab === "concepts" && !backendClassId) {
    // The class map is backed by the server's source index. Local sample
    // classes should keep their existing browser-only workflow unchanged.
    window.location.hash = `#/classes/${encodeURIComponent(klass.id)}/library`;
    return;
  }
  const tutor = tab === "tutor";
  if (["concepts", "people"].includes(tab) && !classHasCapability(klass, "read")) {
    window.location.hash = "#/classes";
    return;
  }
  const backLink = `<a class="back-link" href="#/classes">${icon("arrowLeft", { size: 16 })} All classes</a>`;

  shell(`
    <section>
      <div class="class-header class-header--compact" style="--class-color: ${escapeHtml(classColor(klass))}">
        ${backLink}
        <div class="class-title">
          <span class="class-icon" aria-hidden="true">${classBadge(klass)}</span>
          <div>
            <h1>${escapeHtml(klass.name)}</h1>
            ${klass.focus ? `<p class="muted">${escapeHtml(klass.focus)}</p>` : ""}
          </div>
        </div>
        <nav class="tabs" aria-label="${escapeHtml(klass.name)} sections">
          ${CLASS_TABS.filter((item) => item.id !== "concepts" || (backendClassId && classHasCapability(klass, "read"))).map((item) => `
            <a href="#/classes/${encodeURIComponent(klass.id)}/${item.id}" ${tab === item.id ? 'aria-current="page"' : ""}>
              ${icon(item.icon, { size: 18 })} ${item.label}
            </a>
          `).join("")}
        </nav>
      </div>
      <div id="class-tab"></div>
    </section>
  `, { section: "classes", tutor, compact: true });

  const root = document.querySelector("#class-tab");

  if (tab === "study") {
    await renderStudyTab({
      root,
      klass,
      currentUserId: currentUser?.id || null,
      onMaterialAdded: (material) => {
        if (material.resourceScope === "personal") {
          // Personal resources are served from the owner-scoped API and must not
          // enter the browser's account-agnostic class cache.
          const index = klass.materials.findIndex((item) => item.id === material.id);
          if (index >= 0) klass.materials[index] = material;
          else klass.materials.push(material);
          return material;
        }
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

  if (tab === "concepts") {
    await renderConceptGraphTab({ root, klass });
    return;
  }

  if (tab === "people") {
    await renderClassPeople({ root, klass });
    return;
  }

  await renderLibraryTab({
    root,
    klass,
    currentUserId: currentUser?.id || null,
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

const CLASS_CAPABILITIES = [
  ["read", "View shared materials"],
  ["create", "Create class resources"],
  ["edit", "Edit own contributions"],
  ["delete", "Delete own contributions"],
  ["delete_others", "Delete other contributions"],
  ["invite", "Invite members"],
  ["manage_members", "Remove members"],
  ["manage_permissions", "Manage permissions"]
];

function availableClassCapabilities(klass) {
  return klass.isOwner
    ? CLASS_CAPABILITIES.map(([capability]) => capability)
    : CLASS_CAPABILITIES.map(([capability]) => capability).filter((capability) => klass.capabilities?.includes(capability));
}

function capabilityInputs(name, selected, available, { disabled = false } = {}) {
  return CLASS_CAPABILITIES.filter(([capability]) => available.includes(capability)).map(([capability, label]) => `
    <label class="capability-option">
      <input type="checkbox" name="${escapeHtml(name)}" value="${capability}" ${selected.includes(capability) || capability === "read" ? "checked" : ""} ${disabled || capability === "read" ? "disabled" : ""} />
      <span>${escapeHtml(label)}</span>
    </label>
  `).join("");
}

async function renderClassPeople({ root, klass }) {
  const classId = getBackendClassId(klass);
  if (!classId) {
    root.innerHTML = `<div class="panel empty-state"><strong>Class sharing requires a saved class</strong><span>Save this class to your account before inviting members.</span></div>`;
    return;
  }

  const canInvite = classHasCapability(klass, "invite");
  const canManageMembers = classHasCapability(klass, "manage_members");
  const canManagePermissions = classHasCapability(klass, "manage_permissions");
  const available = availableClassCapabilities(klass);
  root.innerHTML = `<div class="panel empty-state" role="status">Loading class members…</div>`;

  const [memberResult, invitationResult] = await Promise.allSettled([
    getClassMembersApi(classId),
    canInvite ? getClassInvitationsApi(classId) : Promise.resolve([])
  ]);
  const members = memberResult.status === "fulfilled" ? memberResult.value : [];
  const invitations = invitationResult.status === "fulfilled" ? invitationResult.value : [];
  const memberError = memberResult.status === "rejected" ? memberResult.reason?.message : "";
  const invitationError = invitationResult.status === "rejected" ? invitationResult.reason?.message : "";

  root.innerHTML = `
    <section class="section-stack class-people-page">
      <div class="panel class-people-panel">
        <div class="class-people-heading">
          <div><h2>Class members</h2><p class="muted">Membership controls who can read or contribute to shared class resources.</p></div>
          <span class="pill no-dot">${members.length} ${members.length === 1 ? "member" : "members"}</span>
        </div>
        ${memberError ? `<p class="class-inline-error" role="alert">Could not load members: ${escapeHtml(memberError)}</p>` : ""}
        ${members.length ? `<ul class="class-member-list">${members.map((member) => {
          const userId = String(member.user_id);
          const isSelf = userId === String(currentUser?.id);
          const isOwner = member.is_owner === true;
          const current = Array.isArray(member.capabilities) ? member.capabilities : ["read"];
          const canEditThisMember = canManagePermissions && !isOwner && !isSelf
            && current.every((capability) => available.includes(capability));
          const display = member.display_name || member.username || "Class member";
          return `<li class="class-member-row">
            <div class="class-member-identity"><span class="member-avatar" aria-hidden="true">${escapeHtml(display.trim().slice(0, 1).toUpperCase() || "?")}</span><div><strong>${escapeHtml(display)}${isSelf ? " (you)" : ""}${isOwner ? " · owner" : ""}</strong><span class="row-meta">${escapeHtml(member.username ? `@${member.username}` : "Member")} · joined ${escapeHtml(formatClassDate(member.joined_at))}</span></div></div>
            <div class="class-member-capabilities">${current.map((capability) => `<span class="permission-badge">${escapeHtml(CLASS_CAPABILITIES.find(([key]) => key === capability)?.[1] || capability)}</span>`).join("")}</div>
            ${canEditThisMember ? `<details class="member-permission-editor"><summary class="btn btn-ghost btn-small">Permissions</summary>
              <form data-member-permissions="${escapeHtml(userId)}">
                <label class="field"><span>Permission preset</span><select data-permission-preset>
                  <option value="custom">Custom</option><option value="reader">Read only</option>
                  ${available.includes("create") ? `<option value="creator">Read + create</option>` : ""}
                  ${["create", "edit", "delete"].every((capability) => available.includes(capability)) ? `<option value="editor">Contributor with own-resource controls</option>` : ""}
                </select></label>
                <div class="capability-grid">${capabilityInputs(`caps-${userId}`, current, available)}</div>
                <div class="dialog-actions"><button class="btn btn-primary btn-small" type="submit">Save permissions</button>${canManageMembers ? `<button class="btn btn-danger-ghost btn-small" type="button" data-remove-member="${escapeHtml(userId)}">Remove</button>` : ""}</div>
              </form>
            </details>` : canManageMembers && !isOwner && !isSelf ? `<button class="btn btn-danger-ghost btn-small" type="button" data-remove-member="${escapeHtml(userId)}">Remove</button>` : ""}
          </li>`;
        }).join("")}</ul>` : !memberError ? `<p class="muted">No other members have joined yet.</p>` : ""}
      </div>

      ${canInvite ? `<div class="panel class-invitations-panel">
        <div><h2>Invitations</h2><p class="muted">Codes expire or stop working after their use limit. Revoke a code at any time.</p></div>
        ${invitationError ? `<p class="class-inline-error" role="alert">Could not load invitations: ${escapeHtml(invitationError)}</p>` : ""}
        <form id="create-class-invitation" class="class-invitation-form">
          <fieldset>
            <legend>New invitation permissions</legend>
            <p class="field-hint class-capability-note">Read access is always included. Select any additional permissions to grant.</p>
            <div class="capability-grid">${capabilityInputs("invitation-caps", ["read"], available)}</div>
          </fieldset>
          <div class="field-grid">
            <label class="field"><span>Expires in</span><select name="expires_in_days"><option value="1">1 day</option><option value="7" selected>7 days</option><option value="30">30 days</option><option value="90">90 days</option></select></label>
            <label class="field"><span>Successful uses</span><input name="max_uses" type="number" min="1" max="100" value="1" required /></label>
          </div>
          <button class="btn btn-primary" type="submit">${icon("plus", { size: 16 })} Create invitation</button>
          <p class="field-hint" id="invitation-status" role="status" aria-live="polite"></p>
          <div id="new-invitation-code" class="invitation-code-result" hidden></div>
        </form>
        <ul class="invitation-list">${invitations.map((invitation) => `
          <li><div><strong>${invitation.active ? "Active invitation" : "Inactive invitation"}</strong><span class="row-meta">Expires ${escapeHtml(formatClassDate(invitation.expires_at))} · ${invitation.successful_uses}/${invitation.max_uses} uses · ${escapeHtml((invitation.capabilities || []).join(", "))}</span></div>${invitation.active ? `<button class="btn btn-ghost btn-small" type="button" data-revoke-invitation="${escapeHtml(invitation.id)}">Revoke</button>` : `<span class="pill no-dot">${invitation.revoked_at ? "Revoked" : "Expired or used"}</span>`}</li>
        `).join("") || `<li class="muted">No invitations yet.</li>`}</ul>
      </div>` : ""}

      ${!klass.isOwner ? `<div class="class-leave-row"><button id="leave-class-from-people" class="btn btn-danger-ghost" type="button">Leave ${escapeHtml(klass.name)}</button></div>` : ""}
    </section>
  `;

  root.querySelectorAll("[data-member-permissions]").forEach((form) => {
    const presets = {
      reader: ["read"],
      creator: ["read", "create"],
      editor: ["read", "create", "edit", "delete"]
    };
    form.querySelector("[data-permission-preset]").addEventListener("change", (event) => {
      const preset = presets[event.target.value];
      if (!preset) return;
      form.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => { checkbox.checked = preset.includes(checkbox.value); });
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const capabilities = [...form.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
      if (!capabilities.includes("read")) capabilities.push("read");
      try {
        await updateClassMemberCapabilitiesApi(classId, form.dataset.memberPermissions, capabilities);
        toast("Member permissions updated.");
        await renderClassPeople({ root, klass });
      } catch (error) {
        toast(`Could not update permissions: ${error.message}`, { tone: "error" });
      }
    });
  });

  root.querySelectorAll("[data-remove-member]").forEach((button) => button.addEventListener("click", async () => {
    const member = members.find((item) => String(item.user_id) === button.dataset.removeMember);
    const name = member?.display_name || member?.username || "this member";
    const confirmed = await openDialog({ title: `Remove ${name}?`, message: "They will immediately lose access to this class and its shared materials.", confirmLabel: "Remove member", danger: true });
    if (!confirmed) return;
    try {
      await removeClassMemberApi(classId, button.dataset.removeMember);
      toast(`${name} was removed from the class.`);
      await renderClassPeople({ root, klass });
    } catch (error) {
      toast(`Could not remove member: ${error.message}`, { tone: "error" });
    }
  }));

  root.querySelector("#create-class-invitation")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const capabilities = data.getAll("invitation-caps");
    if (!capabilities.includes("read")) capabilities.push("read");
    const status = form.querySelector("#invitation-status");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = "Creating invitation…";
    try {
      const invitation = await createClassInvitationApi(classId, {
        expiresInDays: Number(data.get("expires_in_days")),
        maxUses: Number(data.get("max_uses")),
        capabilities
      });
      await renderClassPeople({ root, klass });
      const result = root.querySelector("#new-invitation-code");
      const refreshedStatus = root.querySelector("#invitation-status");
      result.hidden = false;
      result.innerHTML = `<div><strong>Copy this invitation code now</strong><code>${escapeHtml(invitation.code)}</code></div><button class="btn btn-secondary btn-small" type="button" data-copy-invitation>Copy code</button>`;
      result.querySelector("[data-copy-invitation]").addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(invitation.code);
          refreshedStatus.textContent = "Invitation code copied.";
        } catch {
          refreshedStatus.textContent = "Select and copy the code above.";
        }
      });
      refreshedStatus.textContent = "Invitation created. The code is shown only here.";
    } catch (error) {
      status.textContent = error.message || "Could not create this invitation.";
      status.dataset.tone = "bad";
    } finally {
      button.disabled = false;
    }
  });

  root.querySelectorAll("[data-revoke-invitation]").forEach((button) => button.addEventListener("click", async () => {
    try {
      await revokeClassInvitationApi(classId, button.dataset.revokeInvitation);
      toast("Invitation revoked.");
      await renderClassPeople({ root, klass });
    } catch (error) {
      toast(`Could not revoke invitation: ${error.message}`, { tone: "error" });
    }
  }));

  root.querySelector("#leave-class-from-people")?.addEventListener("click", async () => {
    const confirmed = await openDialog({ title: `Leave ${klass.name}?`, message: "You will immediately lose access to shared class materials. Your personal study materials remain yours.", confirmLabel: "Leave class", danger: true });
    if (!confirmed) return;
    try {
      await leaveClassApi(classId);
      await syncClassesFromApi();
      toast(`You left ${klass.name}.`);
      window.location.hash = "#/classes";
    } catch (error) {
      toast(`Could not leave this class: ${error.message}`, { tone: "error" });
    }
  });
}

function formatClassDate(value) {
  if (!value) return "an unknown date";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
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
          ${klass
            ? `<button id="tutor-sources" class="pill no-dot pill-button" type="button" aria-haspopup="dialog">${icon("book", { size: 14 })} Using <strong>${escapeHtml(klass.name)}</strong> · <span id="tutor-sources-summary"></span></button>`
            : `<span class="pill no-dot">${icon("book", { size: 14 })} Using <strong>no class</strong></span>`}
          <div class="stage-controls">
            <button id="share-screen" class="btn btn-primary" type="button" aria-label="Share screen" title="Share screen">${icon("monitor", { size: 18 })}<span class="btn-label">Share screen</span></button>
            ${klass ? `<button id="open-material" class="btn btn-secondary" type="button" aria-haspopup="dialog" aria-label="Open a class material on the stage" title="Open a class material on the stage">${icon("file", { size: 18 })}<span class="btn-label">Open material</span></button>` : ""}
            <button id="pause-screen" class="btn btn-secondary" type="button" disabled aria-label="Pause screen sharing" title="Pause screen sharing">${icon("pause", { size: 18 })}<span class="btn-label">Pause</span></button>
            <button id="end-session" class="btn btn-danger-ghost" type="button" disabled aria-label="End session" title="End session">${icon("stop", { size: 18 })}<span class="btn-label">End</span></button>
            <button id="open-tutor-settings" class="btn btn-secondary" type="button" aria-label="Tutor settings" title="Tutor settings" aria-haspopup="dialog" aria-controls="tutor-settings" aria-expanded="false">${icon("settings", { size: 18 })}<span class="btn-label">Settings</span></button>
          </div>
        </div>
        <p class="tutor-privacy-note">${icon("monitor", { size: 16 })}<span>${klass ? "Screen or material frames" : "Screen frames"} and enabled microphone audio pass through Jot's server for tutoring. They are not saved.</span></p>
        <div class="screen-stage">
          <video id="screen-preview" autoplay muted playsinline aria-label="${klass ? "Stage preview" : "Shared screen preview"}"></video>
          <svg id="annotation-layer" preserveAspectRatio="xMidYMid meet" aria-hidden="true"></svg>
          <div class="empty-stage">
            <span class="empty-icon">${icon("monitor", { size: 30 })}</span>
            <strong>${klass ? "Share a screen or open a material" : "Share a screen to start"}</strong>
            <span>${klass
              ? "Share a window, tab, or display, or show one of this class's uploads. The tutor sees only what is on the stage, and you can pause anytime."
              : "Pick a window, tab, or display. The tutor sees only what you share, and you can pause anytime."}</span>
          </div>
        </div>
        ${klass ? `
        <div class="material-bar" id="material-bar" hidden>
          <span class="material-name">${icon("file", { size: 16 })}<span id="material-name"></span></span>
          <div class="material-pager">
            <button id="material-prev" class="btn btn-ghost btn-icon" type="button" aria-label="Previous page" title="Previous page">${icon("arrowLeft", { size: 18 })}</button>
            <span id="material-page" aria-live="polite"></span>
            <button id="material-next" class="btn btn-ghost btn-icon" type="button" aria-label="Next page" title="Next page">${icon("arrowRight", { size: 18 })}</button>
          </div>
          <button id="material-close" class="btn btn-ghost" type="button">${icon("x", { size: 16 })} Close</button>
        </div>` : ""}
        <div class="annotation-bar">
          <ul id="annotation-list" aria-label="Marks on screen"></ul>
          <button id="clear-marks" class="btn btn-ghost" type="button">${icon("eraser", { size: 18 })} Clear marks</button>
        </div>
      </div>
      <aside class="panel conversation-panel" aria-label="Tutor conversation">
        <div id="jot-buddy"></div>
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
          <button type="button" id="mic-toggle" class="btn btn-secondary btn-icon" aria-pressed="false" aria-label="Turn microphone on" aria-describedby="mic-description" title="Turn microphone on" data-mic="off" data-icon="micOff">${icon("micOff", { size: 20 })}</button>
          <button type="submit" class="btn btn-primary btn-icon" aria-label="Send" title="Send question">${icon("send", { size: 20 })}</button>
        </form>
      </aside>
    </section>
    <dialog id="tutor-settings" class="dialog tutor-settings-dialog" aria-labelledby="tutor-settings-title">
      <div class="dialog-body">
        <div class="tutor-settings-heading">
          <h2 id="tutor-settings-title">Tutor settings</h2>
          <button id="close-tutor-settings" class="btn btn-ghost btn-icon" type="button" aria-label="Close tutor settings" autofocus>${icon("x", { size: 20 })}</button>
        </div>
        <section class="coaching-controls" aria-label="Tutoring preferences">
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
        </section>
        ${tutorMemoryMarkup}
        <section class="tutor-settings-restart" aria-label="Current conversation">
          <button id="reset-tutor-memory" class="btn btn-secondary" type="button" disabled aria-describedby="reset-memory-description">${icon("refresh", { size: 16 })} Restart conversation</button>
          <p id="reset-memory-description" class="tutor-memory-hint">Start fresh without previous conversation or saved recaps. Your screen stays shared; the microphone turns off. Saved recaps remain available next time.</p>
        </section>
        <div class="dialog-actions">
          <button id="done-tutor-settings" class="btn btn-primary" type="button">Done</button>
        </div>
      </div>
    </dialog>
  `;

  if (container) {
    container.innerHTML = content;
  } else {
    shell(`
      <div class="page-heading tutor-heading">
        <div>
          <h1>Quick tutor</h1>
          <p class="muted">Get help with anything on your screen.</p>
          <p class="tutor-scope-note">${icon("book", { size: 16 })}<span>${currentUser
            ? `Not using any class materials. <a href="#/classes">Open a class</a> to tutor from your notes.`
            : `Not using any class materials. <a href="#/login">Log in</a> to tutor from your own class notes.`}</span></p>
        </div>
      </div>
      ${content}
    `, { section: "tutor" });
  }

  syncTutorControls();

  // Check if a question was queued from quiz player ("Ask tutor about this")
  const queuedQuestion = readQuizTutorContext(sessionStorage.getItem("tutor_initial_question"));
  if (queuedQuestion) {
    const questionInput = document.querySelector("#question-input");
    if (questionInput) {
      questionInput.value = queuedQuestion.text;
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
  // A class upload shown on the stage in place of a shared screen.
  let material = null;
  let selectedSources = klass ? loadTutorSources(klass) : null;
  if (klass && queuedQuestion?.uploadIds.length) {
    const quizSources = klass.uploads.filter((upload) => queuedQuestion.uploadIds.includes(upload.backendId || upload.id)).map((upload) => upload.id);
    if (quizSources.length) selectedSources = quizSources;
  }
  const materialBar = document.querySelector("#material-bar");
  const settingsDialog = document.querySelector("#tutor-settings");
  const settingsTrigger = document.querySelector("#open-tutor-settings");
  settingsTrigger.addEventListener("click", () => {
    if (disposed || settingsDialog.open) return;
    syncTutorControls();
    settingsTrigger.setAttribute("aria-expanded", "true");
    settingsDialog.showModal();
  });
  settingsDialog.addEventListener("close", () => {
    settingsTrigger.setAttribute("aria-expanded", String(settingsDialog.open));
    if (!disposed && !settingsDialog.open) settingsTrigger.focus({ preventScroll: true });
  });
  document.querySelector("#close-tutor-settings").addEventListener("click", () => settingsDialog.close());
  document.querySelector("#done-tutor-settings").addEventListener("click", () => settingsDialog.close());

  function currentClass() {
    return klass ? getClassById(klass.id) || klass : null;
  }

  const memoryPanel = new TutorMemoryPanel({
    root: document.querySelector(".tutor-saved-memory"),
    signedIn: !!currentUser,
    getClassId: () => klass ? getBackendClassId(currentClass()) || undefined : null,
    onChange: () => { if (!disposed) syncTutorControls(); }
  });
  tutorMemoryPanel = memoryPanel;
  void memoryPanel.refresh();

  function refreshSourcesSummary() {
    const summary = document.querySelector("#tutor-sources-summary");
    if (summary) summary.textContent = sourcesSummary(currentClass(), selectedSources);
  }

  function updateMaterialBar() {
    if (!materialBar) return;
    materialBar.hidden = !material;
    if (!material) return;
    document.querySelector("#material-name").textContent = material.name;
    document.querySelector("#material-page").textContent = `Page ${material.pageIndex + 1} of ${material.pageCount}`;
    document.querySelector("#material-prev").disabled = material.pageIndex === 0;
    document.querySelector("#material-next").disabled = material.pageIndex >= material.pageCount - 1;
  }

  function closeMaterial() {
    material?.close();
    material = null;
    updateMaterialBar();
  }

  function stopStage() {
    capture.stop();
    closeMaterial();
  }

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
        stopStage();
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
    onMemoryState: (memory) => { if (!disposed) memoryPanel.report("loaded", memory.loaded_count, memory.enabled); },
    onAudioData: (data, mimeType) => {
      audioController?.playPcm(data, mimeType);
      buddy?.audioArrived();
    },
    onAudioInterrupted: () => {
      audioController?.stopPlayback();
      buddy?.interrupted();
      annotations.clear();
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
      closeMaterial();
      live.pauseScreen();
      annotations.clear();
      emptyStage.hidden = false;
      setStatus({ session: "screen_paused", screen: "paused" });
    },
    onFrame: (frame) => {
      annotations.onFrame(frame.frame_id);
      live.sendFrame(frame);
    }
  });
  const annotations = new AnnotationController({
    layer: document.querySelector("#annotation-layer"),
    list: document.querySelector("#annotation-list"),
    video,
    getFrames: () => capture.getFrames(),
    getCurrentContent: () => capture.getCurrentContent()
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

  function stopSession({ saveMemory = false } = {}) {
    sessionRequestId += 1;
    classPreparation?.abort();
    classPreparation = null;
    stopStage();
    void audioController.close().catch(() => {});
    let finished;
    if (saveMemory) finished = live.finish();
    else { live.close(); finished = Promise.resolve("disabled"); }
    annotations.clear();
    emptyStage.hidden = false;
    return finished;
  }

  disposeTutor = () => {
    disposed = true;
    if (settingsDialog.open) settingsDialog.close();
    memoryPanel.dispose();
    if (tutorMemoryPanel === memoryPanel) tutorMemoryPanel = null;
    buddy?.dispose();
    stopSession();
    annotations.destroy();
    Object.assign(state, {
      session: "idle", connection: "offline", screen: "not sharing",
      mic: "off", micDevice: "", tutorMode: "conversation", messages: []
    });
    disposeTutor = null;
  };

  // Put a screen share or a material on the stage, connecting first if needed.
  // `openStream` attaches its stream to the capture and resolves to it, or to null.
  async function startStage(openStream, screen) {
    const requestId = ++sessionRequestId;
    const previous = { session: state.session, screen: state.screen };
    try {
      setStatus({ session: "selecting_screen", screen: "selecting" });
      const [stream] = await Promise.all([openStream(requestId), audioController.preparePlayback()]);
      if (disposed || requestId !== sessionRequestId) return;
      if (!stream) {
        setStatus(previous);
        return;
      }
      emptyStage.hidden = true;
      annotations.clear();
      setStatus({ session: "connecting", screen });
      if (state.connection !== "connected") {
        if (klass) {
          classPreparation = new AbortController();
          // The material on the stage is always part of the tutor's context.
          let chosen = selectedSources;
          if (chosen && material && !chosen.includes(material.upload.id)) chosen = [...chosen, material.upload.id];
          const prepared = await prepareTutorSources(currentClass(), { selectedUploadIds: chosen, signal: classPreparation.signal });
          if (disposed || requestId !== sessionRequestId) return;
          classPreparation = null;
          live.classId = prepared.classId;
          live.uploadIds = prepared.uploadIds;
          void memoryPanel.refresh();
        }
        await live.connect({ mode: state.tutorMode, language: state.tutorLanguage });
      }
      if (disposed || requestId !== sessionRequestId) return;
      setStatus({ session: "active" });
    } catch (error) {
      if (disposed || requestId !== sessionRequestId) return;
      // Cancelling the screen picker while something is already on stage keeps the session going.
      if (error?.name === "NotAllowedError" && capture.stream && state.connection === "connected") {
        setStatus(previous);
        return;
      }
      stopSession();
      setStatus({ session: "error", screen: "not sharing", connection: "offline" });
      toast(error.message, { tone: "error" });
    }
  }

  document.querySelector("#share-screen").addEventListener("click", () => startStage(async () => {
    const stream = await capture.start();
    // The screen replaced any material; capture.start already stopped its stream.
    if (stream) closeMaterial();
    return stream;
  }, "sharing"));

  document.querySelector("#open-material")?.addEventListener("click", async () => {
    const upload = await chooseMaterial(currentClass());
    if (!upload || disposed) return;
    await startStage(async (requestId) => {
      const stage = new MaterialStage(upload);
      try {
        const stream = await stage.open();
        if (disposed || requestId !== sessionRequestId) {
          stage.close();
          return null;
        }
        const replaced = material;
        material = stage;
        capture.attach(stream);
        replaced?.close();
        updateMaterialBar();
        return stream;
      } catch (error) {
        stage.close();
        // A material that cannot be shown leaves whatever is on stage in place.
        if (capture.stream) {
          toast(error.message, { tone: "error" });
          return null;
        }
        throw error;
      }
    }, "material");
  });

  async function turnPage(step) {
    if (!material) return;
    const next = material.pageIndex + step;
    if (next < 0 || next >= material.pageCount) return;
    annotations.clear();
    const shown = material;
    // showPage sets the new page number at once; large PDF pages can take a moment to draw.
    const rendering = shown.showPage(next);
    updateMaterialBar();
    try {
      await rendering;
    } catch (error) {
      toast(`Could not show that page: ${error.message}`, { tone: "error" });
    }
    if (material !== shown) return;
    // Send the new page right away instead of waiting for the next sample.
    window.setTimeout(() => capture.sampleFrame(), 200);
  }

  document.querySelector("#material-prev")?.addEventListener("click", () => turnPage(-1));
  document.querySelector("#material-next")?.addEventListener("click", () => turnPage(1));

  function pauseStage() {
    stopStage();
    live.pauseScreen();
    annotations.clear();
    emptyStage.hidden = false;
    setStatus({ session: "screen_paused", screen: "paused" });
  }

  document.querySelector("#pause-screen").addEventListener("click", pauseStage);
  document.querySelector("#material-close")?.addEventListener("click", pauseStage);

  document.querySelector("#tutor-sources")?.addEventListener("click", async () => {
    const klassNow = currentClass();
    const next = await chooseTutorSources(klassNow, selectedSources);
    if (next === undefined || disposed) return;
    selectedSources = next;
    saveTutorSources(klassNow, next);
    refreshSourcesSummary();
  });
  refreshSourcesSummary();

  document.querySelector("#end-session").addEventListener("click", async () => {
    if (state.session === "ending") return;
    const remember = live.memoryEnabled;
    const finished = stopSession({ saveMemory: true });
    const requestId = sessionRequestId;
    setStatus({ session: "ending", screen: "not sharing", connection: "closing" });
    if (remember) memoryPanel.report("saving");
    const memoryStatus = await finished;
    if (disposed || requestId !== sessionRequestId) return;
    setStatus({ session: "ended", screen: "not sharing", connection: "offline" });
    memoryPanel.report(remember ? memoryStatus : "disabled");
    void memoryPanel.refresh();
  });

  document.querySelector("#clear-marks").addEventListener("click", () => annotations.clear());
  document.querySelector("#reset-tutor-memory").addEventListener("click", async () => {
    if (state.connection !== "connected" || !["active", "screen_paused"].includes(state.session)) return;
    settingsDialog.close();
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
      await live.connect({ mode: state.tutorMode, language: state.tutorLanguage, useSavedMemory: false });
      if (disposed || requestId !== sessionRequestId) return;
      if (capture.stream) {
        capture.sampleFrame();
        setStatus({ session: "active", screen: material ? "material" : "sharing" });
      } else {
        live.pauseScreen();
        setStatus({ session: "screen_paused", screen: "paused" });
      }
      toast("Conversation restarted without saved recaps. Turn on the microphone when you're ready to continue.");
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
  const access = getAuthOptions();
  const signupEnabled = access.signupMode !== "closed";
  const approvalRequired = access.signupMode === "approval";
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

  const form = signup && !signupEnabled ? `
    <div class="login-heading">
      <h1>Account requests are closed</h1>
      <p class="muted">This Jot server is not accepting new accounts right now.</p>
      <a class="btn btn-primary" href="#/login">Go to log in</a>
    </div>
  ` : `
    <div class="login-heading">
      <h1>${signup ? (approvalRequired ? "Request an account" : "Join Jot") : "Log in"}</h1>
      <p class="muted">${signup ? (approvalRequired ? "Submit your details for administrator review. You can sign in after your request is approved." : "Create an account to keep your classes, uploads, and study sets.") : "Enter your details to open your classes."}</p>
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
      <button class="btn btn-primary btn-large" type="submit">${signup ? (approvalRequired ? "Request account" : "Create account") : "Log in"}</button>
    </form>
    <p class="login-switch">${signup
      ? 'Already have an account? <a href="#/login">Log in</a>'
      : signupEnabled ? `New to Jot? <a href="#/signup">${approvalRequired ? "Request an account" : "Create an account"}</a>` : ""}</p>
    ${access.guestAccessEnabled ? `<a class="home-guest" href="#/study">Continue as guest</a>` : ""}
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
  if (!formEl) return;
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
    submit.textContent = busy
      ? (signup ? (approvalRequired ? "Sending request..." : "Creating your account...") : "Checking your details...")
      : idleLabel;
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
      const result = signup
        ? await signUp({ username: name, password: password.value, email: formEl.elements.email.value.trim() })
        : { status: "active", user: await signIn(name, password.value) };
      if (signup && result.status === "pending") {
        document.querySelector(".login-card").innerHTML = `
          <div class="login-heading">
            <h1>Request sent</h1>
            <p class="muted" role="status">Your account request is waiting for administrator review. Sign in after the administrator approves it.</p>
          </div>
          <div class="account-actions"><a class="btn btn-primary" href="#/login">Back to log in</a></div>
        `;
        return;
      }
      const user = result.user;
      if (!user) throw new Error("The account could not be created. Please try again.");
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

function renderAdmin() {
  shell(`
    <section class="section-stack admin-page" aria-labelledby="admin-title">
      <div class="page-heading">
        <div>
          <h1 id="admin-title">Admin</h1>
          <p class="muted">Manage Jot's AI provider and review account requests.</p>
        </div>
        <a class="btn btn-secondary" href="#/classes">${icon("book", { size: 18 })} Your classes</a>
      </div>
      <section class="panel ai-provider-panel" aria-labelledby="ai-provider-title">
        <div class="ai-provider-heading">
          <div>
            <h2 id="ai-provider-title">OpenAI-compatible route</h2>
            <p class="muted">Used for quiz generation, study materials, reasoning evaluation, and quiz-review tutoring.</p>
          </div>
          <span id="ai-provider-state" class="pill" role="status">Loading settings…</span>
        </div>
        <p class="admin-provider-note">Enter your provider's API base URL, key, and model. Jot sends study prompts and authorized course excerpts to this provider; opted-in saved tutor recaps also send their temporary transcript for summarization. Review its privacy and retention terms. The key is encrypted on the server and never shown again. Live tutoring (including typed messages during a live session), scanned PDFs and note images, and lecture transcription still use the server's Gemini configuration.</p>
        <form id="ai-provider-form" class="ai-provider-form">
          <label>API base URL
            <input name="base_url" type="url" required maxlength="2048" placeholder="https://api.example.com/v1" autocomplete="url">
          </label>
          <label>Model
            <input name="model" type="text" required maxlength="200" placeholder="model-name" autocomplete="off" spellcheck="false">
          </label>
          <label>API key <span class="muted">(leave blank to keep the saved key)</span>
            <input name="api_key" type="password" maxlength="4096" placeholder="Provider API key" autocomplete="new-password" autocapitalize="off" spellcheck="false">
          </label>
          <label class="ai-provider-clear-key"><input name="clear_api_key" type="checkbox"> Clear the saved API key</label>
          <div class="ai-provider-actions">
            <button class="btn btn-primary" type="submit">Save provider</button>
            <button class="btn btn-secondary" type="button" data-test-ai-provider>Test connection</button>
            <button class="btn btn-secondary" type="button" data-reset-ai-provider>Use environment settings</button>
          </div>
          <p id="ai-provider-message" class="muted" role="status" aria-live="polite"></p>
        </form>
      </section>
      <section class="section-stack" aria-labelledby="signup-requests-title">
        <div>
          <h2 id="signup-requests-title">Signup requests</h2>
          <p class="muted">Review new account requests. Approved accounts can sign in immediately.</p>
        </div>
      <div id="signup-requests" class="admin-request-list" aria-live="polite" aria-busy="true">
        <p class="muted">Loading requests…</p>
      </div>
      </section>
    </section>
  `, { section: "classes" });

  const providerForm = document.querySelector("#ai-provider-form");
  const providerMessage = document.querySelector("#ai-provider-message");
  const providerState = document.querySelector("#ai-provider-state");
  const testProviderButton = providerForm.querySelector("[data-test-ai-provider]");
  const resetProviderButton = providerForm.querySelector("[data-reset-ai-provider]");
  const setProviderBusy = (busy) => {
    providerForm.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  };

  const showProviderState = (summary) => {
    const configured = summary.provider_configured;
    providerState.textContent = configured
      ? `Configured · ${summary.settings_source}`
      : "Not configured";
    if (configured) providerState.dataset.tone = "good";
    else delete providerState.dataset.tone;
    providerForm.elements.base_url.value = summary.base_url || "";
    providerForm.elements.model.value = summary.model || "";
    providerMessage.textContent = summary.api_key_configured
      ? "A key is saved. Enter a new key only if you want to replace it."
      : "No API key is saved yet.";
  };

  const loadProviderSettings = async () => {
    providerState.textContent = "Loading settings…";
    try {
      showProviderState(await getAIProviderSettingsApi());
    } catch (error) {
      providerState.textContent = "Could not load";
      delete providerState.dataset.tone;
      providerMessage.textContent = error.message || "Could not load provider settings.";
    }
  };

  providerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const saveButton = providerForm.querySelector("button[type=submit]");
    setProviderBusy(true);
    saveButton.textContent = "Saving…";
    providerMessage.textContent = "";
    const apiKey = providerForm.elements.api_key.value.trim();
    const clearApiKey = providerForm.elements.clear_api_key.checked;
    try {
      const summary = await updateAIProviderSettingsApi({
        base_url: providerForm.elements.base_url.value.trim(),
        model: providerForm.elements.model.value.trim(),
        ...(apiKey ? { api_key: apiKey } : {}),
        clear_api_key: clearApiKey
      });
      providerForm.elements.api_key.value = "";
      providerForm.elements.clear_api_key.checked = false;
      showProviderState(summary);
      providerMessage.textContent = "Provider settings saved. They apply without a restart.";
    } catch (error) {
      providerMessage.textContent = error.message || "Could not save provider settings.";
    } finally {
      saveButton.disabled = false;
      setProviderBusy(false);
      saveButton.textContent = "Save provider";
    }
  });

  testProviderButton.addEventListener("click", async () => {
    setProviderBusy(true);
    providerMessage.textContent = "Testing the saved provider settings…";
    try {
      const result = await testAIProviderSettingsApi();
      providerMessage.textContent = result.message;
    } catch (error) {
      providerMessage.textContent = error.message || "The provider test failed.";
    } finally {
      setProviderBusy(false);
    }
  });

  resetProviderButton.addEventListener("click", async () => {
    const confirmed = await openDialog({
      title: "Use environment provider settings?",
      message: "This removes the saved administrator override and restores the server's environment settings, if configured.",
      confirmLabel: "Use environment settings"
    });
    if (!confirmed) return;
    setProviderBusy(true);
    providerMessage.textContent = "Restoring environment settings…";
    try {
      const summary = await resetAIProviderSettingsApi();
      providerForm.elements.api_key.value = "";
      providerForm.elements.clear_api_key.checked = false;
      showProviderState(summary);
      providerMessage.textContent = summary.provider_configured
        ? "Environment provider settings are active."
        : "The administrator override was removed. No provider is configured.";
    } catch (error) {
      providerMessage.textContent = error.message || "Could not restore environment settings.";
    } finally {
      setProviderBusy(false);
    }
  });

  providerForm.elements.clear_api_key.addEventListener("change", (event) => {
    if (event.currentTarget.checked) providerForm.elements.api_key.value = "";
  });

  const list = document.querySelector("#signup-requests");
  const renderRequests = (requests) => {
    list.setAttribute("aria-busy", "false");
    if (!requests.length) {
      list.innerHTML = `<div class="panel empty-state"><strong>No pending requests</strong><span>New account requests will appear here.</span></div>`;
      return;
    }
    list.innerHTML = requests.map((request) => {
      const date = new Date(request.created_at);
      const createdAt = Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString();
      return `
        <article class="panel admin-request" data-request-id="${escapeHtml(request.id)}">
          <div class="admin-request-details">
            <h2>${escapeHtml(request.display_name || request.username)}</h2>
            <p><strong>@${escapeHtml(request.username)}</strong>${request.email ? ` · ${escapeHtml(request.email)}` : ""}</p>
            <p class="muted">Requested ${escapeHtml(createdAt)}</p>
          </div>
          <div class="admin-request-actions">
            <button class="btn btn-primary" type="button" data-decision="approve">Approve</button>
            <button class="btn btn-danger-ghost" type="button" data-decision="reject">Reject</button>
          </div>
        </article>`;
    }).join("");
  };

  const load = async () => {
    list.setAttribute("aria-busy", "true");
    list.innerHTML = `<p class="muted">Loading requests…</p>`;
    try {
      const result = await getSignupRequestsApi();
      renderRequests(result.requests || []);
    } catch (error) {
      list.setAttribute("aria-busy", "false");
      list.innerHTML = `<div class="panel empty-state"><strong>Could not load signup requests</strong><span>${escapeHtml(error.message || "Please try again.")}</span><button class="btn btn-secondary" type="button" data-reload-requests>Try again</button></div>`;
    }
  };

  list.addEventListener("click", async (event) => {
    if (event.target.closest("[data-reload-requests]")) {
      await load();
      return;
    }
    const button = event.target.closest("[data-decision]");
    const card = button?.closest("[data-request-id]");
    if (!button || !card) return;

    const approve = button.dataset.decision === "approve";
    if (!approve) {
      const confirmed = await openDialog({
        title: "Reject this account request?",
        message: `The request from @${card.querySelector("strong").textContent.slice(1)} will be closed.`,
        confirmLabel: "Reject request",
        danger: true
      });
      if (!confirmed) return;
    }
    const buttons = card.querySelectorAll("button");
    buttons.forEach((item) => { item.disabled = true; });
    button.textContent = approve ? "Approving…" : "Rejecting…";
    try {
      const action = approve ? approveSignupRequestApi : rejectSignupRequestApi;
      const result = await action(card.dataset.requestId);
      toast(approve ? `Approved @${result.username}.` : `Rejected @${result.username}.`);
      await load();
    } catch (error) {
      toast(error.message || "Could not review this request.", { tone: "error" });
      buttons.forEach((item) => { item.disabled = false; });
      button.textContent = approve ? "Approve" : "Reject";
    }
  });

  loadProviderSettings();
  load();
}

// Who may open what. The server enforces the same rules; these only keep the UI honest.
const requireUser = (render, notice = "Log in to see your classes.") => (params) =>
  currentUser ? render(params) : redirect("#/login", notice);
const requireGuest = (render) => (params) =>
  currentUser
    ? redirect("#/classes")
    : getAuthOptions().guestAccessEnabled
      ? render(params)
      : redirect("#/login", "Guest pages are disabled on this Jot server.");
const requireTutorAccess = () =>
  currentUser || getAuthOptions().guestAccessEnabled
    ? renderTutor()
    : redirect("#/login", "Sign in to use Jot on this server.");
const requireAdmin = (render) => (params) =>
  !currentUser
    ? redirect("#/login", "Log in to review signup requests.")
    : currentUser.is_admin
      ? render(params)
      : redirect("#/classes", "Administrator access is required.");

const router = createRouter(
  [
    { path: "/home", render: renderHome },
    { path: "/study", render: requireGuest(renderGuestWorkspace) },
    { path: "/guest", render: requireGuest(renderGuestWorkspace) },
    { path: "/login", render: () => renderLogin({ mode: "signin" }) },
    { path: "/signup", render: () => renderLogin({ mode: "signup" }) },
    { path: "/classes", render: requireUser(renderClasses) },
    { path: "/classes/:id/:tab", render: requireUser(renderClassTab) },
    { path: "/progress", render: requireUser(renderLearningProgressPage) },
    { path: "/account", render: requireUser(renderAccount, "Log in to see your account.") },
    { path: "/admin", render: requireAdmin(renderAdmin) },
    { path: "/tutor", render: requireTutorAccess },
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
