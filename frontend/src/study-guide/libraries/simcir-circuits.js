const SVG_NS = "http://www.w3.org/2000/svg";
const activeMounts = new WeakMap();
let simcirLoadPromise = null;
let simcirAssetUrlsPromise = null;
let fallbackSequence = 0;

function freezeCircuit({ title, description, inputs, outputs, gates, logic, width, height, devices, connectors }) {
  return Object.freeze({
    title,
    description,
    inputs: Object.freeze([...inputs]),
    outputs: Object.freeze([...outputs]),
    gates: Object.freeze([...gates]),
    logic,
    width,
    height,
    devices: Object.freeze(devices.map((device) => Object.freeze({
      ...device,
      ...(device.state ? { state: Object.freeze({ ...device.state }) } : {})
    }))),
    connectors: Object.freeze(connectors.map((connector) => Object.freeze({ ...connector })))
  });
}

/**
 * Reviewed, app-owned circuit definitions only. Model content can select an ID;
 * it cannot contribute Simcir JSON, component types, labels, wires, or code.
 */
export const SIMCIR_CIRCUIT_CATALOG = Object.freeze({
  and_gate: freezeCircuit({
    title: "AND gate",
    description: "The output is 1 only when both inputs are 1.",
    inputs: ["A", "B"],
    outputs: ["Y"],
    gates: ["AND"],
    logic: (a, b) => ({ Y: a && b }),
    width: 440,
    height: 176,
    devices: [
      { type: "DC", id: "powerA", x: 20, y: 34, label: "1" },
      { type: "Toggle", id: "A", x: 82, y: 34, label: "A", state: { on: false } },
      { type: "DC", id: "powerB", x: 20, y: 100, label: "1" },
      { type: "Toggle", id: "B", x: 82, y: 100, label: "B", state: { on: false } },
      { type: "AND", id: "gate", x: 224, y: 62, label: "AND" },
      { type: "LED", id: "Y", x: 334, y: 62, label: "Y" }
    ],
    connectors: [
      { from: "powerA.out0", to: "A.in0" }, { from: "powerB.out0", to: "B.in0" },
      { from: "A.out0", to: "gate.in0" }, { from: "B.out0", to: "gate.in1" },
      { from: "gate.out0", to: "Y.in0" }
    ]
  }),
  or_gate: freezeCircuit({
    title: "OR gate",
    description: "The output is 1 when at least one input is 1.",
    inputs: ["A", "B"],
    outputs: ["Y"],
    gates: ["OR"],
    logic: (a, b) => ({ Y: a || b }),
    width: 440,
    height: 176,
    devices: [
      { type: "DC", id: "powerA", x: 20, y: 34, label: "1" },
      { type: "Toggle", id: "A", x: 82, y: 34, label: "A", state: { on: false } },
      { type: "DC", id: "powerB", x: 20, y: 100, label: "1" },
      { type: "Toggle", id: "B", x: 82, y: 100, label: "B", state: { on: false } },
      { type: "OR", id: "gate", x: 224, y: 62, label: "OR" },
      { type: "LED", id: "Y", x: 334, y: 62, label: "Y" }
    ],
    connectors: [
      { from: "powerA.out0", to: "A.in0" }, { from: "powerB.out0", to: "B.in0" },
      { from: "A.out0", to: "gate.in0" }, { from: "B.out0", to: "gate.in1" },
      { from: "gate.out0", to: "Y.in0" }
    ]
  }),
  half_adder: freezeCircuit({
    title: "Half adder",
    description: "A half adder adds two one-bit inputs. XOR gives the sum; AND gives the carry.",
    inputs: ["A", "B"],
    outputs: ["SUM", "CARRY"],
    gates: ["XOR", "AND"],
    logic: (a, b) => ({ SUM: a !== b, CARRY: a && b }),
    width: 520,
    height: 208,
    devices: [
      { type: "DC", id: "powerA", x: 20, y: 50, label: "1" },
      { type: "Toggle", id: "A", x: 82, y: 50, label: "A", state: { on: false } },
      { type: "DC", id: "powerB", x: 20, y: 122, label: "1" },
      { type: "Toggle", id: "B", x: 82, y: 122, label: "B", state: { on: false } },
      { type: "XOR", id: "sumGate", x: 226, y: 38, label: "XOR" },
      { type: "AND", id: "carryGate", x: 226, y: 120, label: "AND" },
      { type: "LED", id: "sum", x: 356, y: 38, label: "SUM" },
      { type: "LED", id: "carry", x: 356, y: 120, label: "CARRY" }
    ],
    connectors: [
      { from: "powerA.out0", to: "A.in0" }, { from: "powerB.out0", to: "B.in0" },
      { from: "A.out0", to: "sumGate.in0" }, { from: "B.out0", to: "sumGate.in1" },
      { from: "A.out0", to: "carryGate.in0" }, { from: "B.out0", to: "carryGate.in1" },
      { from: "sumGate.out0", to: "sum.in0" }, { from: "carryGate.out0", to: "carry.in0" }
    ]
  })
});

