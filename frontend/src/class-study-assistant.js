import {
  deleteClassStudyAssistantEventApi,
  getClassStudyAssistantEventsApi,
  sendClassStudyAssistantMessageApi
} from "./api.js";
import { bindSourceCitations, sourceCitationHtml } from "./source-citations.js";
import { renderStudyGuideText } from "./study-guide/text-renderer.js";
import { icon } from "./icons.js";
import { escapeHtml } from "./ui.js";

const MAX_MESSAGE_LENGTH = 2_000;
const MAX_HISTORY = 8;

function referenceForDisplay(reference) {
  return {
    ...reference,
    name: reference.label || "Course source",
    ...(reference.timestamp_s != null ? { timestamp_seconds: reference.timestamp_s } : {})
  };
}

function renderMessage(message, index) {
  const isUser = message.role === "user";
  const references = !isUser && Array.isArray(message.references)
    ? message.references.map(referenceForDisplay)
    : [];
  const generated = Array.isArray(message.generatedMaterials) ? message.generatedMaterials : [];
  const selected = Array.isArray(message.selectedSources) ? message.selectedSources : [];
  return `<article class="class-assistant-message ${isUser ? "is-user" : "is-assistant"}" data-message-index="${index}">
    <strong class="class-assistant-message-speaker">${isUser ? "You" : "Jot"}</strong>
    <div class="class-assistant-message-text">${renderStudyGuideText(message.text)}</div>
    ${references.length ? `<div class="class-assistant-references" role="group" aria-label="Sources for this answer">${references.map(sourceCitationHtml).join("")}</div>` : ""}
    ${message.savedEvent ? `<div class="class-assistant-event-confirmation" role="status">Reminder saved: <strong>${escapeHtml(message.savedEvent.title)}</strong> · ${escapeHtml(formatEventDate(message.savedEvent.event_date))}</div>` : ""}
    ${selected.length ? `<div class="class-assistant-action-note"><strong>Sources selected</strong><div>${selected.map((source) => `<span class="class-assistant-source-chip">${escapeHtml(source.title || "Course source")}</span>`).join("")}</div></div>` : ""}
    ${generated.length ? `<div class="class-assistant-generated" role="status"><strong>Generation started</strong><ul>${generated.map((item) => `<li>${escapeHtml(item.title || item.kind)} <span>${escapeHtml(item.status || "Generating")}</span></li>`).join("")}</ul><a href="#study-saved-materials">Go to saved materials</a></div>` : ""}
    ${message.generationError ? `<p class="class-assistant-error" role="status">${escapeHtml(message.generationError)}</p>` : ""}
  </article>`;
}

function formatEventDate(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.valueOf())
    ? String(value || "date not available")
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function renderEvents(events, loading, failed = false) {
  if (loading) return `<p class="field-hint">Loading your private class reminders…</p>`;
  if (failed) {
    return `<p class="field-hint" role="alert">Couldn’t load your reminders.</p><button class="btn btn-ghost btn-small" type="button" data-retry-class-events>Try again</button>`;
  }
  if (!events.length) return `<p class="field-hint">No reminders yet. Ask Jot to remember an exam, assignment, or deadline.</p>`;
  return `<ul class="class-assistant-event-list">${events.map((event) => `
    <li><div><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(formatEventDate(event.event_date))}</span></div>
      <button class="btn btn-ghost btn-small" type="button" data-delete-class-event="${escapeHtml(event.id)}" aria-label="Delete reminder ${escapeHtml(event.title)}">Remove</button>
    </li>`).join("")}</ul>`;
}

