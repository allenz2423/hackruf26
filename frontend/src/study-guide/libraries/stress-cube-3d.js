const DEFAULT_TENSOR = [
  [30, -40, 20],
  [-40, -10, 80],
  [20, 80, 100]
];

const EPSILON = 1e-10;
let stressCubeInstance = 0;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const subtract = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a, amount) => [a[0] * amount, a[1] * amount, a[2] * amount];
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0]
];
const magnitude = (a) => Math.sqrt(dot(a, a));
const unit = (a) => {
  const length = magnitude(a);
  return length > EPSILON ? scale(a, 1 / length) : [0, 0, 0];
};

/** Return a bounded symmetric stress tensor in MPa; invalid input uses the authored example. */
export function normalizeStressTensor(value) {
  const source = Array.isArray(value) && value.length === 3 && value.every((row) => Array.isArray(row) && row.length === 3)
    ? value
    : DEFAULT_TENSOR;
  const matrix = source.map((row) => row.map((entry) => {
    const number = Number(entry);
    return Number.isFinite(number) ? clamp(number, -1000, 1000) : 0;
  }));
  for (let row = 0; row < 3; row += 1) {
    for (let column = row + 1; column < 3; column += 1) {
      const symmetric = (matrix[row][column] + matrix[column][row]) / 2;
      matrix[row][column] = symmetric;
      matrix[column][row] = symmetric;
    }
  }
  return matrix;
}

/** Jacobi eigensolver for a real symmetric 3x3 matrix; vectors are unit rows sorted with values. */
export function symmetricEigenDecomposition(input) {
  const matrix = normalizeStressTensor(input);
  const a = matrix.map((row) => [...row]);
  const vectors = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

  for (let iteration = 0; iteration < 40; iteration += 1) {
    let p = 0, q = 1;
    if (Math.abs(a[0][2]) > Math.abs(a[p][q])) [p, q] = [0, 2];
    if (Math.abs(a[1][2]) > Math.abs(a[p][q])) [p, q] = [1, 2];
    const offDiagonal = a[p][q];
    if (Math.abs(offDiagonal) < 1e-13) break;

    const tau = (a[q][q] - a[p][p]) / (2 * offDiagonal);
    const t = (tau >= 0 ? 1 : -1) / (Math.abs(tau) + Math.sqrt(1 + tau * tau));
    const cosine = 1 / Math.sqrt(1 + t * t);
    const sine = t * cosine;
    const app = a[p][p], aqq = a[q][q];
    a[p][p] = app - t * offDiagonal;
    a[q][q] = aqq + t * offDiagonal;
    a[p][q] = 0;
    a[q][p] = 0;

    for (let k = 0; k < 3; k += 1) {
      if (k !== p && k !== q) {
        const akp = a[k][p], akq = a[k][q];
        a[k][p] = cosine * akp - sine * akq;
        a[p][k] = a[k][p];
        a[k][q] = sine * akp + cosine * akq;
        a[q][k] = a[k][q];
      }
      const vkp = vectors[k][p], vkq = vectors[k][q];
      vectors[k][p] = cosine * vkp - sine * vkq;
      vectors[k][q] = sine * vkp + cosine * vkq;
    }
  }

  const eigenpairs = [0, 1, 2].map((index) => {
    let direction = unit([vectors[0][index], vectors[1][index], vectors[2][index]]);
    const largestComponent = direction.reduce((best, current, component) =>
      Math.abs(current) > Math.abs(direction[best]) ? component : best, 0);
    if (direction[largestComponent] < 0) direction = scale(direction, -1);
    return { value: a[index][index], direction };
  }).sort((left, right) => right.value - left.value);

  return {
    values: eigenpairs.map(({ value }) => value),
    directions: eigenpairs.map(({ direction }) => direction)
  };
}

