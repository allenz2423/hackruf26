import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";

import { createStudyMaterialApi } from "../src/api.js";
import { clearStudyMaterialOperationId, createClassAssistantMaterials, getStudyMaterialOperationId, lastOpenedStudyMaterial, renderStudyTab } from "../src/study.js";

function globals(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
}

test("Continue resumes the last opened ready material within this account and class", (t) => {
  const store = new Map([["jot-study-last-open:student-1:class-1", "older-guide"]]);
  globals(t, { localStorage: { getItem: (key) => store.get(key) || null } });
  const materials = [
    { id: "latest-quiz", status: "ready" },
    { id: "older-guide", status: "ready" },
    { id: "failed-guide", status: "failed" }
  ];
  assert.equal(lastOpenedStudyMaterial(materials, { classId: "class-1", userId: "student-1" }).id, "older-guide");
  assert.equal(lastOpenedStudyMaterial(materials, { classId: "class-2", userId: "student-1" }).id, "latest-quiz");
  assert.equal(lastOpenedStudyMaterial(materials, { classId: "class-1", userId: "student-2" }).id, "latest-quiz");
});

test("study-material API sends nonblank guide and assessment preferences", async (t) => {
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
    guideStyle: "visual",
    additionalInstructions: "Cover every chapter in order."
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
  assert.equal(requests[2].body.additional_instructions, "Cover every chapter in order.");
});

