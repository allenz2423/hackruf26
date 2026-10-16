import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";

import { createStudyMaterialApi } from "../src/api.js";
import { clearStudyMaterialOperationId, getStudyMaterialOperationId, renderStudyTab } from "../src/study.js";

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
    additionalInstructions: "  Focus on applications.  ",
    language: "es",
    operationId: "11111111-1111-4111-8111-111111111111"
  });
  await createStudyMaterialApi("class-id", {
    kind: "practice_exam",
    additionalInstructions: "   "
  });
  await createStudyMaterialApi("class-id", {
    kind: "study_guide",
    title: "  Membrane Transport  ",
    guideStyle: "visual"
  });

  assert.equal(requests[0].body.additional_instructions, "Focus on applications.");
  assert.equal(requests[0].body.language, "es");
  assert.equal(requests[0].body.operation_id, "11111111-1111-4111-8111-111111111111");
  assert.equal(requests[0].body.destination, "personal");
  assert.equal(Object.hasOwn(requests[1].body, "additional_instructions"), false);
  assert.equal(Object.hasOwn(requests[1].body, "language"), false);
  assert.equal(Object.hasOwn(requests[1].body, "operation_id"), false);
  assert.equal(requests[2].body.title, "Membrane Transport");
  assert.equal(requests[2].body.guide_style, "visual");
});

