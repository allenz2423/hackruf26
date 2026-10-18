import test from "node:test";
import assert from "node:assert/strict";

import { buildGuideChatRequest, guideChatHistory, normalizeGuideChatReference } from "../src/guide-chat.js";

test("guide chat sends bounded IDs and messages, never selected source or answer-key text", () => {
  const request = buildGuideChatRequest({
    sectionIndex: 3,
    blockIndex: 2,
    scope: "guide",
    intent: "practice",
    message: "Ask me a different question.",
    history: [{ role: "user", text: "Earlier" }, { role: "assistant", text: "Prior reply" }]
  });
  assert.deepEqual(request, {
    section_index: 3,
    block_index: 2,
    scope: "guide",
    intent: "practice",
    message: "Ask me a different question.",
    history: [{ role: "user", text: "Earlier" }, { role: "assistant", text: "Prior reply" }]
  });
  assert.equal("source_text" in request, false);
  assert.equal("selected_text" in request, false);
  assert.equal("correct_answer" in request, false);
});

test("guide chat retains only the six preceding messages and caps the current message", () => {
  const history = Array.from({ length: 12 }, (_, index) => ({ role: index % 2 ? "assistant" : "user", text: `turn-${index}` }));
  assert.deepEqual(guideChatHistory(history).map((message) => message.text), ["turn-6", "turn-7", "turn-8", "turn-9", "turn-10", "turn-11"]);
  assert.equal(buildGuideChatRequest({ sectionIndex: 0, message: "a".repeat(2200) }).message.length, 2000);
});

test("practice follow-up history includes the question while keeping section context local", () => {
  const history = guideChatHistory([
    {
      role: "assistant",
      text: "Try predicting the direction of water movement.",
      historyText: "Here is a fresh practice question.\n\nFresh practice question: Predict net water movement when the outside has lower water potential.",
      sectionIndex: 2,
      sectionHeading: "Osmosis"
    }
  ]);
  assert.match(history[0].text, /Predict net water movement/);
  assert.equal("sectionHeading" in history[0], false);
  assert.equal("sectionIndex" in history[0], false);
});

test("guide chat source references accept IDs and locators but no source excerpts", () => {
  assert.deepEqual(normalizeGuideChatReference({
    upload_id: "upload-1", label: "Lecture 4", page: 8, slide: 2, timestamp_s: 32.5, excerpt: "private source text"
  }), {
    upload_id: "upload-1", name: "Lecture 4", page: 8, slide: 2, timestamp_seconds: 32.5
  });
  assert.equal(normalizeGuideChatReference({ label: "No id" }), null);
});