/** Intersections of n·x=0 with the twelve edges of the centered [-1,1]^3 cube. */
export function intersectCubeWithPlane(normal) {
  const n = unit(normal);
  if (magnitude(n) < EPSILON) return [];
  const vertices = [];
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) vertices.push([x, y, z]);
  const intersections = [];
  const appendUnique = (point) => {
    if (!intersections.some((existing) => magnitude(subtract(existing, point)) < 1e-8)) intersections.push(point);
  };

  for (let first = 0; first < vertices.length; first += 1) {
    for (let second = first + 1; second < vertices.length; second += 1) {
      const a = vertices[first], b = vertices[second];
      const differingAxes = [0, 1, 2].filter((axis) => a[axis] !== b[axis]);
      if (differingAxes.length !== 1) continue;
      const da = dot(n, a), db = dot(n, b);
      if (Math.abs(da) < 1e-9 && Math.abs(db) < 1e-9) {
        appendUnique(a);
        appendUnique(b);
      } else if (da * db <= 0) {
        const fraction = da / (da - db);
        if (fraction >= -EPSILON && fraction <= 1 + EPSILON) {
          appendUnique(add(a, scale(subtract(b, a), fraction)));
        }
      }
    }
  }

  if (intersections.length < 3) return intersections;
  const center = intersections.reduce((sum, point) => add(sum, point), [0, 0, 0]).map((value) => value / intersections.length);
  const reference = Math.abs(n[0]) < 0.8 ? [1, 0, 0] : [0, 1, 0];
  const u = unit(subtract(reference, scale(n, dot(reference, n))));
  const v = unit(cross(n, u));
  intersections.sort((left, right) => {
    const l = subtract(left, center), r = subtract(right, center);
    return Math.atan2(dot(l, v), dot(l, u)) - Math.atan2(dot(r, v), dot(r, u));
  });
  return intersections;
}

/** Compute principal stress and an absolute-maximum-shear plane from t = σn. */
export function computeStressCubeState(value) {
  const tensor = normalizeStressTensor(value);
  const { values, directions } = symmetricEigenDecomposition(tensor);
  const [sigma1, sigma2, sigma3] = values;
  const [e1, e2, e3] = directions;
  const planeNormal = unit(add(e1, e3));
  const shearDirection = unit(subtract(e1, e3));
  const shearMagnitude = (sigma1 - sigma3) / 2;
  const planeNormalStress = dot(planeNormal, [
    dot(tensor[0], planeNormal),
    dot(tensor[1], planeNormal),
    dot(tensor[2], planeNormal)
  ]);
  const traction = [
    dot(tensor[0], planeNormal),
    dot(tensor[1], planeNormal),
    dot(tensor[2], planeNormal)
  ];
  const shearTraction = subtract(traction, scale(planeNormal, planeNormalStress));
  const eigenvalueScale = Math.max(1, Math.abs(sigma1), Math.abs(sigma2), Math.abs(sigma3));
  const repeatedPrincipalTolerance = 1e-8 * eigenvalueScale;
  return {
    tensor,
    principalStresses: values,
    principalDirections: directions,
    e1, e2, e3,
    planeNormal,
    shearDirection,
    maxShearMpa: shearMagnitude,
    normalStressMpa: planeNormalStress,
    shearTraction,
    planeVertices: intersectCubeWithPlane(planeNormal),
    planeOrientationUnique: Math.abs(sigma1 - sigma2) > repeatedPrincipalTolerance
      && Math.abs(sigma2 - sigma3) > repeatedPrincipalTolerance
  };
}

const format = (value, digits = 2) => (Object.is(value, -0) ? 0 : value).toFixed(digits);
const vecText = (vector) => `(${vector.map((value) => format(value, 3)).join(", ")})`;
const project = ([x, y, z], centerX = 175, centerY = 180, factor = 62) => [
  centerX + factor * (y - x),
  centerY + factor * (0.5 * (x + y) - z)
];
const pointList = (points, cx, cy, factor) => points.map((point) => project(point, cx, cy, factor).map((value) => value.toFixed(1)).join(",")).join(" ");