export const SIMCIR_CIRCUIT_IDS = Object.freeze(Object.keys(SIMCIR_CIRCUIT_CATALOG));

export function getSimcirCircuitDefinition(circuitId) {
  if (typeof circuitId !== "string" || !Object.hasOwn(SIMCIR_CIRCUIT_CATALOG, circuitId)) return null;
  return SIMCIR_CIRCUIT_CATALOG[circuitId];
}

/** Return a fresh fixed-schema simulator description for a catalog ID. */
export function buildSimcirCircuitData(circuitId, inputValues = {}) {
  const circuit = getSimcirCircuitDefinition(circuitId);
  if (!circuit) return null;
  const inputs = Object.fromEntries(circuit.inputs.map((input) => [input, Boolean(inputValues[input])]));
  return {
    width: circuit.width,
    height: circuit.height,
    showToolbox: false,
    editable: false,
    toolbox: [],
    devices: circuit.devices.map((device) => ({
      ...device,
      ...(device.type === "Toggle" ? { state: { on: inputs[device.id] } } : {}),
      ...(device.state ? { state: { ...device.state, ...(device.type === "Toggle" ? { on: inputs[device.id] } : {}) } } : {})
    })),
    connectors: circuit.connectors.map((connector) => ({ ...connector }))
  };
}

async function getSimcirAssetUrls() {
  if (!simcirAssetUrlsPromise) {
    simcirAssetUrlsPromise = Promise.all([
      import("./vendor/simcirjs/simcir.js?url"),
      import("./vendor/simcirjs/simcir-basicset.js?url"),
      import("./vendor/simcirjs/simcir.css?url"),
      import("./vendor/simcirjs/simcir-basicset.css?url")
    ]).then(([core, basicSet, coreCss, basicSetCss]) => ({
      core: core.default,
      basicSet: basicSet.default,
      styles: [coreCss.default, basicSetCss.default]
    })).catch((error) => {
      simcirAssetUrlsPromise = null;
      throw error;
    });
  }
  return simcirAssetUrlsPromise;
}

function addStyles(document, stylesheetUrls) {
  for (const href of stylesheetUrls) {
    const exists = [...(document.head?.querySelectorAll?.('link[data-simcir-style="true"]') || [])]
      .some((link) => link.href === href);
    if (exists) continue;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.simcirStyle = "true";
    document.head?.append(link);
  }
}

function appendClassicScript(document, sourceUrl) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = sourceUrl;
    script.async = false;
    script.dataset.simcirAsset = "true";
    script.onload = () => resolve();
    script.onerror = () => {
      script.remove();
      reject(new Error("A self-hosted SimcirJS asset failed to load."));
    };
    document.head.append(script);
  });
}

/** Load the locally bundled classic scripts; no CDN or third-party request. */
export async function loadSimcirApi(document = globalThis.document, view = globalThis.window) {
  if (!document?.head || !view) throw new Error("SimcirJS requires a browser document.");
  if (view.simcir?.__studyGuideBasicSetLoaded) return view.simcir;
  if (!simcirLoadPromise) {
    simcirLoadPromise = (async () => {
      const assets = await getSimcirAssetUrls();
      addStyles(document, assets.styles);
      if (!view.simcir?.setupSimcir) {
        await appendClassicScript(document, assets.core);
      }
      if (!view.simcir?.setupSimcir) throw new Error("SimcirJS core did not expose its browser API.");
      await appendClassicScript(document, assets.basicSet);
      if (!view.simcir?.registerDevice) throw new Error("SimcirJS basic gates did not initialize.");
      view.simcir.__studyGuideBasicSetLoaded = true;
      return view.simcir;
    })().catch((error) => {
      simcirLoadPromise = null;
      throw error;
    });
  }
  return simcirLoadPromise;
}

