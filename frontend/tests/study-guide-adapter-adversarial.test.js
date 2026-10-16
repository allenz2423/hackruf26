import test from "node:test";
import assert from "node:assert/strict";
import {
  buildGraphTraversal,
  normalizeGraphTraversalParameters,
  selectGraphNode
} from "../src/study-guide/libraries/cytoscape-traversal.js";
import {
  computeStressCubeState,
  intersectCubeWithPlane,
  symmetricEigenDecomposition
} from "../src/study-guide/libraries/stress-cube-3d.js";
import {
  getMolecule3DDefinition,
  getMoleculeXYZ,
  MOLECULE_3D_IDS
} from "../src/study-guide/libraries/molecule-3d.js";
import { mountPythonActivity } from "../src/study-guide/libraries/python-activity.js";
import { runPythonWorkerRequest } from "../src/study-guide/libraries/python-activity-worker-core.js";

const dot = (left, right) => left.reduce((sum, item, index) => sum + item * right[index], 0);
const norm = (vector) => Math.sqrt(dot(vector, vector));
const multiply = (matrix, vector) => matrix.map((row) => dot(row, vector));
const distance = (left, right) => norm(left.map((item, index) => item - right[index]));

test("graph traversals visit cyclic components once and preserve directed reachability", () => {
  const graph = {
    start_node: "c",
    nodes: ["a", "b", "c", "d", "e", "f"].map((id) => ({ id, label: id.toUpperCase() })),
    edges: [
      { source: "c", target: "d" },
      { source: "d", target: "b" },
      { source: "a", target: "b" },
      { source: "c", target: "a" },
      { source: "b", target: "d" },
      { source: "a", target: "c" },
      { source: "f", target: "f" }
    ]
  };
  const reversedEdgeOrder = { ...graph, edges: [...graph.edges].reverse() };
  const bfs = buildGraphTraversal({ ...graph, algorithm: "bfs" });
  const dfs = buildGraphTraversal({ ...graph, algorithm: "dfs" });

  assert.deepEqual(bfs, buildGraphTraversal({ ...reversedEdgeOrder, algorithm: "bfs" }));
  assert.deepEqual(dfs, buildGraphTraversal({ ...reversedEdgeOrder, algorithm: "dfs" }));
  assert.deepEqual(bfs.visits, [
    { nodeId: "c", parentId: null, depth: 0 },
    { nodeId: "a", parentId: "c", depth: 1 },
    { nodeId: "d", parentId: "c", depth: 1 },
    { nodeId: "b", parentId: "a", depth: 2 }
  ]);
  assert.deepEqual(dfs.visits, [
    { nodeId: "c", parentId: null, depth: 0 },
    { nodeId: "a", parentId: "c", depth: 1 },
    { nodeId: "b", parentId: "a", depth: 2 },
    { nodeId: "d", parentId: "b", depth: 3 }
  ]);
  assert.deepEqual(bfs.unreachable_node_ids, ["e", "f"]);
  assert.deepEqual(dfs.unreachable_node_ids, ["e", "f"]);
  assert.equal(new Set(bfs.visits.map(({ nodeId }) => nodeId)).size, bfs.visits.length);
  assert.equal(new Set(dfs.visits.map(({ nodeId }) => nodeId)).size, dfs.visits.length);
});

test("graph validation accepts reverse edges and self-cycles but returns detached normalized data", () => {
  const input = {
    start_node: "n1",
    nodes: [{ id: "n1", label: "One" }, { id: "n2", label: "Two" }],
    edges: [
      { source: "n1", target: "n2", ignored: { selector: "*" } },
      { source: "n2", target: "n1" },
      { source: "n2", target: "n2" }
    ],
    style: [{ selector: "*", style: { display: "none" } }]
  };
  const normalized = normalizeGraphTraversalParameters(input);
  input.nodes[0].label = "mutated";
  input.edges[0].source = "n2";
  input.edges[0].ignored.selector = "body";

  assert.deepEqual(normalized, {
    algorithm: "bfs",
    start_node: "n1",
    nodes: [{ id: "n1", label: "One", description: "" }, { id: "n2", label: "Two", description: "" }],
    edges: [
      { source: "n1", target: "n2" },
      { source: "n2", target: "n1" },
      { source: "n2", target: "n2" }
    ]
  });
  assert.deepEqual(buildGraphTraversal(normalized).visits.map(({ nodeId }) => nodeId), ["n1", "n2"]);
  assert.deepEqual(selectGraphNode(normalized, "n2").outgoing_neighbors.map(({ id }) => id), ["n1", "n2"]);
  assert.throws(() => normalizeGraphTraversalParameters({ ...normalized, edges: [...normalized.edges, { source: "n1", target: "n1" }, { source: "n1", target: "n1" }] }), /duplicates/);
});