function fallbackCubeSvg(state, instanceId, showFaceTractions = false) {
  const { planeVertices, planeNormal, shearDirection, e1, e3 } = state;
  const vertices = [];
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) vertices.push([x, y, z]);
  const byKey = new Map(vertices.map((point, index) => [point.join(","), index]));
  const edgePairs = [];
  vertices.forEach((point, index) => {
    for (let axis = 0; axis < 3; axis += 1) {
      const other = [...point]; other[axis] *= -1;
      const otherIndex = byKey.get(other.join(","));
      if (otherIndex > index) edgePairs.push([index, otherIndex]);
    }
  });
  const planeCenter = planeVertices.length ? planeVertices.reduce((sum, point) => add(sum, point), [0, 0, 0]).map((x) => x / planeVertices.length) : [0, 0, 0];
  const nStart = planeCenter;
  const nEnd = add(nStart, scale(planeNormal, 1.1));
  const shearStart = add(planeCenter, scale(state.e2, 0.45));
  const shearEnd = add(shearStart, scale(shearDirection, 1.1));
  const xy = (point) => project(point).map((value) => value.toFixed(1)).join(",");
  const tractionArrows = showFaceTractions ? [0, 1, 2].map((column) => {
    const origin = [[1, 0, 0], [0, 1, 0], [0, 0, 1]][column];
    const traction = state.tensor.map((row) => row[column]);
    const maxColumnMagnitude = Math.max(...[0, 1, 2].map((index) => magnitude(state.tensor.map((row) => row[index]))), 1);
    const end = add(origin, scale(unit(traction), 0.45 + 0.65 * magnitude(traction) / maxColumnMagnitude));
    const [x1, y1] = project(origin), [x2, y2] = project(end);
    const color = ["#f97316", "#65a30d", "#e11d48"][column];
    return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${color}" stroke-width="3" marker-end="url(#${instanceId}-face-${column})"/>`;
  }).join("") : "";
  return `<svg class="stress-cube-fallback" viewBox="0 0 350 360" role="img" aria-label="Two-dimensional axonometric fallback of a cube cut by the absolute maximum shear plane. The normal and shear direction are shown as separate arrows.">
    <defs><marker id="${instanceId}-n" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#c2410c"/></marker><marker id="${instanceId}-t" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#0369a1"/></marker>${["#f97316", "#65a30d", "#e11d48"].map((color, index) => `<marker id="${instanceId}-face-${index}" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="${color}"/></marker>`).join("")}</defs>
    <polygon points="${pointList(planeVertices, 175, 180, 62)}" fill="#a855f7" fill-opacity=".38" stroke="#7e22ce" stroke-width="2"/>
    <g fill="none" stroke="#475569" stroke-width="2">${edgePairs.map(([a, b]) => `<line x1="${xy(vertices[a]).split(",")[0]}" y1="${xy(vertices[a]).split(",")[1]}" x2="${xy(vertices[b]).split(",")[0]}" y2="${xy(vertices[b]).split(",")[1]}"/>`).join("")}</g>
    ${tractionArrows}
    <line x1="${xy(nStart).split(",")[0]}" y1="${xy(nStart).split(",")[1]}" x2="${xy(nEnd).split(",")[0]}" y2="${xy(nEnd).split(",")[1]}" stroke="#c2410c" stroke-width="4" marker-end="url(#${instanceId}-n)"/>
    <line x1="${xy(shearStart).split(",")[0]}" y1="${xy(shearStart).split(",")[1]}" x2="${xy(shearEnd).split(",")[0]}" y2="${xy(shearEnd).split(",")[1]}" stroke="#0369a1" stroke-width="4" marker-end="url(#${instanceId}-t)"/>
    <text x="${xy(nEnd).split(",")[0]}" y="${Number(xy(nEnd).split(",")[1]) - 7}" fill="#9a3412" font-size="15" font-weight="700">n</text><text x="${xy(shearEnd).split(",")[0]}" y="${Number(xy(shearEnd).split(",")[1]) - 7}" fill="#075985" font-size="15" font-weight="700">τmax</text>
    <text x="188" y="307" fill="#475569" font-size="12">2D axonometric fallback · cube edge = 2</text>
  </svg>`;
}

