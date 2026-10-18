import { sendGuideChatMessageApi } from "./api.js";
import { typesetMath } from "./math.js";
import { renderStudyGuideText } from "./study-guide/text-renderer.js";
import { bindSourceCitations, sourceCitationHtml } from "./source-citations.js";
import { escapeHtml } from "./ui.js";

const MAX_MESSAGE_CHARS = 2000;
const MAX_HISTORY_MESSAGES = 10;
const REQUEST_HISTORY_MESSAGES = 6;

const QUICK_PROMPTS = [
  { intent: "explain", label: "Explain more", message: "Explain the main idea in simpler terms." },
  { intent: "example", label: "Show an example", message: "Work through a new example of this idea." },
  { intent: "connect", label: "Connect ideas", message: "How does this idea connect to the other ideas in the guide?" },
  { intent: "practice", label: "Quiz me", message: "Give me a fresh question so I can try this idea from memory." }
];

export function normalizeGuideChatReference(reference) {
  if (!reference || typeof reference !== "object" || typeof reference.upload_id !== "string") return null;
  return {
    upload_id: reference.upload_id,
    name: typeof reference.label === "string" ? reference.label : "Course source",
    ...(Number.isFinite(Number(reference.page)) ? { page: Number(reference.page) } : {}),
    ...(Number.isFinite(Number(reference.slide)) ? { slide: Number(reference.slide) } : {}),
    ...(Number.isFinite(Number(reference.timestamp_s)) ? { timestamp_seconds: Number(reference.timestamp_s) } : {})
  };
}

function responseHtml(message) {
  return renderStudyGuideText(String(message || ""));
}

