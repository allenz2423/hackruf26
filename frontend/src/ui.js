import { icon } from "./icons.js";

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Accessible replacement for window.prompt/confirm, built on <dialog>.
// Resolves with the entered text (prompt), true (confirm), or null when cancelled.
export function openDialog({ title, message = "", input = null, confirmLabel = "Save", danger = false }) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "dialog";
    dialog.setAttribute("aria-labelledby", "dialog-title");
    dialog.innerHTML = `
      <form method="dialog" class="dialog-body">
        <h2 id="dialog-title">${escapeHtml(title)}</h2>
        ${message ? `<p class="muted">${escapeHtml(message)}</p>` : ""}
        ${input ? `
          <label class="field">
            <span>${escapeHtml(input.label)}</span>
            <input name="value" required maxlength="60" autocomplete="off" value="${escapeHtml(input.value || "")}" />
          </label>` : ""}
        <div class="dialog-actions">
          <button type="button" class="btn btn-secondary" data-cancel>Cancel</button>
          <button type="submit" class="btn ${danger ? "btn-danger" : "btn-primary"}">${escapeHtml(confirmLabel)}</button>
        </div>
      </form>
    `;

    let result = null;
    const form = dialog.querySelector("form");
    const field = dialog.querySelector("input");

    form.addEventListener("submit", (event) => {
      if (field) {
        const value = field.value.trim();
        if (!value) {
          event.preventDefault();
          field.focus();
          return;
        }
        result = value;
      } else {
        result = true;
      }
    });
    dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });

    document.body.append(dialog);
    dialog.showModal();
    field?.select();
  });
}

function toastRegion() {
  let region = document.querySelector("#toast-region");
  if (!region) {
    region = document.createElement("div");
    region.id = "toast-region";
    region.className = "toast-region";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  return region;
}

export function toast(message, { tone = "info" } = {}) {
  const item = document.createElement("div");
  item.className = `toast toast-${tone}`;
  item.innerHTML = `${icon(tone === "error" ? "alert" : "check", { size: 18 })}<span>${escapeHtml(message)}</span>`;
  toastRegion().append(item);
  window.setTimeout(() => item.remove(), tone === "error" ? 7000 : 4000);
}