const ANGLE_INSET = `<svg class="stress-angle-inset" viewBox="0 0 230 185" role="img" aria-labelledby="stress-angle-title stress-angle-description">
  <title id="stress-angle-title">The maximum-shear plane normal bisects e1 and e3</title>
  <desc id="stress-angle-description">In principal-stress coordinates, e1 and e3 are perpendicular. The plane normal n is their normalized sum, exactly 45 degrees from each axis.</desc>
  <defs><marker id="stress-angle-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#c2410c"/></marker></defs>
  <text x="10" y="18" class="stress-inset-title">45° construction (principal plane)</text>
  <line x1="105" y1="135" x2="193" y2="135" stroke="#334155" stroke-width="3" marker-end="url(#stress-angle-arrow)"/>
  <line x1="105" y1="135" x2="105" y2="47" stroke="#334155" stroke-width="3" marker-end="url(#stress-angle-arrow)"/>
  <line x1="105" y1="135" x2="167" y2="73" stroke="#c2410c" stroke-width="4" marker-end="url(#stress-angle-arrow)"/>
  <path d="M133 135 A28 28 0 0 0 124.8 115.2" fill="none" stroke="#c2410c" stroke-width="2"/>
  <path d="M124.8 115.2 A28 28 0 0 0 105 107" fill="none" stroke="#c2410c" stroke-width="2"/>
  <text x="145" y="119" class="stress-angle-label">45°</text><text x="111" y="99" class="stress-angle-label">45°</text>
  <text x="195" y="140" class="stress-axis-label">e₁ (σ₁)</text><text x="80" y="44" class="stress-axis-label">e₃ (σ₃)</text>
  <text x="169" y="68" class="stress-normal-label">n</text><text x="10" y="171" class="stress-inset-note">n = (e₁ + e₃) / √2 · each angle is 45°</text>
</svg>`;

const SCOPED_CSS = `<style>
.stress-cube-activity{color:#172033;font:inherit}.stress-cube-activity *{box-sizing:border-box}.stress-cube-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}.stress-cube-head h4{margin:.15rem 0;font-size:1.05rem}.stress-cube-intro{margin:.35rem 0 .8rem;color:#475569}.stress-cube-stage{display:grid;grid-template-columns:minmax(0,1fr) 235px;gap:.75rem;align-items:stretch}.stress-cube-viewer{position:relative;min-height:340px;overflow:hidden;border:1px solid #d8dee9;border-radius:12px;background:linear-gradient(145deg,#fbfdff,#f3f6fb)}.stress-cube-viewer canvas{display:block;width:100%;height:100%;min-height:340px;touch-action:none;outline-offset:-3px}.stress-cube-fallback{display:block;width:100%;height:100%;min-height:340px}.stress-cube-legend{display:flex;flex-wrap:wrap;gap:.45rem .85rem;margin:.5rem 0;color:#334155;font-size:.86rem}.stress-cube-key{display:inline-flex;align-items:center;gap:.35rem}.stress-cube-swatch{width:.7rem;height:.7rem;border-radius:50%;display:inline-block}.stress-angle-inset{width:100%;height:auto;min-height:180px;border:1px solid #d8dee9;border-radius:12px;background:#fff}.stress-inset-title{font-size:12px;font-weight:700;fill:#172033}.stress-angle-label{font-size:12px;font-weight:700;fill:#9a3412}.stress-axis-label{font-size:11px;fill:#334155}.stress-normal-label{font-size:13px;font-weight:700;fill:#9a3412}.stress-inset-note{font-size:10px;fill:#475569}.stress-cube-controls{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap;margin:.65rem 0}.stress-cube-controls label{display:inline-flex;gap:.4rem;align-items:center}.stress-cube-data{margin-top:.65rem;padding:.65rem .8rem;border:1px solid #d8dee9;border-radius:10px;background:#fff}.stress-cube-data summary{cursor:pointer;font-weight:650}.stress-cube-matrix{border-collapse:collapse;margin:.5rem 0}.stress-cube-matrix th,.stress-cube-matrix td{padding:.22rem .55rem;text-align:right;font-variant-numeric:tabular-nums}.stress-cube-matrix th[scope=row]{text-align:left}.stress-cube-results{display:grid;grid-template-columns:repeat(auto-fit,minmax(205px,1fr));gap:.3rem .9rem;margin:.4rem 0 0}.stress-cube-results p{margin:.15rem 0;font-variant-numeric:tabular-nums}.stress-cube-status{min-height:1.2em;color:#475569}.stress-cube-text-alt{font-size:.9rem;color:#475569}.stress-cube-activity button{min-height:36px}.stress-cube-activity button:focus-visible,.stress-cube-activity input:focus-visible,.stress-cube-viewer canvas:focus-visible{outline:3px solid #0f766e;outline-offset:2px}@media(max-width:680px){.stress-cube-stage{grid-template-columns:1fr}.stress-cube-stage .stress-angle-inset{max-width:270px}.stress-cube-viewer,.stress-cube-viewer canvas,.stress-cube-fallback{min-height:300px}}
</style>`;