/** Shows the signed-in learner's private reminders in the class Library. */
export function mountClassStudyAssistantReminders({ root, classId }) {
  if (!root || !classId) return () => {};
  let events = [];
  let loading = true;
  let failed = false;
  let disposed = false;
  const controller = new AbortController();

  function render() {
    if (disposed) return;
    root.innerHTML = `<section class="panel class-assistant-reminders-card" aria-labelledby="class-assistant-reminders-title">
      <div class="class-assistant-reminders-heading">
        <div><h2 id="class-assistant-reminders-title" tabindex="-1">My reminders</h2><p class="muted">Private to you · exams, assignments, and deadlines Jot is keeping track of.</p></div>
        ${events.length ? `<span class="class-assistant-reminder-count" aria-label="${events.length} reminders">${events.length}</span>` : ""}
      </div>
      <div data-class-assistant-events aria-live="polite">${renderEvents(events, loading, failed)}</div>
    </section>`;
  }

  async function load() {
    loading = true;
    failed = false;
    render();
    try {
      events = await getClassStudyAssistantEventsApi(classId, { signal: controller.signal });
    } catch {
      if (controller.signal.aborted) return;
      failed = true;
    } finally {
      if (!controller.signal.aborted) {
        loading = false;
        render();
      }
    }
  }

  async function onClick(event) {
    if (event.target.closest("[data-retry-class-events]")) {
      void load();
      return;
    }
    const remove = event.target.closest("[data-delete-class-event]");
    if (!remove || remove.disabled) return;
    remove.disabled = true;
    try {
      await deleteClassStudyAssistantEventApi(classId, remove.dataset.deleteClassEvent, { signal: controller.signal });
      events = events.filter((item) => item.id !== remove.dataset.deleteClassEvent);
      render();
      (root.querySelector("[data-delete-class-event]") || root.querySelector("#class-assistant-reminders-title"))
        ?.focus({ preventScroll: true });
    } catch (error) {
      if (controller.signal.aborted) return;
      remove.disabled = false;
      const status = root.querySelector("[data-class-assistant-events]");
      if (status) status.insertAdjacentHTML("beforeend", `<p class="field-hint" role="alert">${escapeHtml(error?.message || "Could not remove that reminder.")}</p>`);
    }
  }

  function dispose() {
    disposed = true;
    controller.abort();
    root.removeEventListener("click", onClick);
    window.removeEventListener("hashchange", dispose);
  }

  root.addEventListener("click", onClick);
  window.addEventListener("hashchange", dispose, { once: true });
  render();
  void load();
  return dispose;
}

