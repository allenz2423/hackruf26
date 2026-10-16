const MOLECULES = Object.freeze({
  water: Object.freeze({
    smiles: "[H]O[H]",
    label: "Water (H₂O) molecular structure"
  }),
  carbon_dioxide: Object.freeze({
    smiles: "O=C=O",
    label: "Carbon dioxide (CO₂) molecular structure"
  }),
  methane: Object.freeze({
    smiles: "[H]C([H])([H])[H]",
    label: "Methane (CH₄) molecular structure"
  }),
  ammonia: Object.freeze({
    smiles: "N([H])([H])[H]",
    label: "Ammonia (NH₃) molecular structure"
  })
});
const activeMounts = new WeakMap();

/** Reviewed structures supported by the molecule activity. */
export const MOLECULE_SMILES = Object.freeze(Object.fromEntries(
  Object.entries(MOLECULES).map(([id, molecule]) => [id, molecule.smiles])
));

export const MOLECULE_IDS = Object.freeze(Object.keys(MOLECULES));

/** Return a reviewed SMILES string, or null for every unsupported identifier. */
export function getMoleculeSmiles(moleculeId) {
  if (typeof moleculeId !== "string" || !Object.hasOwn(MOLECULES, moleculeId)) return null;
  return MOLECULES[moleculeId].smiles;
}

function showFallback(container, message = "Molecular structure could not be rendered. The text description is still available.") {
  const paragraph = container.ownerDocument.createElement("p");
  paragraph.className = "molecule-library-fallback";
  paragraph.setAttribute("role", "status");
  paragraph.setAttribute("aria-live", "polite");
  paragraph.textContent = message;
  container.replaceChildren(paragraph);
}

function parseSmiles(SmilesDrawer, smiles) {
  return new Promise((resolve, reject) => {
    SmilesDrawer.parse(smiles, resolve, reject);
  });
}

/**
 * Draw a fixed reviewed structure with SmilesDrawer, loaded only when needed.
 * Returns a synchronous cleanup function; its `ready` promise settles after the
 * lazy import and draw attempt, which callers may await for status or tests.
 */
export function mountMolecule(container, moleculeId) {
  if (!container || !container.ownerDocument || typeof container.replaceChildren !== "function") {
    const cleanup = () => {};
    cleanup.ready = Promise.resolve(false);
    return cleanup;
  }

  const molecule = typeof moleculeId === "string" && Object.hasOwn(MOLECULES, moleculeId)
    ? MOLECULES[moleculeId]
    : null;
  activeMounts.get(container)?.();
  let active = true;

  const cleanup = () => {
    if (!active) return;
    active = false;
    if (activeMounts.get(container) === cleanup) {
      activeMounts.delete(container);
      container.replaceChildren();
    }
  };
  activeMounts.set(container, cleanup);

  if (!molecule) {
    showFallback(container, "This molecular structure is not available in the reviewed molecule catalog.");
    cleanup.ready = Promise.resolve(false);
    return cleanup;
  }

  cleanup.ready = (async () => {
    try {
      const imported = await import("smiles-drawer");
      if (!active) return false;

      const SmilesDrawer = imported.default;
      const tree = await parseSmiles(SmilesDrawer, molecule.smiles);
      if (!active) return false;

      const svg = container.ownerDocument.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "molecule-library-structure");
      svg.setAttribute("viewBox", "0 0 360 240");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", molecule.label);

      const drawer = new SmilesDrawer.SvgDrawer({
        width: 360,
        height: 240,
        padding: 24,
        bondLength: 48,
        compactDrawing: true
      });
      drawer.draw(tree, svg, "light");
      if (!active) return false;
      container.replaceChildren(svg);
      return true;
    } catch {
      if (active) showFallback(container);
      return false;
    }
  })();

  return cleanup;
}
