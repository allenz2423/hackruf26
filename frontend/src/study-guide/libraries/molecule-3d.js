const SVG_NS = "http://www.w3.org/2000/svg";
const VIEWER_HEIGHT = 280;
const ELEMENT_COLORS = Object.freeze({ H: "#f8fafc", C: "#64748b", N: "#2563eb", O: "#dc2626" });

function freezeMolecule({ name, formula, geometry, description, atoms, bonds }) {
  return Object.freeze({
    name,
    formula,
    geometry,
    description,
    atoms: Object.freeze(atoms.map((atom) => Object.freeze({
      element: atom.element,
      xyz: Object.freeze([...atom.xyz]),
      xy: Object.freeze([...atom.xy])
    }))),
    bonds: Object.freeze(bonds.map((bond) => Object.freeze([...bond])))
  });
}

/**
 * Small, app-authored educational geometries. Coordinates are idealized
 * reference models stored here; the viewer never fetches or parses model data.
 */
export const MOLECULE_3D_CATALOG = Object.freeze({
  water: freezeMolecule({
    name: "Water",
    formula: "H₂O",
    geometry: "bent molecular geometry",
    description: "One oxygen atom is bonded to two hydrogen atoms in a bent arrangement.",
    atoms: [
      { element: "O", xyz: [0, 0, 0], xy: [180, 105] },
      { element: "H", xyz: [-0.7586, 0, 0.588], xy: [95, 54] },
      { element: "H", xyz: [0.7586, 0, 0.588], xy: [265, 54] }
    ],
    bonds: [[0, 1, 1], [0, 2, 1]]
  }),
  carbon_dioxide: freezeMolecule({
    name: "Carbon dioxide",
    formula: "CO₂",
    geometry: "linear molecular geometry",
    description: "A central carbon atom forms a double bond with an oxygen atom at each end.",
    atoms: [
      { element: "O", xyz: [-1.16, 0, 0], xy: [70, 105] },
      { element: "C", xyz: [0, 0, 0], xy: [180, 105] },
      { element: "O", xyz: [1.16, 0, 0], xy: [290, 105] }
    ],
    bonds: [[0, 1, 2], [1, 2, 2]]
  }),
  methane: freezeMolecule({
    name: "Methane",
    formula: "CH₄",
    geometry: "tetrahedral molecular geometry",
    description: "A central carbon atom is bonded to four hydrogen atoms in a tetrahedral arrangement.",
    atoms: [
      { element: "C", xyz: [0, 0, 0], xy: [180, 105] },
      { element: "H", xyz: [0.629, 0.629, 0.629], xy: [95, 55] },
      { element: "H", xyz: [0.629, -0.629, -0.629], xy: [265, 55] },
      { element: "H", xyz: [-0.629, 0.629, -0.629], xy: [95, 155] },
      { element: "H", xyz: [-0.629, -0.629, 0.629], xy: [265, 155] }
    ],
    bonds: [[0, 1, 1], [0, 2, 1], [0, 3, 1], [0, 4, 1]]
  }),
  ammonia: freezeMolecule({
    name: "Ammonia",
    formula: "NH₃",
    geometry: "trigonal pyramidal molecular geometry",
    description: "A nitrogen atom is bonded to three hydrogen atoms in a trigonal pyramidal arrangement.",
    atoms: [
      { element: "N", xyz: [0, 0, 0], xy: [180, 115] },
      { element: "H", xyz: [0.94, 0, -0.35], xy: [80, 50] },
      { element: "H", xyz: [-0.47, 0.814, -0.35], xy: [280, 50] },
      { element: "H", xyz: [-0.47, -0.814, -0.35], xy: [180, 180] }
    ],
    bonds: [[0, 1, 1], [0, 2, 1], [0, 3, 1]]
  })
});

export const MOLECULE_3D_IDS = Object.freeze(Object.keys(MOLECULE_3D_CATALOG));
const activeMounts = new WeakMap();
let nextFallbackId = 0;

export function getMolecule3DDefinition(moleculeId) {
  if (typeof moleculeId !== "string" || !Object.hasOwn(MOLECULE_3D_CATALOG, moleculeId)) return null;
  return MOLECULE_3D_CATALOG[moleculeId];
}

