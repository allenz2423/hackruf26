import {
  MAX_OUTPUT_LENGTH,
  getPythonExampleCode,
  getTrustedPythonProgram,
  normalizePythonAlgorithmBlock,
  normalizePythonTraceResult
} from "./python-activity-contract.js";

export {
  getPythonExampleCode,
  getTrustedPythonProgram,
  normalizePythonAlgorithmBlock,
  normalizePythonTraceResult
};

const MAX_TIMEOUT_MS = 60_000;
const MIN_TIMEOUT_MS = 1_000;
const DEFAULT_TIMEOUT_MS = 45_000;
const DEFAULT_PYODIDE_RUNTIME_URL = "/vendor/pyodide/pyodide.mjs";
const DEFAULT_PYODIDE_INDEX_URL = "/vendor/pyodide/";

let nextRequestId = 0;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/gu, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

export function renderPythonActivity(block) {
  const normalized = normalizePythonAlgorithmBlock(block);
  const code = getTrustedPythonProgram(normalized.parameters.algorithm);
  const args = escapeHtml(JSON.stringify(normalized.parameters));
  const safeCode = escapeHtml(code);
  const safeTitle = escapeHtml(normalized.title);
  const safeAlt = escapeHtml(normalized.alt_text);
  return `<section class="python-algorithm-activity" data-python-activity aria-label="${safeTitle}">
    <h3>${safeTitle}</h3>
    <p>${safeAlt}</p>
    <div class="python-code-window" role="group" aria-label="Python code example">
      <div class="python-code-toolbar" aria-hidden="true"><span class="python-code-window-dots"><i></i><i></i><i></i></span><span class="python-code-filename">example.py</span><span class="python-code-language">Python</span><span class="python-code-readonly">Read only</span></div>
      <div class="python-code-editor" data-python-editor aria-label="Read-only Python example"></div>
    </div>
    <details><summary>Text version of the Python example</summary><pre><code>${safeCode}</code></pre></details>
    <p>Input: <code>${args}</code></p>
    <button type="button" data-python-run>Run this fixed example</button>
    <button type="button" data-python-cancel disabled>Cancel run</button>
    <p data-python-status role="status" aria-live="polite">The Python runtime loads only when you run the example.</p>
    <ol data-python-output aria-label="Algorithm trace"></ol>
    <p data-python-fallback hidden>This interactive trace is unavailable. The Python example and input above remain available as text.</p>
  </section>`;
}

function createDefaultWorker() {
  if (typeof Worker !== "function") return null;
  return new Worker(new URL("./python-activity-worker.js", import.meta.url), { type: "module", name: "study-guide-python-example" });
}

function safeSameOriginUrl(url, baseUrl) {
  const base = new URL(baseUrl);
  const resolved = new URL(url, base);
  if (resolved.origin !== base.origin || !["https:", "http:"].includes(resolved.protocol)) {
    throw new TypeError("Pyodide must be loaded from this site.");
  }
  return resolved.href;
}

function createText(element, text) {
  element.textContent = text;
  return element;
}

/**
 * Mount a fixed-template Python walkthrough. Arbitrary source is never sent to
 * Pyodide: the worker selects one reviewed program by enum and receives only
 * bounded integer inputs. CodeMirror and Pyodide are supplied lazily by host
 * integration code, keeping this adapter usable when those optional bundles
 * are not installed.
 */
