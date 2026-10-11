import { getTutorMemoryApi, setTutorMemoryApi, clearTutorMemoryApi } from "./api.js";
import { escapeHtml, openDialog, toast } from "./ui.js";

export const tutorMemoryMarkup = `
  <section class="tutor-saved-memory" aria-label="Saved session recaps">
    <label class="check-steps-control" for="remember-sessions">
      <input id="remember-sessions" type="checkbox" disabled aria-describedby="saved-memory-description" />
      <span>Remember sessions</span>
    </label>
    <p id="saved-memory-description" class="tutor-memory-hint" role="status" aria-live="polite"></p>
    <div class="saved-memory-actions">
      <details id="saved-recaps" hidden>
        <summary>Recent session recaps</summary>
        <ul id="saved-recap-list" class="saved-recap-list"></ul>
      </details>
      <button id="clear-saved-memory" class="btn btn-ghost" type="button" disabled>Clear saved recaps</button>
      <button id="retry-saved-memory" class="btn btn-ghost" type="button" hidden>Retry</button>
    </div>
  </section>`;

function recapHtml(item) {
  const content = item.content || {};
  const date = new Date(item.created_at);
  const list = (label, values) => Array.isArray(values) && values.length
    ? `<p><strong>${label}</strong></p><ul>${values.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`
    : "";
  return `<li>
    <time datetime="${escapeHtml(item.created_at)}">${escapeHtml(Number.isNaN(date.getTime()) ? "Previous session" : date.toLocaleString())}</time>
    <p>${escapeHtml(content.summary || "")}</p>
    ${list("Topics", content.topics)}
    ${list("Observed progress", content.observed_progress)}
    ${list("To practice", content.practice_targets)}
    ${content.next_step ? `<p><strong>Next step:</strong> ${escapeHtml(content.next_step)}</p>` : ""}
  </li>`;
}

export class TutorMemoryPanel {
  constructor({ root, signedIn, getClassId, onChange = () => {} }) {
    this.root = root;
    this.signedIn = signedIn;
    this.getClassId = getClassId;
    this.onChange = onChange;
    this.data = null;
    this.busy = false;
    this.writing = false;
    this.locked = false;
    this.disposed = false;
    this.status = "";
    this.error = "";
    this.generation = 0;
    root.querySelector("#remember-sessions").addEventListener("change", (event) => this.changeEnabled(event.target.checked));
    root.querySelector("#clear-saved-memory").addEventListener("click", () => this.clear());
    root.querySelector("#retry-saved-memory").addEventListener("click", () => this.refresh());
    this.render();
  }

  setLocked(locked) {
    this.locked = locked;
    this.render();
  }

  render() {
    if (this.disposed) return;
    const available = this.signedIn && this.getClassId() !== undefined;
    const toggle = this.root.querySelector("#remember-sessions");
    toggle.checked = !!this.data?.enabled;
    toggle.disabled = !available || !this.data || this.busy || this.locked;
    const description = this.root.querySelector("#saved-memory-description");
    description.textContent = !this.signedIn
      ? "Log in to remember tutoring sessions. Guests have no saved memory."
      : !available
        ? "Start a session to sync this class, then enable memory after ending it."
        : this.status || this.error || (this.busy ? "Loading session memory…" : this.data?.enabled
          ? "Saves a short recap when you press End and uses recent recaps next time. Audio, screen frames, and full transcripts are never saved."
          : "Memory is off. Enable it to save short recaps when you press End.");
    this.root.querySelector("#clear-saved-memory").disabled = !available || !this.data?.saved_count || this.busy || this.locked;
    const details = this.root.querySelector("#saved-recaps");
    details.hidden = !this.data?.items?.length;
    // Keep the disclosure's open state while the connection changes.
    const html = (this.data?.items || []).map(recapHtml).join("");
    const list = this.root.querySelector("#saved-recap-list");
    if (list.innerHTML !== html) list.innerHTML = html;
    const retry = this.root.querySelector("#retry-saved-memory");
    retry.hidden = !this.error;
    retry.disabled = this.busy || this.locked;
  }

  async refresh() {
    const classId = this.getClassId();
    if (!this.signedIn || classId === undefined || this.disposed || this.writing) return;
    const generation = ++this.generation;
    this.busy = true;
    this.error = "";
    this.onChange();
    this.render();
    try {
      const data = await getTutorMemoryApi(classId);
      if (this.disposed || generation !== this.generation) return;
      this.data = data;
    } catch (error) {
      if (!this.disposed && generation === this.generation) this.error = `Could not load session memory: ${error.message}`;
    } finally {
      if (!this.disposed && generation === this.generation) {
        this.busy = false;
        this.onChange();
        this.render();
      }
    }
  }

  async changeEnabled(enabled) {
    if (this.locked || this.busy || this.disposed || !this.data) { this.render(); return; }
    this.busy = true;
    this.writing = true;
    this.status = "";
    this.error = "";
    this.onChange();
    this.render();
    try {
      const data = await setTutorMemoryApi(this.getClassId(), enabled);
      if (!this.disposed) this.data = data;
    } catch (error) {
      if (!this.disposed) toast(`Could not change session memory: ${error.message}`, { tone: "error" });
    } finally {
      if (!this.disposed) {
        this.busy = false;
        this.writing = false;
        this.onChange();
        this.render();
      }
    }
  }

  async clear() {
    if (this.locked || this.busy || this.disposed || !this.data?.saved_count) return;
    const confirmed = await openDialog({
      title: "Clear saved session recaps?",
      message: "This deletes saved recaps for this class or general tutoring. Memory will keep its current setting.",
      confirmLabel: "Clear recaps", danger: true
    });
    if (!confirmed || this.disposed || this.locked || this.busy) return;
    this.busy = true;
    this.writing = true;
    this.status = "";
    this.error = "";
    this.onChange();
    this.render();
    try {
      const data = await clearTutorMemoryApi(this.getClassId());
      if (!this.disposed) { this.data = data; toast("Saved session recaps cleared."); }
    } catch (error) {
      if (!this.disposed) toast(`Could not clear saved recaps: ${error.message}`, { tone: "error" });
    } finally {
      if (!this.disposed) {
        this.busy = false;
        this.writing = false;
        this.onChange();
        this.render();
      }
    }
  }

  report(status, loadedCount = 0, enabled = false) {
    if (this.disposed) return;
    if (status === "loaded" && this.data) this.data.enabled = enabled;
    const messages = {
      saving: "Saving a short session recap… Your screen and microphone are stopped.",
      saved: "Session recap saved. The tutor can use it next time.",
      empty: "No recap saved because this session had no student questions or voice transcription.",
      failed: "The session ended, but its recap could not be saved. Earlier recaps are still available.",
      unconfirmed: "The session ended, but saving its recap could not be confirmed.",
      disabled: "No session recap saved. Your saved memory setting is unchanged."
    };
    this.status = messages[status] || (status === "loaded"
      ? enabled
        ? `${loadedCount ? `Using ${loadedCount} recent session ${loadedCount === 1 ? "recap" : "recaps"}. ` : "Memory is on for this session. "}A short recap will be saved when you press End.`
        : "Memory is off for this session. No recap will be saved."
      : "");
    this.render();
    if (["saved", "empty", "failed", "unconfirmed"].includes(status) && !this.root.closest("dialog")?.open) {
      toast(messages[status], { tone: ["failed", "unconfirmed"].includes(status) ? "error" : "info" });
    }
  }

  dispose() {
    this.disposed = true;
    this.generation += 1;
    this.data = null;
  }
}