const LAYOUT_CSS = `<style>.stress-cube-activity .stress-cube-side{display:grid;align-content:start;gap:.6rem}.stress-cube-activity .stress-coordinate-inset{width:100%;height:auto;min-height:135px;border:1px solid #d8dee9;border-radius:12px;background:#fff}.stress-cube-activity .stress-coordinate-inset text{font-size:11px;fill:#334155}@media(max-width:680px){.stress-cube-activity .stress-cube-side{grid-template-columns:minmax(0,270px)}}</style>`;

function makeTemplate(instanceId) {
  const angleInset = ANGLE_INSET
    .replaceAll("stress-angle-arrow", `${instanceId}-angle-arrow`)
    .replaceAll("stress-angle-title", `${instanceId}-angle-title`)
    .replaceAll("stress-angle-description", `${instanceId}-angle-description`);
  return `${SCOPED_CSS}${LAYOUT_CSS}<section class="stress-cube-activity" aria-label="Three-dimensional stress cube activity">
    <div class="stress-cube-head"><div><span class="guide-meta-label">3D mechanics model · MPa</span><h4>Absolute maximum-shear plane</h4></div><button type="button" data-stress-reset-view>Reset 3D view</button></div>
    <p class="stress-cube-intro">The cube is a differential element centered at the origin. Drag the 3D view to rotate it; focus the view and use arrow keys for keyboard rotation. Separate insets show the right-handed xyz frame and exact principal-direction angles.</p>
    <div class="stress-cube-stage"><div class="stress-cube-viewer" data-stress-viewer role="group" aria-label="3D stress cube. The controls rotate the cube; its plane and vectors are described by the numerical readout below." tabindex="0"></div><div class="stress-cube-side">${angleInset}<svg class="stress-coordinate-inset" viewBox="0 0 230 142" role="img" aria-labelledby="${instanceId}-frame-title"><title id="${instanceId}-frame-title">Right-handed coordinate frame in the 3D view</title><defs><marker id="${instanceId}-x-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#dc2626"/></marker><marker id="${instanceId}-y-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#16a34a"/></marker><marker id="${instanceId}-z-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#2563eb"/></marker></defs><text x="10" y="18" class="stress-inset-title">Right-handed xyz orientation</text><line x1="112" y1="74" x2="58" y2="102" stroke="#dc2626" stroke-width="3" marker-end="url(#${instanceId}-x-arrow)"/><line x1="112" y1="74" x2="166" y2="102" stroke="#16a34a" stroke-width="3" marker-end="url(#${instanceId}-y-arrow)"/><line x1="112" y1="74" x2="112" y2="31" stroke="#2563eb" stroke-width="3" marker-end="url(#${instanceId}-z-arrow)"/><text x="30" y="110">+x</text><text x="169" y="110">+y</text><text x="117" y="33">+z</text><text x="10" y="132">+x × +y = +z (right-hand rule)</text></svg></div></div>
    <div class="stress-cube-legend" aria-label="Diagram legend"><span class="stress-cube-key"><i class="stress-cube-swatch" style="background:#7e22ce"></i>cut plane</span><span class="stress-cube-key"><i class="stress-cube-swatch" style="background:#c2410c"></i>n, plane normal</span><span class="stress-cube-key"><i class="stress-cube-swatch" style="background:#0369a1"></i>τmax, in-plane shear</span><span class="stress-cube-key"><i class="stress-cube-swatch" style="background:#64748b"></i>+x × +y = +z</span></div>
    <div class="stress-cube-controls"><label><input type="checkbox" data-stress-face-tractions> Show positive-face tractions</label><button type="button" data-stress-toggle-plane aria-pressed="true">Hide shear plane</button><span class="stress-cube-status" data-stress-status role="status" aria-live="polite"></span></div>
    <p class="stress-cube-text-alt">The stress tensor is symmetric, so the principal directions are perpendicular. The selected absolute maximum-shear plane has normal n = (e₁ + e₃)/√2, 45° from both e₁ and e₃. Its normal stress is (σ₁ + σ₃)/2 and its shear magnitude is (σ₁ − σ₃)/2. Tensile stress is positive.</p>
    <details class="stress-cube-data" open><summary>Numerical results and input tensor</summary><div data-stress-readout></div></details>
  </section>`;
}

