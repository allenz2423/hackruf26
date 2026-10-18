import assert from "node:assert/strict";
import test from "node:test";

import { flashcardReflectionHtml } from "../src/flashcards.js";

test("flashcard reflection offers two bounded, escaped prompts and states that notes are transient", () => {
  const html = flashcardReflectionHtml("card/one", {
    knew: "I remembered <script>alert(1)</script>",
    unclear: "x".repeat(1200)
  });

  assert.match(html, /What did you already know\?/);
  assert.match(html, /What was unclear or missing\?/);
  assert.match(html, /maxlength="1000"/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /Reflections stay in this open study session; they are not sent or saved\./);
  assert.match(html, /card-one-knew/);
  assert.match(html, /1000\/1000/);
});