test("class assistant can create private materials from any class tab and report progress", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const requests = [];
  const additions = [];
  const statuses = [];
  let refreshes = 0;
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
    else delete globalThis.crypto;
  });
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path: String(path), options });
    const isCreate = options.method === "POST";
    const detail = {
      id: "assistant-guide-1",
      kind: "study_guide",
      title: "Osmosis · Study guide",
      status: isCreate ? "generating" : "ready",
      resource_scope: "personal",
      scope: { kind: "uploads", upload_ids: ["upload-1"] },
      item_count: 1,
      content: { sections: [] }
    };
    return new Response(JSON.stringify(detail), {
      status: isCreate ? 202 : 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const materials = await createClassAssistantMaterials({
    materialRequest: {
      topic: "Osmosis",
      kinds: ["study_guide"],
      upload_ids: ["upload-1"],
      count: 8,
      learner_focus: "Compare diffusion and osmosis"
    },
    backendClassId: "server-class-1",
    klass: { id: "local-class-1", uploads: [] },
    onMaterialAdded: (material) => additions.push(material),
    onMaterialsChanged: () => { refreshes += 1; },
    onStatus: (status) => statuses.push(status)
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests[0].path, "/api/classes/server-class-1/study-materials");
  assert.equal(requests[0].options.method, "POST");
  const payload = JSON.parse(requests[0].options.body);
  assert.equal(payload.destination, "personal");
  assert.deepEqual(payload.scope, { kind: "uploads", upload_ids: ["upload-1"] });
  assert.deepEqual(payload.guide_styles, ["balanced", "worked_examples"]);
  assert.match(payload.additional_instructions, /Compare diffusion and osmosis/);
  assert.equal(materials[0].status, "generating");
  assert.deepEqual(requests.map((request) => request.options.method || "GET"), ["POST", "GET"]);
  assert.deepEqual(additions.map((material) => material.status), ["generating", "ready"]);
  assert.equal(refreshes, 2);
  assert.deepEqual(statuses.map((status) => status.status), ["generating", "ready"]);
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
    this.classList = { toggle() {} };
    this.listeners = new Map();
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }

  removeEventListener() {}
}

function studyTabElements() {
  const form = new FakeElement();
  const list = new FakeElement();
  const picker = new FakeElement();
  const filters = new Map(["all", "none", "notes", "syllabus"].map((key) => [key, new FakeElement()]));
  picker.querySelector = (selector) => filters.get(selector.match(/"([^"]+)"/)?.[1]);
  picker.querySelectorAll = () => [];
  const elements = new Map([
    ["#generate-form", form], ["#material-list", list], ["#generate-btn", new FakeElement()],
    ["#generation-status", new FakeElement()], ["#study-source-picker", picker],
    ["#source-selection-count", new FakeElement()], ["#generation-instructions-block", new FakeElement()],
    ["#generation-instructions-label", new FakeElement()],
    ["#generation-instructions-help", new FakeElement()],
    ["#generation-instructions", new FakeElement()],
    ["#generation-language-block", new FakeElement()], ["#study-guide-title-block", new FakeElement()],
    ["#study-guide-style-block", new FakeElement()], ["#generate-count-label", new FakeElement()]
  ]);
  return { root: { innerHTML: "", querySelector: (selector) => elements.get(selector) }, list };
}

test("a class study tab only includes personal guides originating in that class", async (t) => {
  const computerScienceId = "11111111-1111-4111-8111-111111111111";
  const economicsId = "22222222-2222-4222-8222-222222222222";
  const guides = [
    {
      id: "cs-guide", kind: "study_guide", title: "Computer Science Source Study Guide",
      origin_class_id: computerScienceId, resource_scope: "personal", created_by: "user-1",
      status: "ready", item_count: 4, created_at: "2026-10-16T00:00:00Z",
      scope: { kind: "all" }, options: {}
    },
    {
      id: "economics-guide", kind: "study_guide", title: "Economics Source Study Guide",
      origin_class_id: economicsId, resource_scope: "personal", created_by: "user-1",
      status: "ready", item_count: 4, created_at: "2026-10-16T00:00:00Z",
      scope: { kind: "all" }, options: {}
    }
  ];
  globals(t, {
    localStorage: (() => {
      const values = new Map([["screen-tutor-classes", JSON.stringify([{
        id: "computer-science-local-id", backendId: computerScienceId,
        name: "Computer Science", uploads: [], materials: [], capabilities: []
      }])]]);
      return {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: (key) => values.delete(key)
      };
    })(),
    fetch: async (path) => {
      let items = [];
      if (path === "/api/me/study-materials") items = guides;
      if (path === `/api/classes/${computerScienceId}/study-materials`) items = [guides[0]];
      return new Response(JSON.stringify({ items }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
  });

  const klass = {
    id: "computer-science-local-id", backendId: computerScienceId, name: "Computer Science",
    uploads: [], materials: [], capabilities: [], isOwner: false
  };
  const classView = studyTabElements();
  await renderStudyTab({ root: classView.root, klass, currentUserId: "user-1" });

  assert.match(classView.list.innerHTML, /Source Study Guide/);
  assert.match(classView.list.innerHTML, /data-open-material="cs-guide"/);
  assert.doesNotMatch(classView.list.innerHTML, /economics-guide/);
  assert.deepEqual(klass.materials.map((material) => material.id), ["cs-guide"]);

  const localClass = { id: "local-only-class", name: "Local class", uploads: [], materials: [] };
  const localView = studyTabElements();
  await renderStudyTab({ root: localView.root, klass: localClass, currentUserId: "user-1" });
  assert.doesNotMatch(localView.list.innerHTML, /Computer Science Source Study Guide|Economics Source Study Guide/);
});

test("additional-instructions control supports guides and assessments", async () => {
  const form = new FakeElement();
  const list = new FakeElement();
  const generateButton = new FakeElement();
  const status = new FakeElement();
  const picker = new FakeElement();
  const selectionCount = new FakeElement();
  const instructions = new FakeElement();
  const instructionsLabel = new FakeElement();
  const instructionsHelp = new FakeElement();
  const instructionsInput = new FakeElement();
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
    ["#generation-instructions-label", instructionsLabel],
    ["#generation-instructions-help", instructionsHelp],
    ["#generation-instructions", instructionsInput],
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
  assert.equal(instructions.hidden, false);
  assert.equal(instructionsLabel.textContent, "Guide instructions (optional)");
  assert.match(instructionsHelp.textContent, /course materials/);
  assert.match(instructionsInput.placeholder, /each chapter in order/);
  instructionsInput.value = "Cover chapter 4 in depth.";
  assert.equal(language.hidden, true);
  assert.equal(guideTitle.hidden, false);
  assert.equal(guideStyle.hidden, false);
  change({ target: { name: "type", value: "Practice test" } });
  assert.equal(instructions.hidden, false);
  assert.equal(instructionsLabel.textContent, "Additional instructions (optional)");
  assert.equal(language.hidden, false);
  assert.equal(instructionsInput.value, "");
  change({ target: { name: "type", value: "Study guide" } });
  assert.equal(instructionsInput.value, "Cover chapter 4 in depth.");
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