export function mountPythonActivity(container, block, {
  codeMirrorLoader = null,
  workerFactory = createDefaultWorker,
  runtimeModuleUrl = DEFAULT_PYODIDE_RUNTIME_URL,
  runtimeIndexUrl = DEFAULT_PYODIDE_INDEX_URL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  setTimeoutFn = globalThis.setTimeout,
  clearTimeoutFn = globalThis.clearTimeout,
  onStatus = null
} = {}) {
  let normalized;
  try {
    normalized = normalizePythonAlgorithmBlock(block);
  } catch (error) {
    return createFailedHandle(container, error);
  }
  if (!container?.ownerDocument?.createElement || typeof container.replaceChildren !== "function") {
    return createFailedHandle(container, new TypeError("Python activity needs a DOM container."));
  }

  const doc = container.ownerDocument;
  const shell = doc.createElement("section");
  shell.className = "python-algorithm-activity";
  shell.setAttribute("aria-label", normalized.title);
  const heading = createText(doc.createElement("h3"), normalized.title);
  const description = createText(doc.createElement("p"), normalized.alt_text);
  const codeWindow = doc.createElement("div");
  codeWindow.className = "python-code-window";
  codeWindow.setAttribute("role", "group");
  codeWindow.setAttribute("aria-label", "Python code example");
  const toolbar = doc.createElement("div");
  toolbar.className = "python-code-toolbar";
  toolbar.setAttribute("aria-hidden", "true");
  const dots = doc.createElement("span");
  dots.className = "python-code-window-dots";
  for (let index = 0; index < 3; index += 1) dots.append(doc.createElement("i"));
  const filename = createText(doc.createElement("span"), "example.py");
  filename.className = "python-code-filename";
  const language = createText(doc.createElement("span"), "Python");
  language.className = "python-code-language";
  const readOnly = createText(doc.createElement("span"), "Read only");
  readOnly.className = "python-code-readonly";
  toolbar.append(dots, filename, language, readOnly);
  const editorHost = doc.createElement("div");
  editorHost.className = "python-code-editor";
  editorHost.setAttribute("aria-label", "Read-only Python example");
  codeWindow.append(toolbar, editorHost);
  const textDetails = doc.createElement("details");
  const summary = createText(doc.createElement("summary"), "Text version of the Python example");
  const code = doc.createElement("pre");
  const codeElement = createText(doc.createElement("code"), getTrustedPythonProgram(normalized.parameters.algorithm));
  code.append(codeElement);
  textDetails.append(summary, code);
  const input = doc.createElement("p");
  input.append(doc.createTextNode("Input: "));
  input.append(createText(doc.createElement("code"), JSON.stringify(normalized.parameters)));
  const runButton = createText(doc.createElement("button"), "Run this fixed example");
  runButton.type = "button";
  const cancelButton = createText(doc.createElement("button"), "Cancel run");
  cancelButton.type = "button";
  cancelButton.disabled = true;
  const status = createText(doc.createElement("p"), "The Python runtime loads only when you run the example.");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const output = doc.createElement("ol");
  output.setAttribute("aria-label", "Algorithm trace");
  const fallback = createText(doc.createElement("p"), "This interactive trace is unavailable. The Python example and input above remain available as text.");
  fallback.hidden = true;

  shell.append(heading, description, codeWindow, textDetails, input, runButton, cancelButton, status, output, fallback);
  container.replaceChildren(shell);

  let disposed = false;
  let activeWorker = null;
  let activeRun = null;
  let editorView = null;
  const actualTimeoutMs = Math.max(MIN_TIMEOUT_MS, Math.min(MAX_TIMEOUT_MS, Number.isFinite(timeoutMs) ? timeoutMs : DEFAULT_TIMEOUT_MS));

  const updateStatus = (message) => {
    status.textContent = message;
    try { onStatus?.(message); } catch { /* status callback is optional */ }
  };

  async function loadEditor() {
    if (typeof codeMirrorLoader !== "function" || disposed) return;
    try {
      const cm = await codeMirrorLoader();
      if (disposed || !cm?.EditorView || !cm?.EditorState || typeof cm?.python !== "function") return;
      const editorTheme = cm.HighlightStyle?.define && cm.syntaxHighlighting && cm.tags
        ? [
          cm.syntaxHighlighting(cm.HighlightStyle.define([
            { tag: cm.tags.keyword, color: "#ff7b72" },
            { tag: cm.tags.definition(cm.tags.variableName), color: "#d2a8ff" },
            { tag: cm.tags.function(cm.tags.variableName), color: "#d2a8ff" },
            { tag: cm.tags.string, color: "#a5d6ff" },
            { tag: cm.tags.number, color: "#79c0ff" },
            { tag: cm.tags.bool, color: "#79c0ff" },
            { tag: cm.tags.comment, color: "#8b949e", fontStyle: "italic" },
            { tag: cm.tags.operator, color: "#ff7b72" },
            { tag: cm.tags.typeName, color: "#ffa657" },
            { tag: cm.tags.propertyName, color: "#e6edf3" }
          ])),
          cm.EditorView.theme({
            "&": { color: "#e6edf3", backgroundColor: "#0d1117" },
            ".cm-content": { caretColor: "transparent" },
            ".cm-focused": { outline: "none" }
          }, { dark: true })
        ]
        : [];
      editorView = new cm.EditorView({
        state: cm.EditorState.create({
          doc: getTrustedPythonProgram(normalized.parameters.algorithm),
          extensions: [
            ...(cm.basicSetup ? [cm.basicSetup] : []),
            cm.python(),
            ...editorTheme,
            cm.EditorView.editable.of(false),
            cm.EditorView.contentAttributes.of({
              "aria-label": `${normalized.title} Python example, read only`,
              "aria-readonly": "true"
            })
          ]
        }),
        parent: editorHost
      });
    } catch {
      editorView = null;
    }
  }

  function endRun(result, statusMessage) {
    const pending = activeRun;
    if (!pending) return;
    if (pending.timer !== null) clearTimeoutFn(pending.timer);
    pending.timer = null;
    const worker = activeWorker;
    activeWorker = null;
    activeRun = null;
    try { worker?.terminate?.(); } catch { /* worker already stopped */ }
    cancelButton.disabled = true;
    runButton.disabled = false;
    updateStatus(statusMessage);
    if (result?.ok && !disposed) {
      output.replaceChildren(...result.trace.steps.map((step) => createText(doc.createElement("li"), step)));
      fallback.hidden = true;
    } else if (!disposed && result?.reason !== "cancelled") {
      fallback.hidden = false;
    }
    pending.resolve(result);
  }

  function cancelRun() {
    if (!activeRun) return;
    endRun({ ok: false, reason: "cancelled" }, "Run cancelled. The worker was stopped.");
  }

  async function run() {
    if (disposed || activeRun) return { ok: false, reason: disposed ? "disposed" : "already_running" };
    output.replaceChildren();
    fallback.hidden = true;
    runButton.disabled = true;
    cancelButton.disabled = false;
    updateStatus("Starting the isolated Python worker…");

    let worker;
    try {
      worker = workerFactory?.();
      if (!worker || typeof worker.postMessage !== "function" || typeof worker.terminate !== "function") {
        throw new Error("Worker unavailable");
      }
      const pageUrl = doc.defaultView?.location?.href || globalThis.location?.href || "http://localhost/";
      const runtimeUrl = safeSameOriginUrl(runtimeModuleUrl, pageUrl);
      const indexUrl = safeSameOriginUrl(runtimeIndexUrl, pageUrl);
      const requestId = `python-${++nextRequestId}`;

      return await new Promise((resolve) => {
        const pending = { resolve, timer: null, requestId };
        activeRun = pending;
        activeWorker = worker;
        pending.timer = setTimeoutFn(() => {
          endRun({ ok: false, reason: "timeout" }, "Python run timed out and the worker was terminated.");
        }, actualTimeoutMs);

        worker.onmessage = (event) => {
          if (disposed || !activeRun || event?.data?.request_id !== requestId) return;
          const message = event.data;
          if (message.protocol_version === 1 && message.type === "result") {
            try {
              if (typeof message.output_json !== "string" || message.output_json.length > MAX_OUTPUT_LENGTH) {
                throw new TypeError("Python output exceeded the size limit.");
              }
              const result = normalizePythonTraceResult(JSON.parse(message.output_json), normalized.parameters);
              endRun({ ok: true, trace: result }, "Python example finished.");
            } catch (error) {
              endRun({ ok: false, reason: "invalid_output", error }, "The Python worker returned an invalid result.");
            }
          } else {
            endRun({ ok: false, reason: "runtime_error" }, "Python runtime unavailable. The fixed code example remains available as text.");
          }
        };
        worker.onerror = () => {
          if (activeRun?.requestId === requestId) {
            endRun({ ok: false, reason: "worker_error" }, "Python worker failed. The fixed code example remains available as text.");
          }
        };
        updateStatus("Loading Pyodide in the isolated worker…");
        try {
          worker.postMessage({
            protocol_version: 1,
            type: "run",
            request_id: requestId,
            template_id: normalized.template_id,
            parameters: normalized.parameters,
            runtime_module_url: runtimeUrl,
            runtime_index_url: indexUrl
          });
        } catch (error) {
          endRun({ ok: false, reason: "dispatch_error", error }, "Could not start the isolated Python worker.");
        }
      });
    } catch (error) {
      try { worker?.terminate?.(); } catch { /* unavailable worker */ }
      runButton.disabled = false;
      cancelButton.disabled = true;
      fallback.hidden = false;
      updateStatus("Python runtime unavailable. The fixed code example remains available as text.");
      return { ok: false, reason: "unavailable", error };
    }
  }

  runButton.addEventListener("click", run);
  cancelButton.addEventListener("click", cancelRun);
  const ready = loadEditor().then(() => ({ ok: true }));

  return {
    ready,
    run,
    cancel: cancelRun,
    dispose() {
      if (disposed) return;
      disposed = true;
      if (activeRun) endRun({ ok: false, reason: "cancelled" }, "Activity closed.");
      runButton.removeEventListener("click", run);
      cancelButton.removeEventListener("click", cancelRun);
      editorView?.destroy?.();
      editorView = null;
      container.replaceChildren();
    }
  };
}

function createFailedHandle(container, error) {
  const doc = container?.ownerDocument;
  if (doc?.createElement && typeof container.replaceChildren === "function") {
    const message = doc.createElement("p");
    message.textContent = "Python activity could not be opened. Its code must use a supported fixed template.";
    message.setAttribute("role", "status");
    container.replaceChildren(message);
  }
  return {
    ready: Promise.resolve({ ok: false, error }),
    run: async () => ({ ok: false, reason: "invalid_block", error }),
    cancel() {},
    dispose() { container?.replaceChildren?.(); }
  };
}
