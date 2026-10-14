import assert from "node:assert/strict";
import test from "node:test";

import { createStudyMaterialApi } from "../src/api.js";
import { renderStudyTab } from "../src/study.js";

function globals(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
}

test("study-material API sends nonblank assessment preferences and omits empty ones", async (t) => {
  const requests = [];
  globals(t, {
    fetch: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 202, text: async () => "{}" };
    }
  });

  await createStudyMaterialApi("class-id", {
    kind: "quiz",
    additionalInstructions: "  Focus on applications.  "
  });
  await createStudyMaterialApi("class-id", {
    kind: "practice_exam",
    additionalInstructions: "   "
  });

  assert.equal(requests[0].body.additional_instructions, "Focus on applications.");
  assert.equal(requests[0].body.destination, "personal");
  assert.equal(Object.hasOwn(requests[1].body, "additional_instructions"), false);
});

class FakeElement {
  constructor() {
    this.innerHTML = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
    this.isConnected = true;
    this.listeners = new Map();
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }
}

test("additional-instructions control appears only for quizzes and practice tests", async () => {
  const form = new FakeElement();
  const list = new FakeElement();
  const generateButton = new FakeElement();
  const status = new FakeElement();
  const picker = new FakeElement();
  const selectionCount = new FakeElement();
  const instructions = new FakeElement();
  const countLabel = new FakeElement();
  const checkbox = { value: "upload-1" };
  const filters = new Map(["all", "none", "notes", "syllabus"].map((key) => [
    key,
    new FakeElement()
  ]));
  picker.querySelector = (selector) => filters.get(selector.match(/"([^"]+)"/)?.[1]);
  picker.querySelectorAll = (selector) => selector.includes(":checked") ? [checkbox] : [];

  const elements = new Map([
    ["#generate-form", form],
    ["#material-list", list],
    ["#generate-btn", generateButton],
    ["#generation-status", status],
    ["#study-source-picker", picker],
    ["#source-selection-count", selectionCount],
    ["#generation-instructions-block", instructions],
    ["#generate-count-label", countLabel]
  ]);
  const root = {
    innerHTML: "",
    querySelector: (selector) => elements.get(selector)
  };
  const klass = {
    id: "local-class",
    name: "Biology",
    uploads: [{ id: "upload-1", name: "Notes", kind: "Notes", status: "ready" }],
    materials: []
  };

  await renderStudyTab({ root, klass });
  assert.match(root.innerHTML, /Additional instructions \(optional\)/);
  assert.match(root.innerHTML, /maxlength="2000"/);
  assert.equal(instructions.hidden, false);

  const change = form.listeners.get("change");
  change({ target: { name: "type", value: "Study guide" } });
  assert.equal(instructions.hidden, true);
  change({ target: { name: "type", value: "Practice test" } });
  assert.equal(instructions.hidden, false);
  change({ target: { name: "type", value: "Flashcards" } });
  assert.equal(instructions.hidden, true);
});
