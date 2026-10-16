import {
  getTrustedPythonProgram,
  normalizePythonAlgorithmBlock
} from "./python-activity-contract.js";

const MAX_OUTPUT_LENGTH = 8_000;
const RUNTIME_PATH_PREFIX = "/vendor/pyodide/";

function resolveRuntimeAsset(value, workerLocation) {
  if (typeof value !== "string" || value.length > 500) throw new TypeError("Invalid runtime asset URL.");
  const resolved = new URL(value, workerLocation.href);
  if (resolved.origin !== workerLocation.origin
    || resolved.protocol !== workerLocation.protocol
    || !resolved.pathname.startsWith(RUNTIME_PATH_PREFIX)) {
    throw new TypeError("Python runtime assets must be hosted in /vendor/pyodide/ on this site.");
  }
  return resolved.href;
}

function validateRequest(message, workerLocation) {
  if (!message || typeof message !== "object" || Array.isArray(message)
    || message.protocol_version !== 1 || message.type !== "run"
    || typeof message.request_id !== "string" || !/^python-[0-9]{1,12}$/u.test(message.request_id)
    || message.template_id !== "python_algorithm_trace_v1") {
    throw new TypeError("Invalid Python worker request.");
  }
  const allowedMessageKeys = new Set([
    "protocol_version", "type", "request_id", "template_id", "parameters",
    "runtime_module_url", "runtime_index_url"
  ]);
  if (Object.keys(message).some((key) => !allowedMessageKeys.has(key))) {
    throw new TypeError("Python worker request contains unsupported fields.");
  }
  if (!message.parameters || typeof message.parameters !== "object" || Array.isArray(message.parameters)
    || Object.keys(message.parameters).some((key) => !["algorithm", "values", "target"].includes(key))) {
    throw new TypeError("Python worker request contains unsupported parameters.");
  }
  const block = normalizePythonAlgorithmBlock({
    template_id: message.template_id,
    parameters: message.parameters
  });
  return {
    request_id: message.request_id,
    block,
    runtime_module_url: resolveRuntimeAsset(message.runtime_module_url, workerLocation),
    runtime_index_url: resolveRuntimeAsset(message.runtime_index_url, workerLocation)
  };
}

/**
 * Handle one isolated worker request. This deliberately executes only a
 * checked-in program selected by enum; model-authored source is never parsed
 * or passed to Pyodide.
 */
export async function runPythonWorkerRequest(message, {
  workerLocation,
  loadPyodide,
  emit
} = {}) {
  let requestId = typeof message?.request_id === "string" ? message.request_id : "python-invalid";
  let pyodide = null;
  try {
    if (!workerLocation?.href || !workerLocation?.origin || typeof loadPyodide !== "function" || typeof emit !== "function") {
      throw new TypeError("Python worker is not configured.");
    }
    const request = validateRequest(message, workerLocation);
    requestId = request.request_id;
    const runtime = await loadPyodide(request.runtime_module_url, request.runtime_index_url);
    pyodide = runtime;
    if (!runtime?.globals || typeof runtime.globals.set !== "function"
      || typeof runtime.runPythonAsync !== "function") {
      throw new TypeError("Pyodide runtime did not expose the expected API.");
    }

    runtime.globals.set("activity_input", JSON.stringify(request.block.parameters));
    // Only this app-owned fixed program is executed. The guide payload never
    // supplies Python text, imports, functions, or arbitrary runtime options.
    await runtime.runPythonAsync(getTrustedPythonProgram(request.block.parameters.algorithm));
    const outputJson = runtime.globals.get("activity_output");
    if (typeof outputJson !== "string" || outputJson.length > MAX_OUTPUT_LENGTH) {
      throw new RangeError("Python result exceeded the output limit.");
    }
    emit({ protocol_version: 1, type: "result", request_id: requestId, output_json: outputJson });
    return { ok: true, request_id: requestId };
  } catch (error) {
    emit({
      protocol_version: 1,
      type: "error",
      request_id: requestId,
      code: error instanceof RangeError ? "output_limit" : "runtime_error"
    });
    return { ok: false, request_id: requestId };
  } finally {
    try { pyodide?.globals?.delete?.("activity_input"); } catch { /* worker teardown follows */ }
    try { pyodide?.globals?.delete?.("activity_output"); } catch { /* worker teardown follows */ }
    try { pyodide?.destroy?.(); } catch { /* worker teardown follows */ }
  }
}
