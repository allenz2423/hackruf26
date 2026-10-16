import test from "node:test";
import assert from "node:assert/strict";
import {
  computeStressCubeState,
  DEFAULT_STRESS_TENSOR_MPA,
  intersectCubeWithPlane,
  mountStressCube3D,
  normalizeStressTensor,
  symmetricEigenDecomposition
} from "../src/study-guide/libraries/stress-cube-3d.js";

const dot = (a, b) => a.reduce((sum, value, index) => sum + value * b[index], 0);
const norm = (a) => Math.sqrt(dot(a, a));
const multiply = (matrix, vector) => matrix.map((row) => dot(row, vector));

test("normalizes the input to a finite, symmetric, bounded 3x3 tensor", () => {
  assert.deepEqual(normalizeStressTensor([[1, 8, Infinity], [2, 3, 5], [7, 9, 1001]]), [
    [1, 5, 3.5], [5, 3, 7], [3.5, 7, 1000]
  ]);
  assert.deepEqual(normalizeStressTensor([["bad"]]), DEFAULT_STRESS_TENSOR_MPA);
});

test("Jacobi principal stresses and directions reconstruct a symmetric stress tensor", () => {
  const tensor = DEFAULT_STRESS_TENSOR_MPA;
  const { values, directions } = symmetricEigenDecomposition(tensor);
  assert.ok(values[0] >= values[1] && values[1] >= values[2]);
  directions.forEach((direction, index) => {
    const actual = multiply(tensor, direction);
    actual.forEach((component, axis) => assert.ok(Math.abs(component - values[index] * direction[axis]) < 1e-8));
    assert.ok(Math.abs(norm(direction) - 1) < 1e-12);
  });
  assert.ok(Math.abs(dot(directions[0], directions[1])) < 1e-12);
  assert.ok(Math.abs(dot(directions[1], directions[2])) < 1e-12);
  assert.ok(Math.abs(dot(directions[0], directions[2])) < 1e-12);
});

test("absolute maximum-shear plane uses the principal e1/e3 bisector and correct traction", () => {
  const state = computeStressCubeState(DEFAULT_STRESS_TENSOR_MPA);
  const [sigma1, , sigma3] = state.principalStresses;
  assert.ok(Math.abs(state.maxShearMpa - (sigma1 - sigma3) / 2) < 1e-10);
  assert.ok(Math.abs(dot(state.planeNormal, state.e1) - Math.SQRT1_2) < 1e-10);
  assert.ok(Math.abs(dot(state.planeNormal, state.e3) - Math.SQRT1_2) < 1e-10);
  assert.ok(Math.abs(dot(state.planeNormal, state.e2)) < 1e-10);

  const traction = multiply(state.tensor, state.planeNormal);
  const normalComponent = dot(state.planeNormal, traction);
  const shear = traction.map((value, index) => value - normalComponent * state.planeNormal[index]);
  assert.ok(Math.abs(normalComponent - (sigma1 + sigma3) / 2) < 1e-10);
  assert.ok(Math.abs(norm(shear) - state.maxShearMpa) < 1e-10);
  shear.forEach((value, axis) => assert.ok(Math.abs(value - state.shearTraction[axis]) < 1e-10));
  assert.ok(Math.abs(dot(state.planeNormal, state.shearTraction)) < 1e-10);
});

test("the supplied teaching tensor has the expected principal stresses and plane normal", () => {
  const state = computeStressCubeState([[30, -40, 20], [-40, -10, 80], [20, 80, 100]]);
  const expectedStresses = [142.0907811202, 49.6431940972, -71.7339752174];
  const expectedNormal = [0.2781149016, 0.9039641976, 0.3248089144];
  state.principalStresses.forEach((value, index) => assert.ok(Math.abs(value - expectedStresses[index]) < 1e-8));
  state.planeNormal.forEach((value, index) => assert.ok(Math.abs(value - expectedNormal[index]) < 1e-8));
  assert.ok(Math.abs(state.maxShearMpa - 106.9123781688) < 1e-8);
});

test("cube cut is a sorted polygon lying on the plane and within the cube", () => {
  for (const normal of [[1, 0, 0], [1, 1, 1], [0.278, 0.904, 0.325]]) {
    const n = normal.map((value) => value / norm(normal));
    const vertices = intersectCubeWithPlane(n);
    assert.ok(vertices.length >= 4 && vertices.length <= 6);
    for (const vertex of vertices) {
      assert.ok(Math.abs(dot(n, vertex)) < 1e-8);
      assert.ok(vertex.every((component) => component >= -1 - 1e-9 && component <= 1 + 1e-9));
    }
  }
});

test("isotropic stress reports that the max-shear plane orientation is not unique", () => {
  const state = computeStressCubeState([[5, 0, 0], [0, 5, 0], [0, 0, 5]]);
  assert.equal(state.maxShearMpa, 0);
  assert.equal(state.planeOrientationUnique, false);
});

test("repeated maximum or minimum principal stresses report non-unique max-shear orientation", () => {
  for (const tensor of [
    [[5, 0, 0], [0, 5, 0], [0, 0, -1]],
    [[5, 0, 0], [0, -2, 0], [0, 0, -2]]
  ]) {
    assert.equal(computeStressCubeState(tensor).planeOrientationUnique, false);
  }
  assert.equal(computeStressCubeState([[5, 0, 0], [0, 1, 0], [0, 0, -2]]).planeOrientationUnique, true);
});

