import test from "node:test";
import assert from "node:assert/strict";
import SmilesDrawer from "smiles-drawer";
import {
  MOLECULE_IDS,
  MOLECULE_SMILES,
  getMoleculeSmiles,
  mountMolecule
} from "../src/study-guide/libraries/molecules.js";

const EXPECTED_SMILES = {
  water: "[H]O[H]",
  carbon_dioxide: "O=C=O",
  methane: "[H]C([H])([H])[H]",
  ammonia: "N([H])([H])[H]"
};

test("molecule catalog has exactly the reviewed allowlist and structures", () => {
  assert.deepEqual(MOLECULE_IDS, ["water", "carbon_dioxide", "methane", "ammonia"]);
  assert.deepEqual(MOLECULE_SMILES, EXPECTED_SMILES);
  assert.equal(Object.isFrozen(MOLECULE_SMILES), true);

  for (const [moleculeId, smiles] of Object.entries(EXPECTED_SMILES)) {
    assert.equal(getMoleculeSmiles(moleculeId), smiles);
  }
});

test("unknown IDs and model-supplied SMILES never resolve through the catalog", () => {
  for (const unknown of ["benzene", "__proto__", "toString", "C", "[H]O[H]", null, 1]) {
    assert.equal(getMoleculeSmiles(unknown), null);
  }
});

test("each reviewed SMILES parses with the installed SmilesDrawer version", async () => {
  for (const [moleculeId, smiles] of Object.entries(EXPECTED_SMILES)) {
    const tree = await new Promise((resolve, reject) => {
      SmilesDrawer.parse(smiles, resolve, reject);
    });
    assert.ok(tree, `${moleculeId} should produce a parse tree`);
    assert.ok(tree.atom, `${moleculeId} parse tree should start with an atom`);
  }
});

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.attributes = {};
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

class FakeContainer {
  constructor() {
    this.children = [];
    this.ownerDocument = {
      createElement: (tagName) => new FakeNode(tagName),
      createElementNS: (_namespace, tagName) => new FakeNode(tagName)
    };
  }

  replaceChildren(...children) {
    this.children = children;
  }
}

test("unsupported molecule mounts show a safe fallback and cleanup removes it", async () => {
  const container = new FakeContainer();
  const cleanup = mountMolecule(container, "<img src=x onerror=alert(1)>");

  assert.equal(await cleanup.ready, false);
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].tagName, "p");
  assert.equal(container.children[0].attributes.role, "status");
  assert.equal(container.children[0].textContent, "This molecular structure is not available in the reviewed molecule catalog.");

  cleanup();
  assert.deepEqual(container.children, []);
});

test("a failed render falls back to text and a stale cleanup cannot clear a newer mount", async () => {
  const container = new FakeContainer();
  const firstCleanup = mountMolecule(container, "water");
  const secondCleanup = mountMolecule(container, "carbon_dioxide");

  assert.equal(await firstCleanup.ready, false);
  assert.equal(await secondCleanup.ready, false);
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].textContent, "Molecular structure could not be rendered. The text description is still available.");

  firstCleanup();
  assert.equal(container.children.length, 1);
  secondCleanup();
  assert.deepEqual(container.children, []);
});
