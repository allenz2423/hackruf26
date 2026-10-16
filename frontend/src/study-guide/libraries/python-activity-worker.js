import { runPythonWorkerRequest } from "./python-activity-worker-core.js";

async function loadPyodideFromSite(runtimeModuleUrl, runtimeIndexUrl) {
  const runtimeModule = await import(runtimeModuleUrl);
  const loadPyodide = runtimeModule.loadPyodide || runtimeModule.default?.loadPyodide;
  if (typeof loadPyodide !== "function") throw new TypeError("Pyodide loader is unavailable.");
  return loadPyodide({ indexURL: runtimeIndexUrl });
}

self.addEventListener("message", (event) => {
  void runPythonWorkerRequest(event.data, {
    workerLocation: self.location,
    loadPyodide: loadPyodideFromSite,
    emit: (message) => self.postMessage(message)
  });
});
