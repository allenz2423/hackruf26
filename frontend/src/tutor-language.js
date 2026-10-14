// Languages the student can lock the live tutor to. "auto" means no lock: the tutor
// follows whatever language the student uses. Keep the ids in step with
// TutorLanguage in backend/app/live_modes.py; the server rejects anything else.

export const TUTOR_LANGUAGES = [
  { id: "auto", label: "No lock (match me)" },
  { id: "en", label: "English" },
  { id: "es", label: "Español (Spanish)" },
  { id: "fr", label: "Français (French)" },
  { id: "de", label: "Deutsch (German)" },
  { id: "pt", label: "Português (Portuguese)" },
  { id: "it", label: "Italiano (Italian)" },
  { id: "zh", label: "中文 (Mandarin Chinese)" },
  { id: "ja", label: "日本語 (Japanese)" },
  { id: "ko", label: "한국어 (Korean)" },
  { id: "hi", label: "हिन्दी (Hindi)" },
  { id: "ar", label: "العربية (Arabic)" }
];

// Quiz and exam generation accepts BCP-47 tags beyond the live tutor's locale allow-list.
// Keep Simplified Chinese explicit here without implying it is a supported live-tutor lock.
export const GENERATION_LANGUAGES = [
  ...TUTOR_LANGUAGES,
  { id: "zh-Hans", label: "简体中文 (Chinese, Simplified)" }
];

const STORAGE_KEY = "jot-tutor-language";

export function normalizeLanguage(id) {
  return TUTOR_LANGUAGES.some((item) => item.id === id) ? id : "auto";
}

export function languageLabel(id) {
  return TUTOR_LANGUAGES.find((item) => item.id === normalizeLanguage(id)).label;
}

// The choice is remembered in this browser so it carries over to the next session.
export function loadTutorLanguage() {
  try {
    return normalizeLanguage(localStorage.getItem(STORAGE_KEY));
  } catch {
    return "auto";
  }
}

export function saveTutorLanguage(id) {
  const value = normalizeLanguage(id);
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Storage blocked: the choice still applies to this visit.
  }
  return value;
}
