import test from "node:test";
import assert from "node:assert/strict";
import {
  getPythonExampleCode,
  getTrustedPythonProgram,
  mountPythonActivity,
  normalizePythonAlgorithmBlock,
  normalizePythonTraceResult,
  renderPythonActivity
} from "../src/study-guide/libraries/python-activity.js";
import { runPythonWorkerRequest } from "../src/study-guide/libraries/python-activity-worker-core.js";

function block(algorithm = "linear_search", overrides = {}) {
  return {
    template_id: "python_algorithm_trace_v1",
    title: "Algorithm walkthrough",
    parameters: { algorithm, ...overrides }
  };
}

test("Python activity accepts only reviewed algorithms and bounded data, never source code", () => {
  assert.deepEqual(normalizePythonAlgorithmBlock(block("linear_search", { values: [1, 2, 3], target: 2 })), {
    template_id: "python_algorithm_trace_v1",
    title: "Algorithm walkthrough",
    alt_text: "A fixed Python algorithm example runs on a short list of integers.",
    parameters: { algorithm: "linear_search", values: [1, 2, 3], target: 2 }
  });
  assert.throws(() => normalizePythonAlgorithmBlock({ ...block(), source: "import os" }), /source code is not accepted/);
  assert.throws(() => normalizePythonAlgorithmBlock(block("linear_search", { source: "print(1)" })), /Unsupported Python activity parameter/);
  assert.throws(() => normalizePythonAlgorithmBlock(block("binary_search", { values: [4, 2, 8], target: 2 })), /must be sorted/);
  assert.throws(() => normalizePythonAlgorithmBlock(block("linear_search", { values: Array(17).fill(1), target: 1 })), /2 to 16/);
  assert.throws(() => normalizePythonAlgorithmBlock(block("insertion_sort", { values: [1, 2], target: 1 })), /does not accept a search target/);
  assert.throws(() => normalizePythonAlgorithmBlock(block("arbitrary_python", { values: [1, 2] })), /supported Python algorithm/);
});

test("Python code is selected from checked-in templates and render fallback is escaped", () => {
  const activity = block("insertion_sort", { values: [5, 1, 3] });
  const code = getPythonExampleCode(activity);
  assert.equal(code, getTrustedPythonProgram("insertion_sort"));
  assert.match(code, /for index in range/);
  const html = renderPythonActivity({ ...activity, title: "<script>alert(1)</script>" });
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /Text version of the Python example/);
  assert.match(html, /Run this fixed example/);
});

test("worker protocol executes only a fixed program with same-origin local Pyodide assets", async () => {
  let program;
  let suppliedInput;
  let loaderArgs;
  const messages = [];
  const runtime = {
    globals: {
      set(key, value) { if (key === "activity_input") suppliedInput = value; },
      get(key) { return key === "activity_output" ? JSON.stringify({ steps: ["Check index 0: 3", "Found 3 at index 0."], found_index: 0 }) : undefined; },
      delete() {}
    },
    async runPythonAsync(source) { program = source; },
    destroy() {}
  };
  const result = await runPythonWorkerRequest({
    protocol_version: 1,
    type: "run",
    request_id: "python-7",
    template_id: "python_algorithm_trace_v1",
    parameters: { algorithm: "linear_search", values: [3, 2], target: 3 },
    runtime_module_url: "http://guide.test/vendor/pyodide/pyodide.mjs",
    runtime_index_url: "http://guide.test/vendor/pyodide/"
  }, {
    workerLocation: { href: "http://guide.test/python-worker.js", origin: "http://guide.test", protocol: "http:" },
    async loadPyodide(...args) { loaderArgs = args; return runtime; },
    emit(message) { messages.push(message); }
  });

  assert.deepEqual(result, { ok: true, request_id: "python-7" });
  assert.deepEqual(loaderArgs, ["http://guide.test/vendor/pyodide/pyodide.mjs", "http://guide.test/vendor/pyodide/"]);
  assert.equal(program, getTrustedPythonProgram("linear_search"));
  assert.deepEqual(JSON.parse(suppliedInput), { algorithm: "linear_search", values: [3, 2], target: 3 });
  assert.deepEqual(messages, [{
    protocol_version: 1,
    type: "result",
    request_id: "python-7",
    output_json: JSON.stringify({ steps: ["Check index 0: 3", "Found 3 at index 0."], found_index: 0 })
  }]);
});