function svgElement(document, name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function renderStaticSchematic(document, circuit, inputValues, outputValues) {
  const wrapper = document.createElement("div");
  wrapper.className = "simcir-circuit-static-fallback";
  const instance = ++fallbackSequence;
  const titleId = `circuit-title-${instance}`;
  const descriptionId = `circuit-description-${instance}`;
  const svg = svgElement(document, "svg", {
    viewBox: `0 0 ${circuit.width} ${circuit.height}`,
    role: "img",
    "aria-labelledby": `${titleId} ${descriptionId}`,
    width: circuit.width,
    height: circuit.height
  });
  svg.style.maxWidth = "100%";
  svg.style.height = "auto";
  const title = svgElement(document, "title", { id: titleId });
  title.textContent = `${circuit.title} circuit schematic`;
  const desc = svgElement(document, "desc", { id: descriptionId });
  desc.textContent = `${circuit.description} ${circuit.inputs.map((name) => `${name} input ${inputValues[name] ? 1 : 0}`).join(", ")}. ${circuit.outputs.map((name) => `${name} output ${outputValues[name] ? 1 : 0}`).join(", ")}.`;
  svg.append(title, desc);

  const gateCount = circuit.gates.length;
  const gateX = Math.round(circuit.width * 0.49);
  const outputX = Math.round(circuit.width * 0.81);
  const gateStep = circuit.height / (gateCount + 1);
  circuit.inputs.forEach((name, index) => {
    const y = Math.round(circuit.height * (index + 1) / (circuit.inputs.length + 1));
    const label = svgElement(document, "text", { x: 28, y: y - 8, "font-size": 16, "font-weight": 700 });
    label.textContent = `${name} = ${inputValues[name] ? 1 : 0}`;
    svg.append(label);
    svg.append(svgElement(document, "path", { d: `M 112 ${y - 12} H ${gateX - 42}`, stroke: "#475569", "stroke-width": 3, fill: "none" }));
  });
  circuit.gates.forEach((gate, index) => {
    const y = Math.round(gateStep * (index + 1));
    const box = svgElement(document, "rect", { x: gateX - 40, y: y - 23, width: 80, height: 46, rx: 6, fill: "#eff6ff", stroke: "#1d4ed8", "stroke-width": 2 });
    const label = svgElement(document, "text", { x: gateX, y: y + 6, "text-anchor": "middle", "font-size": 14, "font-weight": 700 });
    label.textContent = gate;
    svg.append(box, label);
  });
  circuit.outputs.forEach((name, index) => {
    const y = Math.round(circuit.height * (index + 1) / (circuit.outputs.length + 1));
    const wire = svgElement(document, "path", { d: `M ${gateX + 42} ${y} H ${outputX - 10}`, stroke: "#475569", "stroke-width": 3, fill: "none" });
    const label = svgElement(document, "text", { x: outputX, y: y + 5, "font-size": 16, "font-weight": 700 });
    label.textContent = `${name} = ${outputValues[name] ? 1 : 0}`;
    svg.append(wire, label);
  });
  wrapper.append(svg);
  return wrapper;
}

function defaultInputValues(circuit) {
  return Object.fromEntries(circuit.inputs.map((name) => [name, false]));
}

function createRoot(document, circuit) {
  const root = document.createElement("section");
  root.className = "simcir-circuit-activity";
  root.setAttribute("aria-label", `${circuit.title} interactive activity`);
  const heading = document.createElement("h4");
  heading.textContent = circuit.title;
  const description = document.createElement("p");
  description.textContent = circuit.description;
  const controls = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = "Input values";
  controls.append(legend);
  const inputButtons = new Map();
  for (const input of circuit.inputs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "simcir-circuit-input";
    button.setAttribute("aria-pressed", "false");
    button.textContent = `${input}: 0`;
    controls.append(button);
    inputButtons.set(input, button);
  }
  const outputs = document.createElement("p");
  outputs.className = "simcir-circuit-output";
  outputs.setAttribute("role", "status");
  outputs.setAttribute("aria-live", "polite");
  const simulator = document.createElement("div");
  simulator.className = "simcir-circuit-simulator";
  simulator.setAttribute("aria-hidden", "true");
  simulator.style.maxWidth = "100%";
  const fallbackSlot = document.createElement("div");
  fallbackSlot.className = "simcir-circuit-fallback-slot";
  const note = document.createElement("p");
  note.className = "simcir-circuit-note";
  note.textContent = "Use the input buttons to change the circuit. The truth table and output labels provide the same information as the drawing.";
  root.append(heading, description, controls, outputs, simulator, fallbackSlot, note);
  return { root, controls, inputButtons, outputs, simulator, fallbackSlot };
}

/**
 * Mount a fixed SimcirJS circuit. The only content input is a reviewed circuit
 * ID. Buttons are the accessible canonical controls; each change rebuilds the
 * fixed circuit with the selected input values.
 */
export function mountSimcirCircuit(container, circuitId, {
  simcirApi = null,
  loadApi = loadSimcirApi,
  ResizeObserverClass = globalThis.ResizeObserver
} = {}) {
  const circuit = getSimcirCircuitDefinition(circuitId);
  if (!container?.ownerDocument || typeof container.append !== "function") {
    return { ready: Promise.resolve({ ok: false, mode: "unavailable" }), mode: "unavailable", resize() {}, cleanup() {} };
  }
  activeMounts.get(container)?.cleanup();
  if (!circuit) {
    const status = container.ownerDocument.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = "This circuit is not in the reviewed activity catalog.";
    container.append(status);
    let cleaned = false;
    const handle = {
      ready: Promise.resolve({ ok: false, mode: "unavailable" }),
      mode: "unavailable",
      resize() {},
      cleanup() { if (!cleaned) { cleaned = true; status.remove(); } }
    };
    activeMounts.set(container, handle);
    return handle;
  }

  const document = container.ownerDocument;
  const view = document.defaultView || globalThis.window;
  const { root, inputButtons, outputs, simulator, fallbackSlot } = createRoot(document, circuit);
  container.append(root);
  let inputs = defaultInputValues(circuit);
  let api = simcirApi;
  let observer = null;
  let active = true;
  let mode = "loading";
  let fallback = null;
  const buttonListeners = [];

  function evaluate() {
    return circuit.logic(...circuit.inputs.map((name) => inputs[name]));
  }

  function updateAccessibleState() {
    if (!active) return;
    const result = evaluate();
    for (const [name, button] of inputButtons) {
      button.setAttribute("aria-pressed", String(inputs[name]));
      button.textContent = `${name}: ${inputs[name] ? 1 : 0}`;
    }
    outputs.textContent = circuit.outputs.map((name) => `${name} = ${result[name] ? 1 : 0}`).join(" · ");
    fallback?.remove();
    fallback = renderStaticSchematic(document, circuit, inputs, result);
    fallback.hidden = mode === "simcir";
    fallbackSlot.append(fallback);
  }

  function clearSimcir() {
    if (!api) return;
    try {
      api.clearSimcir?.(api.$(simulator));
    } catch { /* fall through to removing our private mount */ }
  }

  function renderSimcir() {
    if (!active || !api) return;
    try {
      clearSimcir();
      api.setupSimcir(api.$(simulator), buildSimcirCircuitData(circuitId, inputs));
      const workspace = simulator.querySelector?.(".simcir-workspace");
      if (workspace) {
        workspace.style.maxWidth = "100%";
        workspace.style.height = "auto";
      }
      mode = "simcir";
      updateAccessibleState();
    } catch (error) {
      clearSimcir();
      mode = "fallback";
      simulator.replaceChildren?.();
      updateAccessibleState();
      return error;
    }
    return null;
  }

  for (const [name, button] of inputButtons) {
    const listener = () => {
      inputs = { ...inputs, [name]: !inputs[name] };
      updateAccessibleState();
      if (api) renderSimcir();
    };
    button.addEventListener("click", listener);
    buttonListeners.push([button, listener]);
  }
  updateAccessibleState();

  function resize() {
    if (!active) return;
    const workspace = simulator.querySelector?.(".simcir-workspace");
    if (workspace) {
      workspace.style.maxWidth = "100%";
      workspace.style.height = "auto";
    }
    if (fallback) fallback.querySelector?.("svg")?.style && (fallback.querySelector("svg").style.maxWidth = "100%");
  }

  const ready = (async () => {
    try {
      api ||= await loadApi(document, view);
      if (!active) return { ok: false, mode: "cancelled" };
      if (!api?.setupSimcir || !api?.clearSimcir || typeof api.$ !== "function") {
        throw new Error("SimcirJS API is incomplete.");
      }
      const error = renderSimcir();
      if (error) return { ok: false, mode: "fallback", error };
      return { ok: true, mode: "simcir" };
    } catch (error) {
      if (!active) return { ok: false, mode: "cancelled" };
      mode = "fallback";
      updateAccessibleState();
      return { ok: false, mode: "fallback", error };
    }
  })();

  if (typeof ResizeObserverClass === "function") {
    observer = new ResizeObserverClass(resize);
    observer.observe(root);
  }
  const handle = {
    get mode() { return mode; },
    get inputs() { return { ...inputs }; },
    get outputs() { return { ...evaluate() }; },
    ready,
    resize,
    cleanup() {
      if (!active) return;
      active = false;
      observer?.disconnect?.();
      for (const [button, listener] of buttonListeners) button.removeEventListener("click", listener);
      clearSimcir();
      root.remove();
      if (activeMounts.get(container) === handle) activeMounts.delete(container);
    }
  };
  activeMounts.set(container, handle);
  return handle;
}
