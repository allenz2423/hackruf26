import { askStudyTutorApi } from "./api.js";
import { icon } from "./icons.js";
import { renderMathInto } from "./math.js";

const FIRST_QUESTION = "Help me understand my mistake with a hint.";

// Keep complete exchanges so the provider always receives alternating roles.
function recentMessages(messages, nextMessage) {
  const recent = [...messages, { role: "user", text: nextMessage }];
  while (recent.length > 15 || recent.reduce((sum, item) => sum + item.text.length, 0) > 24_000) {
    recent.splice(0, 2);
  }
  return recent;
}

export class StudyTutorPanel {
  constructor(container, trigger, context) {
    this.trigger = trigger;
    this.context = context;
    this.messages = [];
    this.pendingText = "";
    this.pendingNode = null;
    this.busy = false;
    this.disposed = false;
    this.requestId = 0;
    this.controller = null;
    this.events = new AbortController();
    this.root = document.createElement("section");
    this.root.className = "review-tutor";
    this.root.id = `review-tutor-${crypto.randomUUID()}`;
    this.root.hidden = true;
    this.root.setAttribute("aria-label", "Tutor help for this question");
    this.root.innerHTML = `
      <div class="review-tutor-heading">
        <h3>Tutor help</h3>
        <button type="button" class="btn btn-ghost btn-icon" data-close aria-label="Close tutor help">${icon("x", { size: 18 })}</button>
      </div>
      <ol class="review-tutor-messages" role="log" aria-label="Question review conversation" aria-live="polite" aria-relevant="additions"></ol>
      <div class="review-tutor-feedback">
        <p class="review-tutor-status" role="status" aria-live="polite"></p>
        <button type="button" class="btn btn-secondary btn-small" data-retry hidden>Retry reply</button>
      </div>
      <form class="review-tutor-form">
        <label class="field">
          <span>Ask a follow-up</span>
          <textarea rows="2" maxlength="2000" required placeholder="What part is still unclear?" aria-label="Ask the tutor a follow-up"></textarea>
        </label>
        <button type="submit" class="btn btn-primary btn-small">Send</button>
      </form>
    `;
    container.append(this.root);
    this.list = this.root.querySelector("ol");
    this.status = this.root.querySelector(".review-tutor-status");
    this.retry = this.root.querySelector("[data-retry]");
    this.input = this.root.querySelector("textarea");
    this.sendButton = this.root.querySelector("[type=submit]");
    trigger.setAttribute("aria-controls", this.root.id);
    trigger.setAttribute("aria-expanded", "false");
    const options = { signal: this.events.signal };
    this.root.querySelector("[data-close]").addEventListener("click", () => this.close(), options);
    this.retry.addEventListener("click", () => void this.send(this.pendingText, true), options);
    this.root.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      const text = this.input.value.trim();
      if (!text || this.busy || this.disposed) return;
      this.input.value = "";
      void this.send(text);
    }, options);
  }

  toggle() {
    if (this.disposed) return;
    if (!this.root.hidden) return this.close();
    this.root.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.input.focus({ preventScroll: true });
    if (!this.messages.length && !this.pendingText) void this.send(FIRST_QUESTION);
  }

  close() {
    this.requestId += 1;
    this.controller?.abort();
    this.busy = false;
    if (this.pendingText) {
      this.status.textContent = "Reply cancelled. Reopen and retry when ready.";
      this.retry.hidden = false;
    }
    this.sendButton.disabled = false;
    this.root.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.trigger.focus({ preventScroll: true });
  }

  appendMessage(role, text) {
    const item = document.createElement("li");
    item.className = `review-tutor-message review-tutor-message--${role}`;
    const label = document.createElement("strong");
    label.textContent = role === "user" ? "You" : "Tutor";
    const body = document.createElement("div");
    renderMathInto(body, text);
    item.append(label, body);
    this.list.append(item);
    // UI history is also bounded and lives only in this player instance.
    while (this.list.children.length > 30) this.list.firstElementChild.remove();
    this.list.scrollTop = this.list.scrollHeight;
    return item;
  }

  async send(text, retry = false) {
    if (!text || this.busy || this.disposed) return;
    if (!retry) {
      this.pendingNode?.remove();
      this.pendingText = text;
      this.pendingNode = this.appendMessage("user", text);
    }
    const messages = recentMessages(this.messages, this.pendingText);
    const requestId = ++this.requestId;
    this.controller = new AbortController();
    this.busy = true;
    this.sendButton.disabled = true;
    this.retry.hidden = true;
    this.status.dataset.error = "false";
    this.status.textContent = "Tutor is thinking…";
    try {
      const result = await askStudyTutorApi({ ...this.context, messages }, { signal: this.controller.signal });
      if (this.disposed || requestId !== this.requestId) return;
      if (typeof result.reply !== "string" || !result.reply.trim() || result.reply.length > 6000) {
        throw new Error("The tutor did not return a reply. Try again.");
      }
      this.messages = [...messages, { role: "assistant", text: result.reply }];
      // Leave room for the next user turn while retaining complete pairs.
      while (this.messages.length > 14 || this.messages.reduce((sum, item) => sum + item.text.length, 0) > 22_000) {
        this.messages.splice(0, 2);
      }
      this.pendingText = "";
      this.pendingNode = null;
      this.appendMessage("assistant", result.reply);
      this.status.textContent = "Ask a follow-up to work through the next step.";
    } catch (error) {
      if (this.disposed || requestId !== this.requestId) return;
      this.status.textContent = error.message || "The tutor could not reply. Try again.";
      this.status.dataset.error = "true";
      this.retry.hidden = false;
    } finally {
      if (!this.disposed && requestId === this.requestId) {
        this.busy = false;
        this.sendButton.disabled = false;
      }
    }
  }

  dispose() {
    this.disposed = true;
    this.requestId += 1;
    this.controller?.abort();
    this.events.abort();
    this.messages = [];
    this.pendingText = "";
    this.root.remove();
  }
}