test("generation operation IDs are stable for retries without persisting request text", async (t) => {
  const storageData = new Map();
  const storage = {
    getItem: (key) => storageData.get(key) || null,
    setItem: (key, value) => storageData.set(key, value),
    removeItem: (key) => storageData.delete(key)
  };
  globals(t, { crypto: webcrypto, sessionStorage: storage });
  const payload = {
    kind: "quiz", scope: { kind: "uploads", upload_ids: ["upload-1"] },
    options: { count: 12, difficulty: "mixed" },
    additional_instructions: "Focus on the energy-transfer examples.",
    destination: "personal", language: "en"
  };
  const first = await getStudyMaterialOperationId("class-1", payload);
  assert.equal(await getStudyMaterialOperationId("class-1", payload), first);
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  const persisted = [...storageData.values()].join(" ");
  assert.doesNotMatch(persisted, /energy-transfer|additional_instructions/);

  const changed = await getStudyMaterialOperationId("class-1", { ...payload, language: "fr" });
  assert.notEqual(changed, first);
  clearStudyMaterialOperationId("class-1", changed);
  assert.equal(storageData.size, 0);
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

  removeEventListener() {}
}

test("additional-instructions control appears only for quizzes and practice tests", async () => {
  const form = new FakeElement();
  const list = new FakeElement();
  const generateButton = new FakeElement();
  const status = new FakeElement();
  const picker = new FakeElement();
  const selectionCount = new FakeElement();
  const instructions = new FakeElement();
  const language = new FakeElement();
  const guideTitle = new FakeElement();
  const guideStyle = new FakeElement();
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
    ["#generation-language-block", language],
    ["#study-guide-title-block", guideTitle],
    ["#study-guide-style-block", guideStyle],
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
  assert.match(root.innerHTML, /Study guide name \(optional\)/);
  assert.match(root.innerHTML, /Study guide style/);
  assert.match(root.innerHTML, /Visual overview/);
  assert.match(root.innerHTML, /Text-dense explanations/);
  assert.match(root.innerHTML, /Interactive learning/);
  assert.match(root.innerHTML, /Worked examples/);
  assert.match(root.innerHTML, /Quick reference/);
  assert.match(root.innerHTML, /AI will suggest a name/);
  assert.match(root.innerHTML, /maxlength="2000"/);
  assert.match(root.innerHTML, /Question language/);
  assert.match(root.innerHTML, /Match source language/);
  assert.match(root.innerHTML, /value="zh-Hans"[^>]*>简体中文 \(Chinese, Simplified\)/);
  assert.equal(instructions.hidden, false);
  assert.equal(language.hidden, false);
  assert.equal(guideTitle.hidden, true);
  assert.equal(guideStyle.hidden, true);

  const change = form.listeners.get("change");
  change({ target: { name: "type", value: "Study guide" } });
  assert.equal(instructions.hidden, true);
  assert.equal(language.hidden, true);
  assert.equal(guideTitle.hidden, false);
  assert.equal(guideStyle.hidden, false);
  change({ target: { name: "type", value: "Practice test" } });
  assert.equal(instructions.hidden, false);
  assert.equal(language.hidden, false);
  change({ target: { name: "type", value: "Flashcards" } });
  assert.equal(instructions.hidden, true);
  assert.equal(language.hidden, true);
  assert.equal(guideTitle.hidden, true);
  assert.equal(guideStyle.hidden, true);
});

test("opening a failed saved guide displays its unverified preview", async (t) => {
  let openedDialog;
  class FakeDialog extends FakeElement {
    constructor() {
      super();
      this.open = false;
      this.root = new FakeElement();
      this.root.querySelectorAll = () => [];
      this.root.querySelector = () => null;
      this.title = new FakeElement();
      this.meta = new FakeElement();
      this.closeButton = new FakeElement();
    }
    setAttribute() {}
    showModal() { this.open = true; }
    remove() {}
    querySelector(selector) {
      return {
        "#material-session": this.root,
        "#material-dialog-title": this.title,
        "#material-dialog-meta": this.meta,
        "[data-close]": this.closeButton
      }[selector] || null;
    }
  }
  globals(t, {
    document: {
      createElement: () => (openedDialog = new FakeDialog()),
      body: { append() {} },
      querySelector: () => null
    },
    window: {
      addEventListener() {},
      removeEventListener() {}
    },
    fetch: async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        id: "guide-1",
        kind: "study_guide",
        title: "Study Guide: asa",
        status: "failed",
        preview_revision: 16,
        preview_content: {
          format: "study_guide",
          sections: [{ heading: "Stress tensor", body: "Provisional section text." }]
        },
        download_urls: {}
      })
    })
  });

  const form = new FakeElement();
  const list = new FakeElement();
  const generateButton = new FakeElement();
  const status = new FakeElement();
  const picker = new FakeElement();
  const selectionCount = new FakeElement();
  const filters = new Map(["all", "none", "notes", "syllabus"].map((key) => [key, new FakeElement()]));
  picker.querySelector = (selector) => filters.get(selector.match(/"([^"]+)"/)?.[1]);
  picker.querySelectorAll = (selector) => selector.includes(":checked") ? [{ value: "upload-1" }] : [];
  const elements = new Map([
    ["#generate-form", form], ["#material-list", list], ["#generate-btn", generateButton],
    ["#generation-status", status], ["#study-source-picker", picker],
    ["#source-selection-count", selectionCount],
    ["#generation-instructions-block", new FakeElement()],
    ["#generation-language-block", new FakeElement()],
    ["#study-guide-title-block", new FakeElement()],
    ["#study-guide-style-block", new FakeElement()],
    ["#generate-count-label", new FakeElement()]
  ]);
  const root = { innerHTML: "", querySelector: (selector) => elements.get(selector) };
  const material = {
    id: "guide-1", type: "Study guide", title: "Study Guide: asa", status: "failed",
    isRemote: true, resourceScope: "personal", count: 30,
    createdAt: "2026-10-15T00:00:00Z"
  };
  const klass = {
    id: "local-class", name: "asa", uploads: [{ id: "upload-1", name: "Notes", kind: "Notes", status: "ready" }],
    materials: [material]
  };

  await renderStudyTab({ root, klass });
  await list.listeners.get("click")({
    target: {
      closest(selector) {
        return selector === "[data-open-material]" ? { dataset: { openMaterial: "guide-1" } } : null;
      }
    }
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(openedDialog.open, true);
  assert.match(openedDialog.root.innerHTML, /Generation stopped before the guide was saved/);
  assert.match(openedDialog.root.innerHTML, /Provisional section text/);
  assert.doesNotMatch(openedDialog.root.innerHTML, /data-download-guide/);
  assert.equal(material.previewContent, undefined, "provisional content must not be cached in class data");
});
