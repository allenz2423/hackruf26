import test from "node:test";
import assert from "node:assert/strict";

import {
  buildGuideChatRequest,
  normalizeGuideChatReference,
  normalizeSavedGuideChatMessage
} from "../src/guide-chat.js";

test("guide chat sends only bounded current context; history now comes from the server", () => {
  const request = buildGuideChatRequest({
    sectionIndex: 3,
    blockIndex: 2,
    scope: "guide",
    intent: "practice",
    message: "Ask me a different question.",
    history: [{ role: "user", text: "untrusted client history" }]
  });
  assert.deepEqual(request, {
    section_index: 3,
    block_index: 2,
    scope: "guide",
    intent: "practice",
    message: "Ask me a different question."
  });
  assert.equal("source_text" in request, false);
  assert.equal("selected_text" in request, false);
  assert.equal("correct_answer" in request, false);
});

test("guide chat caps the current message", () => {
  assert.equal(buildGuideChatRequest({ sectionIndex: 0, message: "a".repeat(2200) }).message.length, 2000);
});

test("saved practice conversation restores references and context without becoming mastery data", () => {
  const message = normalizeSavedGuideChatMessage({
    id: "message-1",
    role: "assistant",
    text: "Try predicting the direction of water movement.",
    section_index: 2,
    section_heading: "Osmosis",
    scope: "section",
    intent: "practice",
    created_at: "2026-10-18T12:00:00Z",
    references: [],
    source_warnings: [],
    practice_question: { prompt: "Predict net water movement." },
    edit_proposal: null,
    edit_state: null
  });
  assert.equal(message.sectionIndex, 2);
  assert.equal(message.sectionHeading, "Osmosis");
  assert.equal(message.practiceQuestion.prompt, "Predict net water movement.");
  assert.equal(message.persisted, true);
  assert.equal("mastery" in message, false);
});

test("saved edit state hides resolved previews after reopening", () => {
  const applied = normalizeSavedGuideChatMessage({
    id: "message-2", role: "assistant", text: "Updated section.",
    section_index: 0, section_heading: "Topic", scope: "section", intent: "adapt",
    created_at: "2026-10-18T12:00:00Z", edit_proposal: { summary: "Edit", section: { blocks: [] } },
    edit_state: "applied"
  });
  assert.equal(applied.editProposal, null);
  assert.equal(applied.editApplied, true);
});

test("guide chat source references accept IDs and locators but no source excerpts", () => {
  assert.deepEqual(normalizeGuideChatReference({
    upload_id: "upload-1", label: "Lecture 4", page: 8, slide: 2, timestamp_s: 32.5, excerpt: "private source text"
  }), {
    upload_id: "upload-1", name: "Lecture 4", page: 8, slide: 2, timestamp_seconds: 32.5
  });
  assert.equal(normalizeGuideChatReference({ label: "No id" }), null);
});
