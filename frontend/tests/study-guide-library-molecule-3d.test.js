import test from "node:test";
import assert from "node:assert/strict";
import {
  MOLECULE_3D_CATALOG,
  MOLECULE_3D_IDS,
  getMolecule3DDefinition,
  getMoleculeXYZ,
  mountMolecule3D
} from "../src/study-guide/libraries/molecule-3d.js";

const EXPECTED_IDS = ["water", "carbon_dioxide", "methane", "ammonia"];

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.attributes = {};
    this.children = [];
    this.style = {};
    this.hidden = false;
    this.textContent = "";
    this.parent = null;
    this.removed = false;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parent?.children.splice(node.parent.children.indexOf(node), 1);
      node.parent = this;
      this.children.push(node);
    }
  }

  remove() {
    if (this.parent) {
      const index = this.parent.children.indexOf(this);
      if (index >= 0) this.parent.children.splice(index, 1);
    }
    this.parent = null;
    this.removed = true;
  }
}

class FakeContainer extends FakeNode {
  constructor() {
    super("container");
    this.ownerDocument = {
      createElement: (name) => new FakeNode(name),
      createElementNS: (_namespace, name) => new FakeNode(name),
      defaultView: null
    };
  }
}

function makeFake3Dmol({ fail = false, resizeObserver = null } = {}) {
  const calls = { createViewer: [], addModel: [], setStyle: [], zoomTo: 0, render: 0, resize: 0, clear: 0, removeAllModels: 0 };
  const library = {
    createViewer(element, options) {
      calls.createViewer.push({ element, options });
      if (fail) throw new Error("WebGL unavailable");
      return {
        addModel(...args) { calls.addModel.push(args); },
        setStyle(...args) { calls.setStyle.push(args); },
        zoomTo() { calls.zoomTo += 1; },
        render() { calls.render += 1; },
        resize() { calls.resize += 1; },
        clear() { calls.clear += 1; },
        removeAllModels() { calls.removeAllModels += 1; }
      };
    }
  };
  const container = new FakeContainer();
  if (resizeObserver) container.ownerDocument.defaultView.ResizeObserver = resizeObserver;
  return { library, calls, container };
}

function findNode(node, tagName) {
  if (node.tagName === tagName) return node;
  for (const child of node.children) {
    const found = findNode(child, tagName);
    if (found) return found;
  }
  return null;
}

test("3D molecule catalog is a fixed deeply frozen allowlist with app-owned XYZ coordinates", () => {
  assert.deepEqual(MOLECULE_3D_IDS, EXPECTED_IDS);
  assert.equal(Object.isFrozen(MOLECULE_3D_CATALOG), true);

  for (const id of EXPECTED_IDS) {
    const molecule = getMolecule3DDefinition(id);
    assert.equal(Object.isFrozen(molecule), true);
    assert.equal(Object.isFrozen(molecule.atoms), true);
    assert.ok(molecule.atoms.every((atom) => Object.isFrozen(atom) && Object.isFrozen(atom.xyz)));
    assert.ok(molecule.bonds.every(([from, to, order]) => from >= 0 && to < molecule.atoms.length && order >= 1 && order <= 3));

    const xyz = getMoleculeXYZ(id).split("\n");
    assert.equal(Number(xyz[0]), molecule.atoms.length);
    assert.match(xyz[1], /idealized educational geometry/);
    assert.equal(xyz.length, molecule.atoms.length + 2);
    for (const [index, atom] of molecule.atoms.entries()) {
      assert.match(xyz[index + 2], new RegExp(`^${atom.element} -?\\d+\\.\\d{4} -?\\d+\\.\\d{4} -?\\d+\\.\\d{4}$`));
    }
  }
});

test("only catalog IDs resolve; arbitrary SMILES, coordinates, URLs, and prototype keys are rejected", () => {
  for (const input of ["CCO", "O=C=O", "__proto__", "constructor", "https://example.test/mol.pdb", { molecule_id: "water", xyz: "arbitrary" }, null, 0]) {
    assert.equal(getMolecule3DDefinition(input), null);
    assert.equal(getMoleculeXYZ(input), null);
  }
});