test("worker rejects untrusted code fields, remote runtime assets, and invalid output size", async () => {
  const emitted = [];
  let runtimeLoaded = false;
  const dependencies = {
    workerLocation: { href: "https://guide.test/worker.js", origin: "https://guide.test", protocol: "https:" },
    async loadPyodide() {
      runtimeLoaded = true;
      return {
        globals: { set() {}, get: () => "x".repeat(8_001), delete() {} },
        async runPythonAsync() {}, destroy() {}
      };
    },
    emit(message) { emitted.push(message); }
  };
  const base = {
    protocol_version: 1,
    type: "run",
    request_id: "python-8",
    template_id: "python_algorithm_trace_v1",
    parameters: { algorithm: "linear_search", values: [1, 2], target: 2 },
    runtime_module_url: "https://guide.test/vendor/pyodide/pyodide.mjs",
    runtime_index_url: "https://guide.test/vendor/pyodide/"
  };

  await runPythonWorkerRequest({ ...base, source: "import os" }, dependencies);
  assert.equal(runtimeLoaded, false);
  assert.equal(emitted.at(-1).type, "error");

  await runPythonWorkerRequest({ ...base, request_id: "python-9", runtime_module_url: "https://attacker.test/pyodide.mjs" }, dependencies);
  assert.equal(runtimeLoaded, false);
  assert.equal(emitted.at(-1).type, "error");

  await runPythonWorkerRequest({ ...base, request_id: "python-10" }, dependencies);
  assert.equal(runtimeLoaded, true);
  assert.deepEqual(emitted.at(-1), {
    protocol_version: 1, type: "error", request_id: "python-10", code: "output_limit"
  });
});