class FakeElement {
  constructor() {
    this.listeners = new Map();
    this.children = [];
    this.attributes = {};
    this.clientWidth = 500;
    this.clientHeight = 360;
    this.removed = false;
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  removeEventListener(name) { this.listeners.delete(name); }
  fire(name, event = {}) { this.listeners.get(name)?.(event); }
  setAttribute(name, value) { this.attributes[name] = value; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  remove() { this.removed = true; }
  getBoundingClientRect() { return { width: 500, height: 360 }; }
}

class FakeGeometry {
  constructor() { this.disposeCount = 0; }
  setAttribute() { return this; }
  setFromPoints() { return this; }
  computeVertexNormals() {}
  dispose() { this.disposeCount += 1; }
}

function makeFakeThree() {
  const geometries = [], materials = [], renderers = [];
  class Geometry extends FakeGeometry { constructor(...args) { super(); this.args = args; geometries.push(this); } }
  class Material { constructor(options) { this.options = options; this.disposeCount = 0; materials.push(this); } dispose() { this.disposeCount += 1; } }
  class Vector3 { constructor(...coords) { this.coords = coords; } }
  class Node {
    constructor() {
      this.children = [];
      this.rotation = { x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } };
      this.position = { set() {} };
    }
    add(child) { this.children.push(child); }
    clear() { this.children = []; }
  }
  class Renderer {
    constructor() { this.domElement = new FakeElement(); this.renderCount = 0; this.disposeCount = 0; renderers.push(this); }
    setPixelRatio() {}
    setClearColor() {}
    setSize() {}
    render() { this.renderCount += 1; }
    dispose() { this.disposeCount += 1; }
  }
  class ArrowHelper extends Node {
    constructor() {
      super();
      this.line = { geometry: new Geometry(), material: new Material({}) };
      this.cone = { geometry: new Geometry(), material: new Material({}) };
    }
  }
  const THREE = {
    WebGLRenderer: Renderer, Vector3, Scene: Node, Group: Node,
    PerspectiveCamera: class extends Node { lookAt() {} updateProjectionMatrix() {} },
    AmbientLight: class {}, BoxGeometry: Geometry, EdgesGeometry: Geometry,
    BufferGeometry: Geometry, Float32BufferAttribute: class {},
    MeshBasicMaterial: Material, LineBasicMaterial: Material,
    Mesh: Node, Line: Node, LineSegments: Node, ArrowHelper,
    DoubleSide: 2
  };
  return { THREE, geometries, materials, renderers };
}

function fakeActivityDocument() {
  const nodes = new Map();
  for (const key of ["[data-stress-viewer]", "[data-stress-readout]", "[data-stress-status]", "[data-stress-toggle-plane]", "[data-stress-face-tractions]", "[data-stress-reset-view]"]) nodes.set(key, new FakeElement());
  nodes.get("[data-stress-face-tractions]").checked = false;
  nodes.get("[data-stress-toggle-plane]").setAttribute = function (name, value) { this.attributes[name] = value; };
  const wrapper = new FakeElement();
  wrapper.ownerDocument = { createElement: () => new FakeElement() };
  wrapper.querySelector = (selector) => nodes.get(selector) || null;
  const document = { createElement: () => wrapper };
  const container = new FakeElement();
  container.ownerDocument = document;
  return { container, wrapper, nodes };
}

test("Three.js activity redraws safely and disposes renderer, listeners, and scene resources idempotently", async () => {
  const { THREE, geometries, materials, renderers } = makeFakeThree();
  const { container, wrapper, nodes } = fakeActivityDocument();
  const handle = mountStressCube3D(container, { stress_mpa: DEFAULT_STRESS_TENSOR_MPA }, THREE);
  assert.deepEqual(await handle.ready, { ok: true, renderer: "three" });
  const renderer = renderers[0];
  assert.ok(renderer.renderCount >= 1, nodes.get("[data-stress-status]").textContent);
  const renderBeforeUpdate = renderer.renderCount;
  handle.update({ stress_mpa: [[4, 0, 0], [0, 2, 0], [0, 0, -3]] });
  assert.ok(renderer.renderCount > renderBeforeUpdate);
  nodes.get("[data-stress-toggle-plane]").fire("click");
  assert.equal(nodes.get("[data-stress-toggle-plane]").textContent, "Show shear plane");
  assert.ok(renderer.renderCount > renderBeforeUpdate + 1);
  assert.ok(geometries.length > 0 && materials.length > 0);

  handle.dispose();
  handle.dispose();
  assert.equal(renderer.disposeCount, 1);
  assert.ok(geometries.some((geometry) => geometry.disposeCount > 0));
  assert.ok(materials.some((material) => material.disposeCount > 0));
  assert.equal(renderer.domElement.removed, true);
  assert.equal(renderer.domElement.listeners.size, 0);
  assert.equal(wrapper.removed, true);
  const renderAfterDispose = renderer.renderCount;
  nodes.get("[data-stress-toggle-plane]").fire("click");
  assert.equal(renderer.renderCount, renderAfterDispose);
});

test("missing Three.js falls back to a static SVG and reports renderer choice", async () => {
  const { container, nodes } = fakeActivityDocument();
  const handle = mountStressCube3D(container, { stress_mpa: DEFAULT_STRESS_TENSOR_MPA });
  assert.deepEqual(await handle.ready, { ok: true, renderer: "svg" });
  assert.match(nodes.get("[data-stress-viewer]").innerHTML, /stress-cube-fallback/);
  assert.match(nodes.get("[data-stress-status]").textContent, /2D axonometric fallback/);
  assert.equal(nodes.get("[data-stress-reset-view]").disabled, true);
  handle.dispose();
});