function renderReadout(host, state) {
  host.innerHTML = `<table class="stress-cube-matrix" aria-label="Symmetric stress tensor in MPa"><caption>σ (MPa)</caption><thead><tr><th></th><th scope="col">x</th><th scope="col">y</th><th scope="col">z</th></tr></thead><tbody>${state.tensor.map((row, index) => `<tr><th scope="row">${["x", "y", "z"][index]}</th>${row.map((value) => `<td>${format(value)}</td>`).join("")}</tr>`).join("")}</tbody></table>
    <div class="stress-cube-results"><p><strong>Principal stresses:</strong> σ₁ ${format(state.principalStresses[0])}, σ₂ ${format(state.principalStresses[1])}, σ₃ ${format(state.principalStresses[2])} MPa</p><p><strong>Maximum shear:</strong> τmax = (σ₁ − σ₃)/2 = ${format(state.maxShearMpa)} MPa</p><p><strong>Plane normal:</strong> n = ${vecText(state.planeNormal)}</p><p><strong>Normal stress on plane:</strong> σn = ${format(state.normalStressMpa)} MPa</p><p><strong>Shear traction:</strong> τ = (${state.shearTraction.map((value) => format(value)).join(", ")}) MPa; magnitude ${format(magnitude(state.shearTraction))} MPa</p><p><strong>Principal construction:</strong> n = (e₁ + e₃)/√2; angle to e₁ = angle to e₃ = 45°${state.planeOrientationUnique ? "." : "; the plane orientation is not unique because an extreme principal stress is repeated."}</p><p><strong>Coordinates:</strong> right-handed x-y-z, +x × +y = +z. Tension is positive.</p></div>`;
}

function addTriangleFace(scene, THREE, points, color, opacity, resources) {
  const center = points.reduce((sum, point) => add(sum, point), [0, 0, 0]).map((x) => x / points.length);
  const positions = [];
  for (let i = 0; i < points.length; i += 1) {
    positions.push(...center, ...points[i], ...points[(i + 1) % points.length]);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.computeVertexNormals?.();
  const material = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity, depthWrite: false });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);
  resources.push(geometry, material);
}

function addLine(scene, THREE, points, color, opacity, resources, lineWidth = 1) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(...point)));
  const material = new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity, linewidth: lineWidth });
  const line = new THREE.Line(geometry, material);
  scene.add(line);
  resources.push(geometry, material);
}

function addArrow(scene, THREE, start, vector, length, color, resources, headLength = 0.22, headWidth = 0.13) {
  if (magnitude(vector) < EPSILON || length < EPSILON) return;
  const arrow = new THREE.ArrowHelper(
    new THREE.Vector3(...unit(vector)), new THREE.Vector3(...start), length, color, headLength, headWidth
  );
  scene.add(arrow);
  if (arrow.line?.geometry) resources.push(arrow.line.geometry);
  if (arrow.line?.material) resources.push(arrow.line.material);
  if (arrow.cone?.geometry) resources.push(arrow.cone.geometry);
  if (arrow.cone?.material) resources.push(arrow.cone.material);
}

function disposeResources(resources) {
  for (const resource of resources) {
    if (Array.isArray(resource)) resource.forEach((item) => item?.dispose?.());
    else resource?.dispose?.();
  }
  resources.length = 0;
}