class FakeElement {
  constructor(ownerDocument, tagName = "div") {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.listeners = new Map();
    this.disabled = false;
    this.hidden = false;
    this.value = "";
    this.textContent = "";
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  removeEventListener(name) { this.listeners.delete(name); }
  dispatch(name) { this.listeners.get(name)?.({ target: this }); }
}

function fakeDocument() {
  const document = {
    defaultView: { location: { href: "http://guide.test/study" } },
    createElement(tagName) { return new FakeElement(document, tagName); },
    createTextNode(text) { return { textContent: text }; }
  };
  return document;
}

function fakeContainer() {
  const ownerDocument = fakeDocument();
  return new FakeElement(ownerDocument, "container");
}

class FakeWorker {
  terminated = false;
  message = null;
  postMessage(message) { this.message = message; }
  terminate() { this.terminated = true; }
}

test("mount runs in a worker, renders bounded output, and terminates it after completion", async () => {
  const container = fakeContainer();
  const worker = new FakeWorker();
  const handle = mountPythonActivity(container, block("linear_search", { values: [3, 2], target: 2 }), {
    workerFactory: () => worker
  });
  await handle.ready;
  const run = handle.run();
  assert.equal(worker.message.type, "run");
  assert.equal(worker.message.parameters.values.length, 2);
  worker.onmessage({ data: {
    protocol_version: 1,
    type: "result",
    request_id: worker.message.request_id,
    output_json: JSON.stringify({ steps: ["Check index 0: 3", "Check index 1: 2", "Found 2 at index 1."], found_index: 1 })
  } });

  const result = await run;
  assert.equal(result.ok, true);
  assert.deepEqual(container.children[0].children.find((child) => child.tagName === "ol").children.map((step) => step.textContent), [
    "Check index 0: 3", "Check index 1: 2", "Found 2 at index 1."
  ]);
  assert.equal(worker.terminated, true);
  handle.dispose();
});

test("mount cancellation and timeout terminate the dedicated worker", async () => {
  const cancelContainer = fakeContainer();
  const cancelWorker = new FakeWorker();
  const cancellable = mountPythonActivity(cancelContainer, block(), { workerFactory: () => cancelWorker });
  const cancelRun = cancellable.run();
  cancellable.cancel();
  assert.deepEqual(await cancelRun, { ok: false, reason: "cancelled" });
  assert.equal(cancelWorker.terminated, true);
  cancellable.dispose();

  const timeoutContainer = fakeContainer();
  const timeoutWorker = new FakeWorker();
  let timeoutCallback;
  const timed = mountPythonActivity(timeoutContainer, block(), {
    workerFactory: () => timeoutWorker,
    setTimeoutFn(callback) { timeoutCallback = callback; return 1; },
    clearTimeoutFn() {}
  });
  const timeoutRun = timed.run();
  timeoutCallback();
  assert.deepEqual(await timeoutRun, { ok: false, reason: "timeout" });
  assert.equal(timeoutWorker.terminated, true);
  timed.dispose();
});

test("mount lazily initializes a read-only CodeMirror view and destroys it on dispose", async () => {
  const container = fakeContainer();
  let loadCount = 0;
  let editorOptions;
  let destroyed = false;
  class EditorView {
    static editable = { of: (value) => ({ editable: value }) };
    static contentAttributes = { of: (value) => ({ contentAttributes: value }) };
    constructor(options) { editorOptions = options; }
    destroy() { destroyed = true; }
  }
  const handle = mountPythonActivity(container, block("binary_search"), {
    workerFactory: () => new FakeWorker(),
    async codeMirrorLoader() {
      loadCount += 1;
      return {
        EditorView,
        EditorState: { create: (state) => state },
        basicSetup: { basic: true },
        python: () => ({ language: "python" })
      };
    }
  });
  await handle.ready;
  assert.equal(loadCount, 1);
  assert.equal(editorOptions.state.doc, getTrustedPythonProgram("binary_search"));
  assert.ok(editorOptions.state.extensions.some((extension) => extension?.editable === false));
  handle.dispose();
  assert.equal(destroyed, true);
});

test("trace result validation rejects malformed or excessive output", () => {
  assert.deepEqual(normalizePythonTraceResult({ steps: ["done"], sorted_values: [1, 2] }, "insertion_sort"), {
    steps: ["done"], sorted_values: [1, 2]
  });
  assert.throws(() => normalizePythonTraceResult({ steps: Array(129).fill("x") }, "linear_search"), /invalid trace/);
  assert.throws(() => normalizePythonTraceResult({ steps: ["ok", "x".repeat(241)], found_index: null }, "linear_search"), /Trace step/);
  assert.throws(() => normalizePythonTraceResult({ steps: ["found"], found_index: 1 }, { algorithm: "linear_search", values: [2, 3], target: 2 }), /inconsistent/);
  assert.throws(() => normalizePythonTraceResult({ steps: ["found"], found_index: 1 }, { algorithm: "linear_search", values: [4, 4, 7], target: 4 }), /inconsistent/);
  assert.equal(normalizePythonTraceResult({ steps: ["found"], found_index: 1 }, { algorithm: "binary_search", values: [4, 4, 7], target: 4 }).found_index, 1);
  assert.throws(() => normalizePythonTraceResult({ steps: ["done"], sorted_values: [2, 1] }, { algorithm: "insertion_sort", values: [1, 2] }), /inconsistent/);
});

test("insertion sort accepts the backend's null placeholder for an omitted search target", () => {
  const normalized = normalizePythonAlgorithmBlock({
    template_id: "python_algorithm_trace_v1",
    parameters: { algorithm: "insertion_sort", values: [3, 1, 2], target: null }
  });
  assert.deepEqual(normalized.parameters, { algorithm: "insertion_sort", values: [3, 1, 2] });
});