test("3Dmol receives only the fixed catalog XYZ model and provides accessible labels and resize", async () => {
  const { library, calls, container } = makeFake3Dmol();
  const existing = new FakeNode("existing-content");
  container.append(existing);
  const handle = mountMolecule3D(container, "water", library);

  assert.equal(await handle.ready, true);
  assert.equal(handle.mode, "3d");
  assert.equal(calls.createViewer.length, 1);
  assert.deepEqual(calls.addModel, [[getMoleculeXYZ("water"), "xyz"]]);
  assert.deepEqual(calls.setStyle, [[{}, { stick: { radius: 0.16 }, sphere: { scale: 0.24 } }]]);
  assert.equal(calls.zoomTo, 1);
  assert.equal(calls.render, 1);

  const viewerElement = calls.createViewer[0].element;
  assert.equal(viewerElement.attributes.role, "img");
  assert.match(viewerElement.attributes["aria-label"], /Water \(H₂O\).*rotatable 3D molecular view/);
  assert.equal(findNode(container, "svg").attributes.role, "img");
  assert.equal(container.children.find((child) => child.className === "molecule-3d-static-fallback").hidden, true);

  handle.resize();
  assert.equal(calls.resize, 1);
  assert.equal(calls.render, 2);
  handle.dispose();
  handle.dispose();
  assert.equal(calls.clear, 1);
  assert.deepEqual(container.children, [existing]);
});

test("WebGL failure leaves the accessible static 2D and text fallback visible", async () => {
  const { library, calls, container } = makeFake3Dmol({ fail: true });
  const handle = mountMolecule3D(container, "carbon_dioxide", library);

  assert.equal(await handle.ready, true);
  assert.equal(handle.mode, "fallback");
  assert.equal(calls.createViewer.length, 1);
  assert.equal(calls.addModel.length, 0);
  const fallback = container.children.find((child) => child.className === "molecule-3d-static-fallback");
  assert.ok(fallback);
  assert.equal(findNode(fallback, "svg").attributes.role, "img");
  assert.match(findNode(fallback, "desc").textContent, /linear molecular geometry/);
  assert.match(findNode(fallback, "p").textContent, /Carbon dioxide \(CO₂\).*double bond/);
  assert.equal(container.children.some((child) => child.className === "molecule-3d-webgl-viewer"), false);

  assert.doesNotThrow(() => handle.resize());
  handle.dispose();
  assert.deepEqual(container.children, []);
});

test("unsupported IDs render a safe status and never invoke the injected viewer", async () => {
  const { library, calls, container } = makeFake3Dmol();
  const handle = mountMolecule3D(container, "<script>alert(1)</script>", library);

  assert.equal(await handle.ready, true);
  assert.equal(handle.mode, "unavailable");
  assert.equal(calls.createViewer.length, 0);
  const message = container.children.find((child) => child.tagName === "p");
  assert.equal(message.attributes.role, "status");
  assert.equal(message.textContent, "This molecular structure is not in the reviewed 3D molecule catalog.");
  handle.dispose();
  assert.deepEqual(container.children, []);
});

test("updates can switch catalog entries but reject an unknown replacement and clean the viewer", async () => {
  const { library, calls, container } = makeFake3Dmol();
  const handle = mountMolecule3D(container, "water", library);
  await handle.ready;

  handle.update("methane");
  assert.equal(handle.mode, "3d");
  assert.deepEqual(calls.addModel.map(([xyz]) => xyz), [getMoleculeXYZ("water"), getMoleculeXYZ("methane")]);
  assert.equal(calls.removeAllModels, 1);

  handle.update("arbitrary-model-data");
  assert.equal(handle.mode, "unavailable");
  assert.equal(calls.clear, 1);
  assert.equal(calls.createViewer.length, 1);
  assert.equal(container.children.some((child) => child.className === "molecule-3d-webgl-viewer"), false);
  handle.dispose();
});