/** Mounts the private class chat. The supplied state is page-memory only. */
export function mountClassStudyAssistant({
  root,
  classId,
  localClassId,
  className,
  state,
  onCreateMaterials
}) {
  if (!root || !classId) return () => {};
  state.messages ||= [];
  state.open = false;
  let busy = false;
  let disposed = false;
  const controllers = new Set();

  function render({ scroll = false } = {}) {
    if (disposed) return;
    const restoreInputFocus = root.contains(document.activeElement)
      && document.activeElement.id === "class-assistant-input";
    const previousScrollTop = root.querySelector("[data-class-assistant-transcript]")?.scrollTop || 0;
    const inputDraft = root.querySelector("#class-assistant-input")?.value || "";
    root.innerHTML = `<div class="class-study-assistant-widget">
      <button class="class-assistant-launcher" type="button" aria-label="Ask Jot about ${escapeHtml(className || "this class")}" aria-expanded="${state.open ? "true" : "false"}" aria-haspopup="dialog" ${state.open ? 'aria-controls="class-assistant-panel"' : ""}>
        ${icon("message", { size: 20 })}<span>Ask Jot</span>
      </button>
      ${state.open ? `<section class="class-assistant-panel" id="class-assistant-panel" role="dialog" aria-modal="false" aria-labelledby="class-assistant-title">
        <header class="class-assistant-heading">
          <div><h2 id="class-assistant-title">Ask Jot</h2><p>${escapeHtml(className || "This class")}</p></div>
          <button class="class-assistant-close" type="button" data-close-assistant aria-label="Close Ask Jot">${icon("x", { size: 18 })}</button>
        </header>
        <div class="class-assistant-transcript" data-class-assistant-transcript role="log" aria-label="Chat with Jot about this class" aria-live="polite" aria-relevant="additions">
          ${state.messages.length ? state.messages.map(renderMessage).join("") : `<div class="class-assistant-empty"><strong>What would you like help with?</strong><div class="class-assistant-suggestions"><button type="button" data-assistant-prompt="How am I doing in this class?">How am I doing?</button><button type="button" data-assistant-prompt="What should I review next?">What should I review next?</button><button type="button" data-assistant-prompt="Create a study guide about ">Create a study guide</button></div></div>`}
          ${busy ? `<p class="class-assistant-pending" role="status">Jot is checking your class context…</p>` : ""}
        </div>
        <form class="class-assistant-form" data-class-assistant-form>
          <label class="visually-hidden" for="class-assistant-input">Message Jot about this class</label>
          <textarea id="class-assistant-input" name="message" rows="2" maxlength="${MAX_MESSAGE_LENGTH}" placeholder="Ask about class, progress, or reminders…" required></textarea>
          <button class="btn btn-primary" type="submit" aria-label="Send message" ${busy ? "disabled" : ""}>${icon("send", { size: 18 })}<span>Send</span></button>
        </form>
        <p class="class-assistant-privacy">Chat stays in this page and clears when you leave. It does not change scores or mastery.</p>
      </section>` : ""}
    </div>`;
    const transcript = root.querySelector("[data-class-assistant-transcript]");
    if (scroll && transcript) transcript.scrollTop = transcript.scrollHeight;
    else if (transcript) transcript.scrollTop = previousScrollTop;
    if (state.open) bindSourceCitations(root, { classId: localClassId });
    const input = root.querySelector("#class-assistant-input");
    if (input) input.value = inputDraft;
    if (restoreInputFocus) input?.focus({ preventScroll: true });
  }

  function appendAssistant(text, extras = {}) {
    state.messages.push({ role: "assistant", text, ...extras });
    state.messages = state.messages.slice(-40);
  }

  async function submitMessage(value) {
    const message = String(value || "").trim();
    if (!message || message.length > MAX_MESSAGE_LENGTH || busy) return;
    const history = state.messages.slice(-MAX_HISTORY).map(({ role, text }) => ({ role, text }));
    state.messages.push({ role: "user", text: message });
    state.messages = state.messages.slice(-40);
    busy = true;
    render({ scroll: true });
    const controller = new AbortController();
    controllers.add(controller);
    const stopOnNavigation = () => controller.abort();
    window.addEventListener("hashchange", stopOnNavigation, { once: true });
    try {
      const result = await sendClassStudyAssistantMessageApi(classId, message, history, { signal: controller.signal });
      const extras = {
        references: Array.isArray(result.references) ? result.references : [],
        savedEvent: result.saved_event || null
      };
      if (result.material_request) {
        const request = result.material_request;
        extras.selectedSources = Array.isArray(request.sources) ? request.sources : [];
        appendAssistant(result.reply, extras);
        render({ scroll: true });
        try {
          extras.generatedMaterials = await onCreateMaterials?.(request, updateGeneratedMaterial) || [];
        } catch (error) {
          extras.generationError = error?.message || "Could not start study-material generation.";
        }
        const lastAssistant = [...state.messages].reverse().find((item) => item.role === "assistant");
        if (lastAssistant) Object.assign(lastAssistant, extras);
      } else {
        appendAssistant(result.reply || "I couldn't prepare a response. Try asking another way.", extras);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        appendAssistant(error?.message || "Jot couldn't answer right now. Please try again.");
      } else {
        appendAssistant("I stopped working on that message when you left the class page.");
      }
    } finally {
      controllers.delete(controller);
      window.removeEventListener("hashchange", stopOnNavigation);
      busy = false;
      render({ scroll: true });
    }
  }

  function updateGeneratedMaterial(update) {
    const assistant = [...state.messages].reverse().find((item) => item.role === "assistant");
    if (!assistant || !update || typeof update !== "object") return;
    const materials = Array.isArray(assistant.generatedMaterials)
      ? assistant.generatedMaterials
      : (assistant.generatedMaterials = []);
    const index = materials.findIndex((item) => item.id && item.id === update.id);
    if (index >= 0) materials[index] = { ...materials[index], ...update };
    else materials.push(update);
    render();
  }

  root.addEventListener("submit", onSubmit);
  root.addEventListener("click", onClick);

  function onSubmit(event) {
    if (!event.target.closest("[data-class-assistant-form]")) return;
    event.preventDefault();
    if (busy) return;
    const input = root.querySelector("#class-assistant-input");
    const value = input?.value;
    if (input) input.value = "";
    void submitMessage(value);
  }

  function onClick(event) {
    if (event.target.closest(".class-assistant-launcher")) {
      state.open = !state.open;
      render();
      if (state.open) root.querySelector("#class-assistant-input")?.focus({ preventScroll: true });
      else root.querySelector(".class-assistant-launcher")?.focus({ preventScroll: true });
      return;
    }
    if (event.target.closest("[data-close-assistant]")) {
      state.open = false;
      render();
      root.querySelector(".class-assistant-launcher")?.focus({ preventScroll: true });
      return;
    }
    const prompt = event.target.closest("[data-assistant-prompt]");
    if (prompt) {
      const input = root.querySelector("#class-assistant-input");
      if (input) {
        input.value = prompt.dataset.assistantPrompt || "";
        input.focus();
        if (input.value.endsWith("about ")) {
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }
      return;
    }
  }

  function onKeydown(event) {
    if (event.key !== "Escape" || !state.open) return;
    event.preventDefault();
    state.open = false;
    render();
    root.querySelector(".class-assistant-launcher")?.focus({ preventScroll: true });
  }

  render();
  root.addEventListener("keydown", onKeydown);
  return () => {
    disposed = true;
    for (const controller of controllers) controller.abort();
    controllers.clear();
    root.removeEventListener("submit", onSubmit);
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKeydown);
  };
}