/** Serialize only app-owned coordinates into the simple XYZ format. */
export function getMoleculeXYZ(moleculeId) {
  const molecule = getMolecule3DDefinition(moleculeId);
  if (!molecule) return null;
  const comment = `${molecule.name} (${molecule.formula}); idealized educational geometry`;
  const atoms = molecule.atoms.map(({ element, xyz }) => `${element} ${xyz.map((value) => value.toFixed(4)).join(" ")}`);
  return [String(molecule.atoms.length), comment, ...atoms].join("\n");
}

function makeSvgElement(document, name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function renderBond(document, svg, from, to, order, serial) {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const length = Math.hypot(dx, dy) || 1;
  const offsetX = -dy / length * 4;
  const offsetY = dx / length * 4;
  const count = Math.max(1, Math.min(3, order));
  for (let index = 0; index < count; index += 1) {
    const offset = (index - (count - 1) / 2) * 5;
    svg.append(makeSvgElement(document, "line", {
      x1: from[0] + offsetX * offset / 4,
      y1: from[1] + offsetY * offset / 4,
      x2: to[0] + offsetX * offset / 4,
      y2: to[1] + offsetY * offset / 4,
      stroke: "#475569",
      "stroke-width": 3,
      "stroke-linecap": "round",
      "data-bond": serial
    }));
  }
}

function createFallback(document, molecule, message = "") {
  const wrapper = document.createElement("div");
  wrapper.className = "molecule-3d-static-fallback";
  const id = `molecule-3d-fallback-${++nextFallbackId}`;
  const svg = makeSvgElement(document, "svg", {
    viewBox: "0 0 360 210",
    role: "img",
    "aria-labelledby": `${id}-title ${id}-description`
  });
  const title = makeSvgElement(document, "title", { id: `${id}-title` });
  title.textContent = `${molecule.name} (${molecule.formula}), 2D fallback depiction`;
  const description = makeSvgElement(document, "desc", { id: `${id}-description` });
  description.textContent = `${molecule.geometry}. ${molecule.description}`;
  svg.append(title, description);

  molecule.bonds.forEach(([fromIndex, toIndex, order], index) => {
    const from = molecule.atoms[fromIndex];
    const to = molecule.atoms[toIndex];
    if (from && to) renderBond(document, svg, from.xy, to.xy, order, index);
  });
  molecule.atoms.forEach((atom, index) => {
    const group = makeSvgElement(document, "g", {
      transform: `translate(${atom.xy[0]} ${atom.xy[1]})`,
      "data-atom-index": index
    });
    group.append(makeSvgElement(document, "circle", {
      r: 19,
      fill: ELEMENT_COLORS[atom.element] || "#94a3b8",
      stroke: "#334155",
      "stroke-width": 2
    }));
    const label = makeSvgElement(document, "text", {
      "text-anchor": "middle",
      "dominant-baseline": "central",
      fill: "#0f172a",
      "font-size": 15,
      "font-weight": 700
    });
    label.textContent = atom.element;
    group.append(label);
    svg.append(group);
  });
  wrapper.append(svg);

  const note = document.createElement("p");
  note.className = "molecule-3d-static-description";
  note.setAttribute("role", message ? "status" : "note");
  note.textContent = message || `${molecule.name} (${molecule.formula}): ${molecule.geometry}. ${molecule.description}`;
  wrapper.append(note);
  return wrapper;
}

function createUnavailableFallback(document, moleculeId) {
  const fallback = document.createElement("p");
  fallback.className = "molecule-3d-static-description";
  fallback.setAttribute("role", "status");
  fallback.setAttribute("aria-live", "polite");
  fallback.textContent = "This molecular structure is not in the reviewed 3D molecule catalog.";
  return fallback;
}

function addCatalogModel(viewer, moleculeId, replaceExisting = false) {
  const molecule = getMolecule3DDefinition(moleculeId);
  if (!molecule) return false;
  const xyz = getMoleculeXYZ(moleculeId);
  if (!xyz) return false;
  if (replaceExisting) viewer.removeAllModels?.();
  viewer.addModel(xyz, "xyz");
  viewer.setStyle({}, { stick: { radius: 0.16 }, sphere: { scale: 0.24 } });
  viewer.zoomTo();
  viewer.render();
  return true;
}

/**
 * Mount a fixed catalog structure using an injected 3Dmol.js `$3Dmol` API.
 * The model argument is only a catalog ID; SMILES, PDB, XYZ, URLs, and arbitrary
 * atom/bond arrays supplied by a guide are deliberately not accepted.
 */
export function mountMolecule3D(container, moleculeId, $3Dmol) {
  if (!container?.ownerDocument || typeof container.append !== "function") {
    return { ready: Promise.resolve(false), mode: "unavailable", update() {}, resize() {}, dispose() {} };
  }

  activeMounts.get(container)?.dispose();
  let active = true;
  let currentId = moleculeId;
  let currentMolecule = getMolecule3DDefinition(currentId);
  let mode = currentMolecule ? "fallback" : "unavailable";
  let fallback = null;
  let unavailableFallback = null;
  let viewport = null;
  let viewer = null;
  let observer = null;

  function updateFallback(id) {
    currentId = id;
    currentMolecule = getMolecule3DDefinition(currentId);
    if (fallback) fallback.remove();
    fallback = null;
    if (unavailableFallback) unavailableFallback.remove();
    unavailableFallback = null;
    if (currentMolecule) {
      fallback = createFallback(container.ownerDocument, currentMolecule);
      fallback.hidden = mode === "3d";
      container.append(fallback);
    } else {
      mode = "unavailable";
      unavailableFallback = createUnavailableFallback(container.ownerDocument, currentId);
      container.append(unavailableFallback);
    }
  }

  function resize() {
    if (!active || !viewer) return;
    try {
      viewer.resize?.();
      viewer.render?.();
    } catch {
      // A stale or detached WebGL context should not remove the static fallback.
    }
  }

  function cleanupViewer() {
    observer?.disconnect();
    observer = null;
    try {
      viewer?.clear?.();
    } catch {
      // Removing the owned viewport below still releases its canvas from the DOM.
    }
    viewer = null;
    if (viewport) viewport.remove();
    viewport = null;
  }

  function tryCreateViewer() {
    if (!currentMolecule || typeof $3Dmol?.createViewer !== "function") return false;
    if (!viewport) {
      viewport = container.ownerDocument.createElement("div");
      viewport.className = "molecule-3d-webgl-viewer";
      viewport.hidden = false;
      viewport.setAttribute("role", "img");
      viewport.setAttribute("aria-label", `${currentMolecule.name} (${currentMolecule.formula}), rotatable 3D molecular view. ${currentMolecule.geometry}.`);
      viewport.style.width = "100%";
      viewport.style.height = `${VIEWER_HEIGHT}px`;
      viewport.style.minHeight = `${VIEWER_HEIGHT}px`;
      viewport.style.boxSizing = "border-box";
      container.append(viewport);
    }
    try {
      viewer = $3Dmol.createViewer(viewport, { backgroundColor: "white", antialias: true });
      if (!viewer || typeof viewer.addModel !== "function" || typeof viewer.setStyle !== "function" || typeof viewer.zoomTo !== "function" || typeof viewer.render !== "function") {
        cleanupViewer();
        mode = "fallback";
        return false;
      }
      if (!addCatalogModel(viewer, currentId)) {
        cleanupViewer();
        mode = "fallback";
        return false;
      }
      mode = "3d";
      if (fallback) fallback.hidden = true;
      const ResizeObserverImpl = container.ownerDocument.defaultView?.ResizeObserver;
      if (ResizeObserverImpl) {
        observer = new ResizeObserverImpl(resize);
        observer.observe(container);
      }
      return true;
    } catch {
      cleanupViewer();
      mode = "fallback";
      if (fallback) fallback.hidden = false;
      return false;
    }
  }

  const dispose = () => {
    if (!active) return;
    active = false;
    cleanupViewer();
    fallback?.remove();
    fallback = null;
    unavailableFallback?.remove();
    unavailableFallback = null;
    if (activeMounts.get(container) === handle) activeMounts.delete(container);
  };

  const handle = {
    get mode() { return mode; },
    ready: null,
    update(nextMoleculeId) {
      if (!active) return;
      updateFallback(nextMoleculeId);
      if (!currentMolecule) {
        cleanupViewer();
        return;
      }
      if (currentMolecule && viewer) {
        try {
          if (addCatalogModel(viewer, currentId, true)) {
            mode = "3d";
            if (fallback) fallback.hidden = true;
            resize();
            return;
          }
        } catch {
          cleanupViewer();
        }
      }
      if (currentMolecule) {
        mode = "fallback";
        if (fallback) fallback.hidden = false;
        tryCreateViewer();
      }
    },
    resize,
    dispose
  };

  updateFallback(currentId);
  const mounted3d = tryCreateViewer();
  handle.ready = Promise.resolve(Boolean(fallback || unavailableFallback));
  if (!mounted3d && currentMolecule) mode = "fallback";
  activeMounts.set(container, handle);
  return handle;
}
