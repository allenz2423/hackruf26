import test from "node:test";
import assert from "node:assert/strict";
import {
  SIMCIR_CIRCUIT_CATALOG,
  SIMCIR_CIRCUIT_IDS,
  buildSimcirCircuitData,
  getSimcirCircuitDefinition,
  mountSimcirCircuit
} from "../src/study-guide/libraries/simcir-circuits.js";

class FakeNode {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName;
    this.ownerDocument = ownerDocument;
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
    this.style = {};
    this.dataset = {};
    this.hidden = false;
    this.parentNode = null;
    this._text = "";
    this.className = "";
    this.classList = {
      contains: (name) => this.className.split(/\s+/u).includes(name)
    };
  }

  set textContent(value) {
    this._text = String(value);
    this.replaceChildren();
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  append(...nodes) {
    for (const node of nodes) {
      node.remove?.();
      node.parentNode = this;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    if (nodes.length) this.append(...nodes);
  }

  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.children;
    const index = siblings.indexOf(this);
    if (index >= 0) siblings.splice(index, 1);
    this.parentNode = null;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type) {
    for (const listener of this.listeners.get(type) || []) listener({ type, target: this });
  }

  querySelector(selector) {
    const className = selector.startsWith(".") ? selector.slice(1) : null;
    for (const child of this.children) {
      if (className && child.className.split(/\s+/u).includes(className)) return child;
      if (!className && child.tagName === selector) return child;
      const nested = child.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  }
}

class FakeDocument {
  constructor() {
    this.defaultView = {};
    this.head = new FakeNode("head", this);
  }

  createElement(name) { return new FakeNode(name, this); }
  createElementNS(_namespace, name) { return new FakeNode(name, this); }
}

class FakeContainer extends FakeNode {
  constructor() {
    const document = new FakeDocument();
    super("container", document);
  }
}

function makeFakeSimcir({ fail = false } = {}) {
  const calls = { setup: [], clear: 0 };
  const api = {
    $(element) { return [element]; },
    clearSimcir([target]) {
      calls.clear += 1;
      target.replaceChildren();
    },
    setupSimcir([target], data) {
      if (fail) throw new Error("Simcir fixture failure");
      calls.setup.push(data);
      const workspace = target.ownerDocument.createElement("svg");
      workspace.className = "simcir-workspace";
      target.append(workspace);
    }
  };
  return { api, calls };
}

function findButton(root, label) {
  return root.children.flatMap((child) => child.children)
    .find((child) => child.className === "simcir-circuit-input" && child.textContent === `${label}: 0`)
    || root.children.flatMap((child) => child.children)
      .find((child) => child.className === "simcir-circuit-input" && child.textContent.startsWith(`${label}:`));
}

test("catalog is a fixed allowlist and circuit data cannot accept arbitrary simulator JSON", () => {
  assert.deepEqual(SIMCIR_CIRCUIT_IDS, ["and_gate", "or_gate", "half_adder"]);
  assert.equal(Object.isFrozen(SIMCIR_CIRCUIT_CATALOG), true);
  assert.equal(getSimcirCircuitDefinition("__proto__"), null);
  assert.equal(getSimcirCircuitDefinition("constructor"), null);
  assert.equal(getSimcirCircuitDefinition({ id: "and_gate", devices: [{ type: "Script" }] }), null);
  assert.equal(buildSimcirCircuitData("arbitrary", { A: true }), null);

  const data = buildSimcirCircuitData("half_adder", { A: true, B: false, devices: [{ type: "Unsafe" }] });
  assert.equal(data.editable, false);
  assert.equal(data.showToolbox, false);
  assert.deepEqual(data.toolbox, []);
  assert.deepEqual(data.devices.filter((device) => device.type === "Toggle").map(({ id, state }) => [id, state.on]), [
    ["A", true], ["B", false]
  ]);
  assert.ok(data.devices.every(({ type }) => ["DC", "Toggle", "XOR", "AND", "LED"].includes(type)));
  assert.ok(data.connectors.every(({ from, to }) => /^[A-Za-z0-9_]+\.(in|out)\d+$/u.test(from) && /^[A-Za-z0-9_]+\.(in|out)\d+$/u.test(to)));
  assert.equal(Object.hasOwn(data, "devicesData"), false);
});

test("logic outputs for AND, OR, and half-adder match their complete truth tables", () => {
  const expected = {
    and_gate: [[0, 0, [0]], [0, 1, [0]], [1, 0, [0]], [1, 1, [1]]],
    or_gate: [[0, 0, [0]], [0, 1, [1]], [1, 0, [1]], [1, 1, [1]]],
    half_adder: [[0, 0, [0, 0]], [0, 1, [1, 0]], [1, 0, [1, 0]], [1, 1, [0, 1]]]
  };
  for (const [id, rows] of Object.entries(expected)) {
    const circuit = getSimcirCircuitDefinition(id);
    for (const [a, b, outputBits] of rows) {
      const output = circuit.logic(Boolean(a), Boolean(b));
      assert.deepEqual(circuit.outputs.map((name) => Number(output[name])), outputBits, `${id}: A=${a}, B=${b}`);
    }
  }
});

test("mount uses the injected Simcir API and accessible controls rebuild the fixed circuit", async () => {
  const container = new FakeContainer();
  const { api, calls } = makeFakeSimcir();
  const handle = mountSimcirCircuit(container, "and_gate", { simcirApi: api });
  assert.deepEqual(await handle.ready, { ok: true, mode: "simcir" });
  assert.equal(calls.setup.length, 1);
  assert.equal(calls.setup[0].devices.some((device) => device.type === "AND"), true);
  assert.deepEqual(handle.outputs, { Y: false });

  const root = container.children.find((child) => child.className === "simcir-circuit-activity");
  findButton(root, "A").dispatch("click");
  assert.deepEqual(handle.inputs, { A: true, B: false });
  assert.deepEqual(handle.outputs, { Y: false });
  findButton(root, "B").dispatch("click");
  assert.deepEqual(handle.inputs, { A: true, B: true });
  assert.deepEqual(handle.outputs, { Y: true });
  assert.equal(calls.setup.length, 3);
  assert.equal(root.querySelector(".simcir-circuit-output").getAttribute("aria-live"), "polite");
  assert.equal(root.querySelector(".simcir-circuit-simulator").getAttribute("aria-hidden"), "true");

  handle.cleanup();
  handle.cleanup();
  assert.equal(calls.clear, 4);
  assert.deepEqual(container.children, []);
});

test("library loading or rendering failure leaves an accessible SVG and logic fallback", async () => {
  const loadContainer = new FakeContainer();
  const loadHandle = mountSimcirCircuit(loadContainer, "half_adder", {
    loadApi: async () => { throw new Error("Local library unavailable"); }
  });
  assert.equal((await loadHandle.ready).mode, "fallback");
  let root = loadContainer.children[0];
  assert.ok(root.querySelector(".simcir-circuit-static-fallback"));
  assert.equal(root.querySelector("svg").getAttribute("role"), "img");
  assert.match(root.querySelector(".simcir-circuit-output").textContent, /SUM = 0 · CARRY = 0/u);
  findButton(root, "A").dispatch("click");
  findButton(root, "B").dispatch("click");
  assert.match(root.querySelector(".simcir-circuit-output").textContent, /SUM = 0 · CARRY = 1/u);
  loadHandle.cleanup();

  const renderContainer = new FakeContainer();
  const { api } = makeFakeSimcir({ fail: true });
  const renderHandle = mountSimcirCircuit(renderContainer, "or_gate", { simcirApi: api });
  assert.equal((await renderHandle.ready).mode, "fallback");
  root = renderContainer.children[0];
  assert.ok(root.querySelector(".simcir-circuit-static-fallback"));
  assert.match(root.querySelector(".simcir-circuit-note").textContent, /truth table and output labels/u);
  renderHandle.cleanup();
});

test("cleanup disposes the observer, clears simulator resources, and preserves siblings", async () => {
  const container = new FakeContainer();
  const sibling = container.ownerDocument.createElement("p");
  sibling.textContent = "keep";
  container.append(sibling);
  const { api, calls } = makeFakeSimcir();
  const observerCalls = { observe: 0, disconnect: 0 };
  class FakeResizeObserver {
    constructor(callback) { this.callback = callback; }
    observe() { observerCalls.observe += 1; }
    disconnect() { observerCalls.disconnect += 1; }
  }
  const handle = mountSimcirCircuit(container, "or_gate", { simcirApi: api, ResizeObserverClass: FakeResizeObserver });
  await handle.ready;
  assert.equal(observerCalls.observe, 1);
  handle.resize();
  handle.cleanup();
  assert.equal(observerCalls.disconnect, 1);
  assert.ok(calls.clear >= 2);
  assert.deepEqual(container.children, [sibling]);
});