test("3D stress calculation matches a rotated analytic tensor and its traction decomposition", () => {
  // This is R diag(8, 2, -4) Rᵀ for a 45° rotation about z.
  const tensor = [[5, 3, 0], [3, 5, 0], [0, 0, -4]];
  const { values, directions } = symmetricEigenDecomposition(tensor);
  assert.deepEqual(values.map((value) => Number(value.toFixed(10))), [8, 2, -4]);

  directions.forEach((direction, index) => {
    const reconstructed = multiply(tensor, direction);
    reconstructed.forEach((value, axis) => assert.ok(Math.abs(value - values[index] * direction[axis]) < 1e-10));
    assert.ok(Math.abs(norm(direction) - 1) < 1e-12);
  });
  for (let left = 0; left < 3; left += 1) {
    for (let right = left + 1; right < 3; right += 1) assert.ok(Math.abs(dot(directions[left], directions[right])) < 1e-12);
  }

  const state = computeStressCubeState(tensor);
  assert.ok(Math.abs(dot(state.planeNormal, state.e1) - Math.SQRT1_2) < 1e-12);
  assert.ok(Math.abs(dot(state.planeNormal, state.e3) - Math.SQRT1_2) < 1e-12);
  assert.ok(Math.abs(state.normalStressMpa - 2) < 1e-10);
  assert.ok(Math.abs(state.maxShearMpa - 6) < 1e-10);
  assert.ok(Math.abs(norm(state.shearTraction) - 6) < 1e-10);
  assert.ok(Math.abs(dot(state.planeNormal, state.shearTraction)) < 1e-10);

  const exactPlaneNormal = [0.5, 0.5, Math.SQRT1_2];
  const expectedTraction = multiply(tensor, exactPlaneNormal);
  const actualTraction = multiply(state.tensor, state.planeNormal);
  assert.ok(Math.abs(norm(actualTraction) - norm(expectedTraction)) < 1e-10);
});

test("plane clipping handles axis, diagonal, and cube-edge intersections without duplicate vertices", () => {
  const cases = [
    { normal: [1, 0, 0], expectedCount: 4 },
    { normal: [1, 1, 0], expectedCount: 4 },
    { normal: [1, 1, 1], expectedCount: 6 }
  ];
  for (const { normal, expectedCount } of cases) {
    const points = intersectCubeWithPlane(normal);
    const unique = new Set(points.map((point) => point.map((coordinate) => coordinate.toFixed(9)).join(",")));
    assert.equal(points.length, expectedCount);
    assert.equal(unique.size, points.length);
    for (const point of points) {
      assert.ok(Math.abs(dot(normal, point)) < 1e-9);
      assert.ok(point.every((coordinate) => coordinate >= -1 - 1e-9 && coordinate <= 1 + 1e-9));
    }
  }
  assert.deepEqual(intersectCubeWithPlane([0, 0, 0]), []);
});

test("molecule catalog coordinates encode the advertised 3D geometries", () => {
  assert.deepEqual(MOLECULE_3D_IDS, ["water", "carbon_dioxide", "methane", "ammonia"]);
  const atoms = (id) => getMolecule3DDefinition(id).atoms;

  const water = atoms("water");
  assert.ok(Math.abs(distance(water[0].xyz, water[1].xyz) - distance(water[0].xyz, water[2].xyz)) < 1e-12);
  const waterAngle = Math.acos(dot(water[1].xyz, water[2].xyz) / (norm(water[1].xyz) * norm(water[2].xyz))) * 180 / Math.PI;
  assert.ok(waterAngle > 100 && waterAngle < 110, `unexpected H-O-H angle ${waterAngle}`);

  const carbonDioxide = atoms("carbon_dioxide");
  assert.ok(Math.abs(distance(carbonDioxide[0].xyz, carbonDioxide[1].xyz) - distance(carbonDioxide[1].xyz, carbonDioxide[2].xyz)) < 1e-12);
  assert.equal(Math.abs(carbonDioxide[0].xyz[0] - carbonDioxide[1].xyz[0]), 1.16);
  assert.deepEqual(carbonDioxide.map(({ xyz }) => xyz.slice(1)), [[0, 0], [0, 0], [0, 0]]);

  const methane = atoms("methane");
  const methaneVectors = methane.slice(1).map(({ xyz }) => xyz);
  const methaneLengths = methaneVectors.map(norm);
  assert.ok(methaneLengths.every((length) => Math.abs(length - methaneLengths[0]) < 1e-12));
  const methaneCosines = methaneVectors.flatMap((left, index) => methaneVectors.slice(index + 1).map((right) => dot(left, right) / (norm(left) * norm(right))));
  assert.ok(methaneCosines.every((cosine) => Math.abs(cosine + 1 / 3) < 1e-12));

  const ammonia = atoms("ammonia");
  const ammoniaHydrogens = ammonia.slice(1).map(({ xyz }) => xyz);
  const ammoniaAngles = ammoniaHydrogens.flatMap((left, index) => ammoniaHydrogens.slice(index + 1).map((right) => Math.acos(dot(left, right) / (norm(left) * norm(right))) * 180 / Math.PI));
  assert.equal(ammoniaAngles.length, 3);
  assert.ok(ammoniaAngles.every((angle) => angle > 100 && angle < 115));

  for (const id of MOLECULE_3D_IDS) {
    const definition = getMolecule3DDefinition(id);
    const xyzLines = getMoleculeXYZ(id).split("\n");
    assert.equal(Number(xyzLines[0]), definition.atoms.length);
    assert.equal(xyzLines.length, definition.atoms.length + 2);
    assert.ok(definition.bonds.every(([left, right]) => distance(definition.atoms[left].xyz, definition.atoms[right].xyz) > 0));
  }
});

