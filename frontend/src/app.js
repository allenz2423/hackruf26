import "./styles.css";
import { AnnotationController } from "./annotations.js";
import { AudioController } from "./audio.js";
import { getClasses, getClassById, createClass, updateClass, deleteClass } from "./classes.js";
import { GeminiLiveController } from "./gemini-live.js";
import { renderLibraryTab } from "./library.js";
import { createRouter } from "./router.js";
import { ScreenCaptureController } from "./screen-capture.js";
import { renderStudyTab } from "./study.js";

const app = document.querySelector("#app");

const state = {
  session: "idle",
  connection: "offline",
  screen: "not sharing",
  mic: "off",
  messages: []
};

function setStatus(patch) {
  Object.assign(state, patch);
  document.querySelector("[data-session-status]")?.replaceChildren(state.session);
  document.querySelector("[data-connection-status]")?.replaceChildren(state.connection);
  document.querySelector("[data-screen-status]")?.replaceChildren(state.screen);
  document.querySelector("[data-mic-status]")?.replaceChildren(state.mic);
}

function shell(content) {
  app.innerHTML = `
    <header class="app-header">
      <a class="brand" href="#/classes">Screen Tutor</a>
      <nav aria-label="Primary navigation">
        <a href="#/classes">Classes</a>
        <a href="#/tutor">Tutor</a>
      </nav>
      <div class="status-strip" aria-label="Session status">
        <span>Session <strong data-session-status>${state.session}</strong></span>
        <span>Live <strong data-connection-status>${state.connection}</strong></span>
        <span>Screen <strong data-screen-status>${state.screen}</strong></span>
        <span>Mic <strong data-mic-status>${state.mic}</strong></span>
      </div>
    </header>
    <main>${content}</main>
  `;
}

function renderClasses() {
  const classes = getClasses();
  shell(`
    <section class="classes-page">
      <div class="page-heading">
        <h1>Classes</h1>
        <button id="new-class" type="button">New class</button>
      </div>
      <div class="class-grid" id="class-grid"></div>
    </section>
  `);

  const grid = document.querySelector("#class-grid");
  for (const klass of classes) {
    const card = document.createElement("article");
    card.className = "class-card";
    card.innerHTML = `
      <a href="#/classes/${klass.id}/library" class="class-card-main">
        <span class="class-icon" style="--class-color: ${klass.color}">${klass.icon}</span>
        <strong>${klass.name}</strong>
        <span>${klass.uploads.length} uploads</span>
      </a>
      <div class="card-actions">
        <button type="button" data-rename="${klass.id}">Rename</button>
        <button type="button" data-delete="${klass.id}">Delete</button>
      </div>
    `;
    grid.append(card);
  }

  document.querySelector("#new-class").addEventListener("click", () => {
    const name = window.prompt("Class name");
    if (!name?.trim()) return;
    const nextClass = createClass(name.trim());
    window.location.hash = `#/classes/${nextClass.id}/library`;
  });

  grid.addEventListener("click", (event) => {
    const renameId = event.target.dataset.rename;
    const deleteId = event.target.dataset.delete;

    if (renameId) {
      const klass = getClassById(renameId);
      const name = window.prompt("New class name", klass.name);
      if (name?.trim()) {
        updateClass(renameId, { name: name.trim(), icon: name.trim().charAt(0).toUpperCase() });
        renderClasses();
      }
    }

    if (deleteId && window.confirm("Delete this class and its local uploads?")) {
      deleteClass(deleteId);
      renderClasses();
    }
  });
}

function renderClassTab({ id, tab }) {
  const klass = getClassById(id);
  if (!klass) {
    window.location.hash = "#/classes";
    return;
  }

  shell(`
    <section class="class-page">
      <div class="class-header">
        <h1>${klass.name}</h1>
        <div class="tabs" role="tablist">
          <a class="${tab === "library" ? "is-active" : ""}" href="#/classes/${klass.id}/library">Library</a>
          <a class="${tab === "study" ? "is-active" : ""}" href="#/classes/${klass.id}/study">Study</a>
          <a class="${tab === "tutor" ? "is-active" : ""}" href="#/classes/${klass.id}/tutor">Tutor</a>
        </div>
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
  const selectedClass = klass ? klass.name : "No class";
  const container = klass ? document.querySelector("#class-tab") : null;
  const content = `
    <section class="tutor-workspace">
      <div class="stage-panel">
        <div class="stage-topline">
          <span>${selectedClass}</span>
          <div>
            <button id="share-screen" type="button">Share screen</button>
            <button id="pause-screen" type="button">Pause</button>
            <button id="end-session" type="button">End</button>
          </div>
        </div>
        <div class="screen-stage">
          <video id="screen-preview" autoplay muted playsinline></video>
          <svg id="annotation-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg>
          <div class="empty-stage">Share a screen to start tutoring</div>
        </div>
        <div class="annotation-bar">
          <button id="clear-marks" type="button">Clear marks</button>
          <ul id="annotation-list" aria-label="Active annotations"></ul>
        </div>
      </div>
      <aside class="conversation-panel">
        <ol id="conversation-list" class="conversation-list"></ol>
        <form id="composer" class="composer">
          <input id="question-input" name="question" autocomplete="off" placeholder="Ask a question" />
          <button type="button" id="mic-toggle">Mic</button>
          <button type="submit">Send</button>
        </form>
      </aside>
    </section>
  `;

  if (container) {
    container.innerHTML = content;
  } else {
    shell(content);
  }

  const video = document.querySelector("#screen-preview");
  const emptyStage = document.querySelector(".empty-stage");
  const live = new GeminiLiveController({
    onMessage: (message) => appendMessage(message.role, message.text),
    onStatusChange: (connection) => setStatus({ connection }),
    onAnnotationRequest: (args) => annotations.addMany(args?.items || args?.annotations || [])
  });
  const capture = new ScreenCaptureController({
    video,
    onEnded: () => setStatus({ session: "screen_paused", screen: "paused" }),
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
      setStatus({ session: "error", screen: "not sharing", connection: "offline" });
      appendMessage("tutor", error.message);
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
      appendMessage("tutor", error.message);
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