function renderThree(scene, root, THREE, state, showPlane, showFaceTractions, resources) {
  disposeResources(resources);
  root.clear();
  const boxGeometry = new THREE.BoxGeometry(2, 2, 2);
  const boxMaterial = new THREE.MeshBasicMaterial({ color: 0x94a3b8, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false });
  const box = new THREE.Mesh(boxGeometry, boxMaterial);
  root.add(box); resources.push(boxGeometry, boxMaterial);
  const edgeGeometry = new THREE.EdgesGeometry(boxGeometry);
  const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x475569, linewidth: 2 });
  const edges = new THREE.LineSegments(edgeGeometry, edgeMaterial);
  root.add(edges); resources.push(edgeGeometry, edgeMaterial);

  if (showPlane && state.planeVertices.length >= 3) {
    addTriangleFace(root, THREE, state.planeVertices, 0x9333ea, 0.45, resources);
    addLine(root, THREE, [...state.planeVertices, state.planeVertices[0]], 0x7e22ce, 1, resources, 2);
  }

  const nStart = [0, 0, 0];
  const shearStart = scale(state.e2, 0.45);
  addArrow(root, THREE, nStart, state.planeNormal, 1.62, 0xc2410c, resources, 0.22, 0.14);
  addArrow(root, THREE, shearStart, state.shearTraction, 1.25, 0x0369a1, resources, 0.2, 0.13);

  // A small coordinate triad starts at one cube corner, separate from n and τ at the center.
  const corner = [-1.2, -1.2, -1.2];
  addArrow(root, THREE, corner, [1, 0, 0], 0.72, 0xdc2626, resources, 0.12, 0.08);
  addArrow(root, THREE, corner, [0, 1, 0], 0.72, 0x16a34a, resources, 0.12, 0.08);
  addArrow(root, THREE, corner, [0, 0, 1], 0.72, 0x2563eb, resources, 0.12, 0.08);

  if (showFaceTractions) {
    const maxColumnMagnitude = Math.max(...[0, 1, 2].map((column) => magnitude(state.tensor.map((row) => row[column]))), 1);
    const faceOrigins = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    faceOrigins.forEach((origin, column) => {
      const traction = state.tensor.map((row) => row[column]);
      const len = 0.12 + 0.95 * magnitude(traction) / maxColumnMagnitude;
      addArrow(root, THREE, origin, traction, len, [0xf97316, 0x84cc16, 0xe11d48][column], resources, 0.17, 0.11);
    });
  }
}

function showSvgFallback(viewer, state, showPlane, instanceId, showFaceTractions = false) {
  viewer.innerHTML = fallbackCubeSvg(state, instanceId, showFaceTractions);
  const svg = viewer.querySelector?.("svg");
  if (!showPlane) svg?.querySelector("polygon")?.setAttribute("display", "none");
}