test("Python worker rejects injected source, unknown fields, and runtime path escapes before loading Pyodide", async () => {
  const base = {
    protocol_version: 1,
    type: "run",
    request_id: "python-42",
    template_id: "python_algorithm_trace_v1",
    parameters: { algorithm: "linear_search", values: [4, 9], target: 9 },
    runtime_module_url: "https://guide.test/vendor/pyodide/pyodide.mjs",
    runtime_index_url: "https://guide.test/vendor/pyodide/"
  };
  const maliciousRequests = [
    { ...base, source: "import os; os.remove('/')" },
    { ...base, code: "print('not fixed')" },
    { ...base, parameters: { ...base.parameters, source: "print(1)" } },
    { ...base, runtime_module_url: "https://attacker.test/vendor/pyodide/pyodide.mjs" },
    { ...base, runtime_module_url: "https://guide.test/vendor/pyodide/../outside.mjs" },
    { ...base, runtime_index_url: "https://guide.test/vendor/pyodide.evil/" }
  ];

  for (const request of maliciousRequests) {
    let loaded = false;
    const emitted = [];
    const result = await runPythonWorkerRequest(request, {
      workerLocation: { href: "https://guide.test/python-worker.js", origin: "https://guide.test", protocol: "https:" },
      async loadPyodide() { loaded = true; throw new Error("must not load"); },
      emit(message) { emitted.push(message); }
    });
    assert.equal(result.ok, false);
    assert.equal(loaded, false);
    assert.equal(emitted.length, 1);
    assert.equal(emitted[0].type, "error");
  }
});

class FakeElement {
  constructor(document, tagName = "div") {
    this.ownerDocument = document;
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  removeEventListener(name) { this.listeners.delete(name); }
}

class FakeWorker {
  terminated = false;
  postMessage(message) { this.message = message; }
  terminate() { this.terminated = true; }
}

function makePythonContainer() {
  const document = {
    defaultView: { location: { href: "https://guide.test/study" } },
    createElement(tagName) { return new FakeElement(document, tagName); },
    createTextNode(text) { return { textContent: text }; }
  };
  return new FakeElement(document, "container");
}

function findByTag(node, tagName) {
  if (node?.tagName === tagName) return node;
  for (const child of node?.children || []) {
    const found = findByTag(child, tagName);
    if (found) return found;
  }
  return null;
}

test("Python activity rejects a worker result whose claimed search index contradicts its input", async () => {
  const container = makePythonContainer();
  const worker = new FakeWorker();
  const handle = mountPythonActivity(container, {
    template_id: "python_algorithm_trace_v1",
    parameters: { algorithm: "linear_search", values: [4, 9], target: 9 }
  }, { workerFactory: () => worker });
  const run = handle.run();
  worker.onmessage({ data: {
    protocol_version: 1,
    type: "result",
    request_id: worker.message.request_id,
    output_json: JSON.stringify({ steps: ["Found 9 at index 0."], found_index: 0 })
  } });

  const result = await run;
  assert.deepEqual({ ok: result.ok, reason: result.reason }, { ok: false, reason: "invalid_output" });
  assert.equal(worker.terminated, true);
  const list = findByTag(container, "ol");
  assert.deepEqual(list.children, []);
  const activitySection = container.children[0];
  const fallbackParagraph = activitySection.children.at(-1);
  assert.equal(fallbackParagraph.hidden, false);
  assert.match(fallbackParagraph.textContent, /remain available as text/);
  handle.dispose();
});
