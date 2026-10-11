import { icon } from "./icons.js";

// Light or dark is a choice the person makes. Until they make one, the page
// follows the system setting. index.html applies the saved choice before the
// first paint; this module owns it afterward.

const STORAGE_KEY = "jot-theme";
const THEME_COLORS = { light: "#f4efe3", dark: "#121d24" };

function savedTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLORS[theme]);
  refreshThemeToggles();
}

function toggleLabel(theme) {
  return theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
}

// The button shows where it will take you: a sun while dark, a moon while light.
export function themeToggleMarkup({ floating = false } = {}) {
  const theme = currentTheme();
  const label = toggleLabel(theme);
  return `<button class="btn btn-ghost btn-icon theme-toggle${floating ? " theme-toggle-floating" : ""}" type="button" data-theme-toggle aria-label="${label}" title="${label}">${icon(theme === "dark" ? "sun" : "moon", { size: 20 })}</button>`;
}

function refreshThemeToggles() {
  const theme = currentTheme();
  const label = toggleLabel(theme);
  for (const button of document.querySelectorAll("[data-theme-toggle]")) {
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.innerHTML = icon(theme === "dark" ? "sun" : "moon", { size: 20 });
  }
}

export function setTheme(theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private mode or blocked storage: the choice still applies for this visit.
  }
  applyTheme(theme);
}

export function initTheme() {
  // The inline script in index.html normally set this already.
  applyTheme(savedTheme() ?? systemTheme());

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-theme-toggle]")) {
      setTheme(currentTheme() === "dark" ? "light" : "dark");
    }
  });

  // With no saved choice, follow the system as it changes.
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (!savedTheme()) applyTheme(systemTheme());
  });
}