/** Mounts an app-owned stress scene. THREE must be supplied by a trusted fixed adapter. */
export function mountStressCube3D(container, parameters = {}, THREE = null) {
  const tensorValue = parameters?.stress_mpa ?? parameters?.stress_tensor_mpa ?? DEFAULT_TENSOR;
  let state = computeStressCubeState(tensorValue);
  if (!container?.ownerDocument) {
    return { ready: Promise.resolve(false), update() {}, dispose() {} };
  }

  const document = container.ownerDocument;
  const instanceId = `stress-cube-${++stressCubeInstance}`;
  const wrapper = document.createElement("div");
  wrapper.innerHTML = makeTemplate(instanceId);
  container.replaceChildren(wrapper);
  const viewer = wrapper.querySelector("[data-stress-viewer]");
  const readout = wrapper.querySelector("[data-stress-readout]");
  const status = wrapper.querySelector("[data-stress-status]");
  const planeButton = wrapper.querySelector("[data-stress-toggle-plane]");
  const faceCheckbox = wrapper.querySelector("[data-stress-face-tractions]");
  const resetViewButton = wrapper.querySelector("[data-stress-reset-view]");
  renderReadout(readout, state);

  let disposed = false;
  let renderer = null;
  let scene = null;
  let root = null;
  let camera = null;
  let showPlane = true;
  const resources = [];
  const listeners = [];
  let resizeObserver = null;
  const listen = (target, name, callback, options) => {
    target?.addEventListener?.(name, callback, options);
    listeners.push(() => target?.removeEventListener?.(name, callback, options));
  };
  const draw = () => {
    if (!disposed && renderer && scene && camera) renderer.render(scene, camera);
  };
  const rebuild = () => {
    if (renderer && scene && root) {
      renderThree(scene, root, THREE, state, showPlane, faceCheckbox.checked, resources);
      draw();
    } else showSvgFallback(viewer, state, showPlane, instanceId, faceCheckbox.checked);
  };

  try {
    if (!THREE?.WebGLRenderer) throw new Error("Three.js renderer unavailable");
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    renderer.setPixelRatio?.(Math.min(globalThis.devicePixelRatio || 1, 2));
    renderer.setClearColor?.(0xffffff, 0);
    viewer.replaceChildren(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.tabIndex = -1;
    scene = new THREE.Scene();
    root = new THREE.Group();
    scene.add(root);
    camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    camera.position.set(4.3, 4.5, 4.3);
    camera.up?.set?.(0, 0, 1);
    camera.lookAt(0, 0, 0);
    const ambient = new THREE.AmbientLight(0xffffff, 2.1);
    scene.add(ambient);

    const resize = () => {
      if (disposed || !renderer || !camera) return;
      const rect = viewer.getBoundingClientRect();
      const width = Math.max(250, Math.floor(rect.width || viewer.clientWidth || 500));
      const height = Math.max(280, Math.floor(rect.height || viewer.clientHeight || 360));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      draw();
    };
    if (typeof ResizeObserver === "function") {
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(viewer);
    } else listen(globalThis, "resize", resize);

    let dragging = false, previousX = 0, previousY = 0;
    listen(renderer.domElement, "pointerdown", (event) => {
      dragging = true; previousX = event.clientX; previousY = event.clientY;
      renderer.domElement.setPointerCapture?.(event.pointerId);
    });
    listen(renderer.domElement, "pointermove", (event) => {
      if (!dragging) return;
      root.rotation.y += (event.clientX - previousX) * 0.008;
      root.rotation.x = clamp(root.rotation.x + (event.clientY - previousY) * 0.008, -1.2, 1.2);
      previousX = event.clientX; previousY = event.clientY; draw();
    });
    listen(renderer.domElement, "pointerup", () => { dragging = false; });
    listen(renderer.domElement, "pointercancel", () => { dragging = false; });
    listen(viewer, "keydown", (event) => {
      if (!root || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault?.();
      if (event.key === "ArrowLeft") root.rotation.y -= 0.08;
      if (event.key === "ArrowRight") root.rotation.y += 0.08;
      if (event.key === "ArrowUp") root.rotation.x = clamp(root.rotation.x - 0.08, -1.2, 1.2);
      if (event.key === "ArrowDown") root.rotation.x = clamp(root.rotation.x + 0.08, -1.2, 1.2);
      draw();
    });
    rebuild();
    resize();
    status.textContent = "3D view ready. Drag to rotate; the 45° inset stays fixed.";
  } catch {
    resizeObserver?.disconnect?.();
    listeners.splice(0).forEach((remove) => remove());
    disposeResources(resources);
    renderer?.dispose?.();
    renderer = null;
    showSvgFallback(viewer, state, showPlane, instanceId);
    resetViewButton.disabled = true;
    status.textContent = "3D graphics are unavailable; showing a labeled 2D axonometric fallback.";
  }

  listen(planeButton, "click", () => {
    showPlane = !showPlane;
    planeButton.textContent = showPlane ? "Hide shear plane" : "Show shear plane";
    planeButton.setAttribute("aria-pressed", String(showPlane));
    rebuild();
  });
  listen(faceCheckbox, "change", rebuild);
  listen(resetViewButton, "click", () => {
    if (root) { root.rotation.set(0, 0, 0); draw(); }
    status.textContent = "3D view reset.";
  });

  return {
    ready: Promise.resolve({ ok: true, renderer: renderer ? "three" : "svg" }),
    update(next = {}) {
      if (disposed) return;
      state = computeStressCubeState(next?.stress_mpa ?? next?.stress_tensor_mpa ?? tensorValue);
      renderReadout(readout, state);
      rebuild();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      resizeObserver?.disconnect?.();
      listeners.splice(0).forEach((remove) => remove());
      disposeResources(resources);
      renderer?.dispose?.();
      renderer?.domElement?.remove?.();
      wrapper.remove();
    }
  };
}

export const DEFAULT_STRESS_TENSOR_MPA = DEFAULT_TENSOR.map((row) => [...row]);
