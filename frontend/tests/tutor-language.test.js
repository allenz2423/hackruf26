import assert from "node:assert/strict";
import test from "node:test";

import { TUTOR_LANGUAGES, languageLabel, normalizeLanguage } from "../src/tutor-language.js";

test("the choices match the server's allow-list, with no lock first", () => {
  // Same order as TutorLanguage in backend/app/live_modes.py (checked there too).
  assert.deepEqual(
    TUTOR_LANGUAGES.map((item) => item.id),
    ["auto", "en", "es", "fr", "de", "pt", "it", "zh", "ja", "ko", "hi", "ar"]
  );
  assert.equal(new Set(TUTOR_LANGUAGES.map((item) => item.label)).size, TUTOR_LANGUAGES.length);
});

test("anything unknown means no lock", () => {
  for (const value of [undefined, null, "", "ES", "klingon", "es\n", 7]) {
    assert.equal(normalizeLanguage(value), "auto");
  }
  assert.equal(normalizeLanguage("es"), "es");
  assert.equal(languageLabel("es"), "Español (Spanish)");
  assert.equal(languageLabel("nope"), "No lock (match me)");
});