function messageHtml(message, index) {
  const isUser = message.role === "user";
  const contextLabel = message.scope === "guide"
    ? "Whole guide"
    : message.sectionHeading || (Number.isInteger(message.sectionIndex) ? `Section ${message.sectionIndex + 1}` : "Study guide");
  return `<article class="guide-chat-message ${isUser ? "is-user" : "is-assistant"}" data-guide-chat-message="${index}">
    <p class="guide-chat-speaker">${isUser ? "You" : "Jot · source-grounded help"} <span class="guide-chat-message-context">${escapeHtml(contextLabel)}</span></p>
    <div class="guide-chat-message-body">${responseHtml(message.text)}</div>
    ${!isUser && message.references?.length ? `<div class="guide-chat-evidence" aria-label="Course sources">${message.references.map(sourceCitationHtml).join("")}</div>` : ""}
    ${!isUser && message.sourceWarnings?.length ? `<div class="guide-chat-caveats" role="note"><strong>Source limits</strong><ul>${message.sourceWarnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul></div>` : ""}
    ${!isUser && message.practiceQuestion ? `<section class="guide-chat-challenge" aria-label="Fresh practice question">
      <h4>Try it from memory</h4><div>${responseHtml(message.practiceQuestion.prompt)}</div>
      <p class="field-hint">Your response stays in this open guide only. This practice chat does not update mastery or review progress.</p>
      <form data-guide-chat-challenge="${index}">
        <label class="field"><span>Your answer</span><textarea name="answer" rows="3" maxlength="${MAX_MESSAGE_CHARS}" required></textarea></label>
        <button class="btn btn-secondary btn-small" type="submit">Get feedback</button>
      </form>
    </section>` : ""}
  </article>`;
}

export function guideChatHistory(messages) {
  return messages.slice(-REQUEST_HISTORY_MESSAGES).map(({ role, text, historyText }) => ({
    role,
    text: String(historyText || text || "").slice(0, 6000)
  }));
}

export function buildGuideChatRequest({ sectionIndex, blockIndex = null, scope = "section", intent = "explain", message, history = [] }) {
  return {
    section_index: sectionIndex,
    ...(Number.isInteger(blockIndex) ? { block_index: blockIndex } : {}),
    scope: scope === "guide" ? "guide" : "section",
    intent: ["explain", "example", "connect", "practice"].includes(intent) ? intent : "explain",
    message: String(message || "").slice(0, MAX_MESSAGE_CHARS),
    history: guideChatHistory(history)
  };
}

export function createGuideChat(host, { material, sectionIndex = 0, sectionHeading = "", classId = null } = {}) {
  if (!host) return null;
  let guideMaterial = material;
  let activeSectionIndex = sectionIndex;
  let activeSectionHeading = sectionHeading;
  let activeBlockIndex = null;
  let activeScope = "section";
  let messages = [];
  let trigger = null;
  let pending = false;
  let destroyed = false;
  let requestController = null;
  let statusText = "";

  host.className = "guide-chat-host";
  host.hidden = true;

  function render() {
    if (destroyed) return;
    host.innerHTML = `<aside class="guide-chat-panel" aria-labelledby="guide-chat-title" aria-describedby="guide-chat-privacy">
      <header class="guide-chat-header">
        <div><p class="eyebrow">Study help</p><h3 id="guide-chat-title">Ask about this guide</h3><p class="guide-chat-context" data-guide-chat-context>${escapeHtml(activeSectionHeading || "Current section")}</p></div>
        <button class="btn btn-ghost btn-icon" type="button" data-guide-chat-close aria-label="Close study help">×</button>
      </header>
      <p id="guide-chat-privacy" class="guide-chat-privacy">Answers use course sources when available. Chat and practice answers are cleared when you close this guide.</p>
      <label class="guide-chat-scope"><span>Ask about</span><select data-guide-chat-scope><option value="section">This section</option><option value="guide">The whole guide</option></select></label>
      <div class="guide-chat-scroll">
        <div class="guide-chat-messages" data-guide-chat-messages aria-live="polite" aria-relevant="additions text">${messages.map(messageHtml).join("")}</div>
        <p class="guide-chat-status" data-guide-chat-status role="status" aria-live="polite">${escapeHtml(statusText)}</p>
      </div>
      <div class="guide-chat-starters" aria-label="Suggested questions">${QUICK_PROMPTS.map((prompt) => `<button class="btn btn-secondary btn-small" type="button" data-guide-chat-intent="${prompt.intent}" data-guide-chat-prompt="${escapeHtml(prompt.message)}">${escapeHtml(prompt.label)}</button>`).join("")}</div>
      <form class="guide-chat-composer" data-guide-chat-form>
        <label class="field"><span class="visually-hidden">Ask a question about your study guide</span><textarea name="message" rows="2" maxlength="${MAX_MESSAGE_CHARS}" placeholder="Ask a question about this guide…" required></textarea></label>
        <button class="btn btn-primary" type="submit">Send</button>
      </form>
      <p class="guide-chat-footer">This help is separate from your saved quiz results and mastery record.</p>
    </aside>`;
    const panel = host.querySelector(".guide-chat-panel");
    const messageBox = host.querySelector("[data-guide-chat-messages]");
    const scope = host.querySelector("[data-guide-chat-scope]");
    scope.value = activeScope;
    host.querySelector("[data-guide-chat-close]")?.addEventListener("click", close);
    scope.addEventListener("change", () => { activeScope = scope.value === "guide" ? "guide" : "section"; });
    host.querySelectorAll("[data-guide-chat-intent]").forEach((button) => button.addEventListener("click", () => {
      if (pending) return;
      void send(button.dataset.guideChatPrompt, button.dataset.guideChatIntent);
    }));
    host.querySelector("[data-guide-chat-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = event.currentTarget.elements.message;
      const prompt = input.value.trim();
      if (!prompt || pending) return;
      input.value = "";
      void send(prompt, "explain");
    });
    host.querySelectorAll("[data-guide-chat-challenge]").forEach((form) => form.addEventListener("submit", (event) => {
      event.preventDefault();
      const answer = event.currentTarget.elements.answer.value.trim();
      if (!answer || pending) return;
      event.currentTarget.elements.answer.value = "";
      const question = messages[Number(event.currentTarget.dataset.guideChatChallenge)];
      void send(`My answer to the practice question: ${answer}`, "practice", question ? {
        sectionIndex: question.sectionIndex,
        sectionHeading: question.sectionHeading,
        scope: question.scope,
        blockIndex: null
      } : undefined);
    }));
    for (const element of host.querySelectorAll(".tex-math")) void typesetMath(element.parentElement || host);
    bindSourceCitations(host, { classId: classId || guideMaterial.classId });
    if (panel) panel.dataset.busy = String(pending);
    const submit = host.querySelector("[data-guide-chat-form] button[type=submit]");
    if (submit) submit.disabled = pending;
    host.querySelectorAll("[data-guide-chat-intent]").forEach((button) => { button.disabled = pending; });
    if (messageBox) messageBox.scrollTop = messageBox.scrollHeight;
  }

  function setTriggerExpanded(expanded) {
    trigger?.setAttribute?.("aria-expanded", String(expanded));
  }

  function open({ sectionIndex: nextIndex = activeSectionIndex, sectionHeading: nextHeading = activeSectionHeading, blockIndex = null, scope = null, sourceTrigger = null, intent = null, message = null } = {}) {
    if (destroyed) return;
    if (sourceTrigger) trigger = sourceTrigger;
    activeSectionIndex = nextIndex;
    activeSectionHeading = nextHeading;
    activeBlockIndex = Number.isInteger(blockIndex) ? blockIndex : null;
    if (scope === "section" || scope === "guide") activeScope = scope;
    host.hidden = false;
    setTriggerExpanded(true);
    render();
    host.querySelector("[data-guide-chat-form] textarea")?.focus({ preventScroll: true });
    if (typeof message === "string" && message.trim()) void send(message, intent || "explain");
  }

  function close() {
    if (destroyed) return;
    requestController?.abort();
    requestController = null;
    pending = false;
    host.hidden = true;
    setTriggerExpanded(false);
    trigger?.focus?.({ preventScroll: true });
  }

  function setContext({ sectionIndex: nextIndex, sectionHeading: nextHeading, material: nextMaterial, sourceTrigger } = {}) {
    if (Number.isInteger(nextIndex)) activeSectionIndex = nextIndex;
    if (typeof nextHeading === "string") activeSectionHeading = nextHeading;
    if (nextMaterial) guideMaterial = nextMaterial;
    if (sourceTrigger) {
      trigger = sourceTrigger;
      setTriggerExpanded(!host.hidden);
    }
    if (!host.hidden) render();
  }

  async function send(text, intent, messageContext = null) {
    if (destroyed || pending || typeof guideMaterial.id !== "string" || !text) return;
    const context = {
      sectionIndex: Number.isInteger(messageContext?.sectionIndex) ? messageContext.sectionIndex : activeSectionIndex,
      sectionHeading: typeof messageContext?.sectionHeading === "string" ? messageContext.sectionHeading : activeSectionHeading,
      scope: messageContext?.scope === "guide" ? "guide" : activeScope,
      blockIndex: Number.isInteger(messageContext?.blockIndex) ? messageContext.blockIndex : activeBlockIndex
    };
    const userMessage = { role: "user", text: text.slice(0, MAX_MESSAGE_CHARS), ...context };
    const history = guideChatHistory(messages);
    messages = [...messages, userMessage].slice(-MAX_HISTORY_MESSAGES);
    pending = true;
    statusText = "Looking through the authorized course sources…";
    render();
    requestController = new AbortController();
    try {
      const request = buildGuideChatRequest({
        sectionIndex: context.sectionIndex,
        blockIndex: context.blockIndex,
        scope: context.scope,
        intent,
        message: userMessage.text,
        history
      });
      const response = await sendGuideChatMessageApi(guideMaterial.id, request, { signal: requestController.signal });
      if (destroyed) return;
      const reply = typeof response.reply === "string" ? response.reply.slice(0, 6000) : "";
      if (!reply) throw new Error("The study helper returned an empty answer. Try asking again.");
      const references = Array.isArray(response.references)
        ? response.references.map(normalizeGuideChatReference).filter(Boolean).slice(0, 8)
        : [];
      const sourceWarnings = Array.isArray(response.source_warnings)
        ? response.source_warnings.filter((warning) => typeof warning === "string").slice(0, 4)
        : [];
      if (!references.length && !sourceWarnings.length) {
        sourceWarnings.push("No course-source citation was returned for this answer. Check it against the guide before relying on it.");
      }
      const practiceQuestion = intent === "practice" && response.practice_question
        && typeof response.practice_question.prompt === "string"
        ? { prompt: response.practice_question.prompt.slice(0, 3000) }
        : null;
      messages = [...messages, {
        role: "assistant",
        text: reply,
        historyText: practiceQuestion ? `${reply}\n\nFresh practice question: ${practiceQuestion.prompt}` : reply,
        references,
        sourceWarnings,
        practiceQuestion,
        ...context
      }].slice(-MAX_HISTORY_MESSAGES);
      statusText = sourceWarnings.length ? "Answer received with a source limitation." : "Answer ready.";
      activeBlockIndex = null;
    } catch (error) {
      if (destroyed || error?.name === "AbortError") return;
      const message = error.message || "Study help is temporarily unavailable. Try again.";
      messages = [...messages, { role: "assistant", text: message, references: [], sourceWarnings: [], ...context }].slice(-MAX_HISTORY_MESSAGES);
      statusText = "The request did not complete. Your course guide is still available.";
    } finally {
      requestController = null;
      pending = false;
      if (!destroyed) render();
    }
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    requestController?.abort();
    messages = [];
    host.replaceChildren();
    host.hidden = true;
    setTriggerExpanded(false);
    trigger = null;
  }

  host.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !host.hidden) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  });

  return { open, close, setContext, destroy, get messages() { return [...messages]; }, get isOpen() { return !host.hidden; } };
}
