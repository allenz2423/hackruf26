import { escapeHtml } from "../ui.js";
import { displayMathHtml, mathHtml, typesetMath } from "../math.js";
import { ANIMAL_CELL_LIMITATION, ANIMAL_CELL_ORGANELLES } from "./animal-cell-data.js";
import { SAMPLING_DEMO_POPULATION, samplingDemoNumber, samplingDemoResult } from "./sampling-demo.js";
import { sanitizeSvgMarkup } from "./safe-svg.js";

let authoredSvgCount = 0;

const MOLECULES = {
  water: {
    name: "Water", formula: "H₂O",
    description: "A schematic bent 2D connectivity diagram with oxygen bonded to two hydrogen atoms; bond angles are not to scale.",
    atoms: [
      { id: "o", element: "O", x: 50, y: 34, description: "Oxygen is the central atom in this model." },
      { id: "h1", element: "H", x: 27, y: 57, description: "This hydrogen is bonded to oxygen." },
      { id: "h2", element: "H", x: 73, y: 57, description: "This hydrogen is bonded to oxygen." }
    ], bonds: [{ from: "o", to: "h1", order: 1 }, { from: "o", to: "h2", order: 1 }]
  },
  carbon_dioxide: {
    name: "Carbon dioxide", formula: "CO₂",
    description: "A linear model with carbon between two oxygen atoms and a double bond to each.",
    atoms: [
      { id: "o1", element: "O", x: 22, y: 36, description: "This terminal oxygen is double-bonded to carbon." },
      { id: "c", element: "C", x: 50, y: 36, description: "Carbon is the central atom in this linear model." },
      { id: "o2", element: "O", x: 78, y: 36, description: "This terminal oxygen is double-bonded to carbon." }
    ], bonds: [{ from: "o1", to: "c", order: 2 }, { from: "c", to: "o2", order: 2 }]
  },
  methane: {
    name: "Methane", formula: "CH₄",
    description: "A 2D projection of one carbon bonded to four hydrogen atoms; it does not represent methane's 3D tetrahedral geometry.",
    atoms: [
      { id: "c", element: "C", x: 50, y: 38, description: "Carbon is the central atom in this model." },
      { id: "h1", element: "H", x: 25, y: 18, description: "This hydrogen is bonded to carbon." },
      { id: "h2", element: "H", x: 75, y: 18, description: "This hydrogen is bonded to carbon." },
      { id: "h3", element: "H", x: 25, y: 58, description: "This hydrogen is bonded to carbon." },
      { id: "h4", element: "H", x: 75, y: 58, description: "This hydrogen is bonded to carbon." }
    ], bonds: [
      { from: "c", to: "h1", order: 1 }, { from: "c", to: "h2", order: 1 },
      { from: "c", to: "h3", order: 1 }, { from: "c", to: "h4", order: 1 }
    ]
  },
  ammonia: {
    name: "Ammonia", formula: "NH₃",
    description: "A 2D projection of one nitrogen bonded to three hydrogen atoms; it does not represent ammonia's 3D geometry.",
    atoms: [
      { id: "n", element: "N", x: 50, y: 39, description: "Nitrogen is the central atom in this model." },
      { id: "h1", element: "H", x: 25, y: 18, description: "This hydrogen is bonded to nitrogen." },
      { id: "h2", element: "H", x: 75, y: 18, description: "This hydrogen is bonded to nitrogen." },
      { id: "h3", element: "H", x: 50, y: 64, description: "This hydrogen is bonded to nitrogen." }
    ], bonds: [
      { from: "n", to: "h1", order: 1 }, { from: "n", to: "h2", order: 1 }, { from: "n", to: "h3", order: 1 }
    ]
  }
};

let activityId = 0;

function number(value, digits = 2) {
  const fixed = Number(value).toFixed(digits);
  return fixed.includes(".") ? fixed.replace(/0+$/, "").replace(/\.$/, "") : fixed;
}

function linearExpression(slope, intercept) {
  const constant = intercept < 0 ? ` − ${number(Math.abs(intercept))}` : ` + ${number(intercept)}`;
  return `${number(slope)}x${constant}`;
}

function linearMath(slope, intercept) {
  const constant = intercept < 0 ? `-${number(Math.abs(intercept))}` : `+${number(intercept)}`;
  return `$y=${number(slope)}x${constant}$`;
}

function safeId(prefix) {
  activityId += 1;
  return `${prefix}-${activityId}`;
}

function modelText(block) {
  return `<details class="activity-text-alternative"><summary>Text description</summary><p>${mathHtml(block.alt_text)}</p><p>${mathHtml(block.fallback_text)}</p></details>`;
}

function disposeGuideLibrary(handle) {
  if (typeof handle === "function") handle();
  else if (typeof handle?.dispose === "function") handle.dispose();
  else handle?.cleanup?.();
}

function mountGuideLibrary(card, load, onReady, onFailure) {
  let handle = null;
  let disposed = false;
  const dialog = card.closest?.("dialog");
  const dispose = () => {
    disposed = true;
    disposeGuideLibrary(handle);
  };
  dialog?.addEventListener?.("close", dispose, { once: true });
  void Promise.resolve().then(load).then(async (mounted) => {
    handle = mounted;
    if (mounted?.ready) {
      const result = await mounted.ready;
      if (result === false || result?.ok === false) throw new Error("Guide library renderer did not become ready.");
    }
    if (disposed || card.isConnected === false) {
      disposeGuideLibrary(handle);
      return;
    }
    onReady?.(mounted);
  }).catch(() => {
    if (!disposed) onFailure?.();
    disposeGuideLibrary(handle);
  });
}

function pairControl(label, key, value, { min, max, step, unit = "", precision = 2 }) {
  const id = safeId(`activity-${key}`);
  const shown = number(value, precision);
  return `<div class="activity-control"><span id="${id}-label">${escapeHtml(label)}</span><span class="activity-control-inputs">
    <input id="${id}-range" type="range" min="${min}" max="${max}" step="${step}" value="${value}" data-activity-control="${key}" aria-labelledby="${id}-label">
    <input id="${id}-number" type="number" min="${min}" max="${max}" step="${step}" value="${value}" data-activity-control="${key}" aria-labelledby="${id}-label">
    ${unit ? `<span class="activity-unit">${escapeHtml(unit)}</span>` : ""}
  </span><span class="activity-control-readout" data-activity-readout="${key}">${escapeHtml(shown)}${unit ? ` ${escapeHtml(unit)}` : ""}</span></div>`;
}

function lineSvg(slope, intercept, id) {
  const left = 42, right = 335, top = 22, bottom = 192;
  const bound = Math.max(10, Math.abs(slope) * 10 + Math.abs(intercept));
  const xAt = (x) => left + ((x + 10) / 20) * (right - left);
  const yAt = (y) => bottom - ((y + bound) / (2 * bound)) * (bottom - top);
  const points = [];
  for (let i = 0; i <= 60; i += 1) {
    const x = -10 + (20 * i) / 60;
    points.push(`${xAt(x).toFixed(2)},${yAt(slope * x + intercept).toFixed(2)}`);
  }
  const yZero = yAt(0);
  const xZero = xAt(0);
  return `<svg class="activity-graph" viewBox="0 0 360 230" role="img" aria-label="Graph of y equals ${escapeHtml(linearExpression(slope, intercept))}. Horizontal x range minus ten to ten.">
    <defs><pattern id="${id}-grid" width="29.3" height="28" patternUnits="userSpaceOnUse"><path d="M 29.3 0 L 0 0 0 28" fill="none" class="activity-grid" /></pattern></defs>
    <rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}" fill="url(#${id}-grid)" />
    <line class="activity-axis" x1="${left}" y1="${yZero}" x2="${right}" y2="${yZero}" /><line class="activity-axis" x1="${xZero}" y1="${top}" x2="${xZero}" y2="${bottom}" />
    <polyline class="activity-plot-line" points="${points.join(" ")}" />
    <text class="activity-plot-label" x="${right - 8}" y="${Math.max(top + 14, Math.min(bottom - 3, yZero - 5))}">x</text>
    <text class="activity-plot-label" x="${xZero + 5}" y="${top + 12}">y</text>
    <text class="activity-plot-label" x="${left}" y="${bottom + 20}">−10</text><text class="activity-plot-label" x="${right - 18}" y="${bottom + 20}">10</text>
  </svg>`;
}

function linearGraphCard(block) {
  const id = safeId("linear-graph");
  const { slope, intercept } = block.parameters;
  return `<section class="study-interactive-model activity-model" data-guide-activity="linear_graph_v1" data-activity-id="${id}" data-default-slope="${slope}" data-default-intercept="${intercept}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Interactive graph</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-activity-reset>Reset</button></div>
    <p class="activity-intro">Explore how the slope and y-intercept change the line. The graph uses a fixed x range from −10 to 10.</p>
    <div class="activity-layout"><figure class="activity-figure"><div data-activity-visual><div data-library-fallback>${lineSvg(slope, intercept, id)}</div><div class="guide-library-visual guide-library-plot" data-jsxgraph-board role="img" aria-label="Interactive JSXGraph plot of y equals ${escapeHtml(linearExpression(slope, intercept))}." hidden></div></div><figcaption>${mathHtml(block.alt_text)}</figcaption></figure>
      <div class="activity-controls">${pairControl("Slope (m)", "slope", slope, { min: -10, max: 10, step: 0.1 })}${pairControl("Y-intercept (b)", "intercept", intercept, { min: -10, max: 10, step: 0.1 })}
      <p class="activity-equation" data-activity-result>${mathHtml(linearMath(slope, intercept))}</p></div></div>${modelText(block)}</section>`;
}

function responseSvg(initial, final, tau, time, id) {
  const left = 45, right = 335, top = 22, bottom = 184;
  const low = Math.min(initial, final), high = Math.max(initial, final);
  const padding = Math.max(1, (high - low) * 0.15);
  const yMin = low - padding, yMax = high + padding;
  const xAt = (seconds) => left + (seconds / (5 * tau)) * (right - left);
  const yAt = (value) => bottom - ((value - yMin) / (yMax - yMin)) * (bottom - top);
  const valueAt = (seconds) => final + (initial - final) * Math.exp(-seconds / tau);
  const points = [];
  for (let i = 0; i <= 60; i += 1) {
    const seconds = 5 * tau * i / 60;
    points.push(`${xAt(seconds).toFixed(2)},${yAt(valueAt(seconds)).toFixed(2)}`);
  }
  return `<svg class="activity-graph" viewBox="0 0 360 225" role="img" aria-label="First-order response starting at ${number(initial)}, approaching ${number(final)}, with time constant ${number(tau)} seconds.">
    <line class="activity-grid" x1="${left}" y1="${top}" x2="${right}" y2="${top}" /><line class="activity-grid" x1="${left}" y1="${(top + bottom) / 2}" x2="${right}" y2="${(top + bottom) / 2}" />
    <line class="activity-axis" x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}" /><line class="activity-axis" x1="${left}" y1="${top}" x2="${left}" y2="${bottom}" />
    <polyline class="activity-plot-line" points="${points.join(" ")}" />
    <line class="activity-current-time" x1="${xAt(time)}" y1="${top}" x2="${xAt(time)}" y2="${bottom}" />
    <circle class="activity-current-point" cx="${xAt(time)}" cy="${yAt(valueAt(time))}" r="5" />
    <text class="activity-plot-label" x="${left}" y="${bottom + 20}">0 s</text><text class="activity-plot-label" x="${right - 34}" y="${bottom + 20}">5τ</text>
  </svg>`;
}

function responseCard(block) {
  const id = safeId("response");
  const { initial_value: initial, final_value: final, time_constant_s: tau } = block.parameters;
  const time = 0;
  return `<section class="study-interactive-model activity-model" data-guide-activity="first_order_response_v1" data-activity-id="${id}" data-default-initial="${initial}" data-default-final="${final}" data-default-tau="${tau}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Interactive model</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-activity-reset>Reset</button></div>
    <p class="activity-intro">This ideal first-order response follows y(t) = y<sub>f</sub> + (y<sub>0</sub> − y<sub>f</sub>)e<sup>−t/τ</sup>.</p>
    <div class="activity-layout"><figure class="activity-figure"><div data-activity-visual><div data-library-fallback>${responseSvg(initial, final, tau, time, id)}</div><div class="guide-library-visual guide-library-plot" data-echarts-response role="img" aria-label="Interactive ECharts plot of the first-order response." hidden></div></div><figcaption>${mathHtml(block.alt_text)}</figcaption></figure>
      <div class="activity-controls">${pairControl("Initial value", "initial_value", initial, { min: -1000, max: 1000, step: 1 })}${pairControl("Final value", "final_value", final, { min: -1000, max: 1000, step: 1 })}${pairControl("Time constant", "time_constant_s", tau, { min: 0.01, max: 3600, step: 0.01, unit: "s" })}
      <label class="activity-control" for="${id}-time"><span>Time</span><span class="activity-control-inputs"><input id="${id}-time" type="range" min="0" max="${5 * tau}" step="${Math.max(0.01, tau / 100)}" value="0" data-activity-control="time" aria-label="Elapsed time in seconds"><span class="activity-unit">s</span></span><span class="activity-control-readout" data-activity-readout="time">0 s</span></label>
      <p class="activity-equation" data-activity-result>At 0 s: y = ${escapeHtml(number(initial))}.</p></div></div>${modelText(block)}</section>`;
}

function circuitSvg(id) {
  return `<svg class="activity-circuit" viewBox="0 0 360 220" role="img" aria-label="An ideal source V in series with resistors R one and R two. Output voltage is measured across R two.">
    <path class="circuit-wire" d="M70 28 H290 V188 H70 V28 M70 65 V97 M70 126 V188" />
    <path class="circuit-resistor" d="M290 44 l-10 8 20 12-20 12 20 12-20 12 10 8 M290 112 l-10 8 20 12-20 12 20 12-20 12 10 8" />
    <path class="circuit-wire" d="M70 65 H290 M70 126 H290 M270 126 V188 M260 126 H280 M265 134 H275" />
    <path class="circuit-battery" d="M55 97 H85 M60 126 H80" />
    <text class="circuit-label" x="300" y="80">R₁</text><text class="circuit-label" x="300" y="150">R₂</text>
    <text class="circuit-label" x="22" y="96">Vᵢₙ</text><text class="circuit-label" x="276" y="119">Vₒᵤₜ</text>
    <circle class="circuit-node" cx="290" cy="105" r="4" /><title id="${id}-schematic-title">Resistor divider</title>
  </svg>`;
}

function circuitCard(block) {
  const id = safeId("divider");
  const { voltage_v: voltage, r1_ohm: r1, r2_ohm: r2 } = block.parameters;
  const output = voltage * r2 / (r1 + r2);
  const current = voltage / (r1 + r2);
  return `<section class="study-interactive-model activity-model" data-guide-activity="resistor_divider_v1" data-activity-id="${id}" data-default-voltage="${voltage}" data-default-r1="${r1}" data-default-r2="${r2}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Ideal circuit model</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-activity-reset>Reset</button></div>
    <p class="activity-intro">Assume ideal wires, an ideal source, and no load connected to the output.</p>
    <div class="activity-layout"><figure class="activity-figure"><div data-activity-visual>${circuitSvg(id)}</div><figcaption>${mathHtml(block.alt_text)}</figcaption></figure>
      <div class="activity-controls">${pairControl("Input voltage", "voltage_v", voltage, { min: 0, max: 24, step: 0.1, unit: "V" })}${pairControl("Upper resistor R₁", "r1_ohm", r1, { min: 1, max: 1_000_000, step: 1, unit: "Ω", precision: 0 })}${pairControl("Lower resistor R₂", "r2_ohm", r2, { min: 1, max: 1_000_000, step: 1, unit: "Ω", precision: 0 })}
      <p class="activity-equation">${mathHtml("$V_{out}=V_{in}\\frac{R_2}{R_1+R_2}$")}</p>
      <p class="activity-result" data-activity-result>Vout = ${number(output)} V; series current = ${number(current * 1000)} mA.</p></div></div>${modelText(block)}</section>`;
}

function traceFor(values, target) {
  const trace = [];
  let low = 0, high = values.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const value = values[middle];
    const comparison = target === value ? "equal" : (target < value ? "smaller" : "larger");
    trace.push({ low, high, middle, value, comparison });
    if (comparison === "equal") return { trace, found: middle };
    if (comparison === "smaller") high = middle - 1;
    else low = middle + 1;
  }
  return { trace, found: null };
}

function binarySearchCard(block) {
  const id = safeId("binary-search");
  const { values, target } = block.parameters;
  return `<section class="study-interactive-model activity-model" data-guide-activity="binary_search_trace_v1" data-activity-id="${id}" data-values="${escapeHtml(JSON.stringify(values))}" data-target="${target}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Algorithm trace</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-trace-reset>Reset</button></div>
    <p class="activity-intro">Binary search compares the target with the middle value, then keeps only the half that can still contain it.</p>
    <p class="binary-search-values"><strong>Sorted values:</strong> ${values.map((value) => `<span>${value}</span>`).join(" ")} <strong>Target:</strong> ${target}</p>
    <ol class="binary-search-trace" data-trace-steps></ol><p class="activity-result" data-trace-status role="status" aria-live="polite">Choose Next to reveal the first comparison.</p>
    <div class="activity-actions"><button class="btn btn-secondary" type="button" data-trace-previous disabled>Previous</button><button class="btn btn-primary" type="button" data-trace-next>Next comparison</button></div>${modelText(block)}</section>`;
}

function samplingPopulationRows() {
  return SAMPLING_DEMO_POPULATION.map((row) => `<tr data-sampling-id="${row.id}">
    <th scope="row">${row.id}</th><td>${row.stratum}</td><td>${row.practiceProblems}</td>
    <td data-sampling-status>Not selected</td><td data-sampling-treatment>—</td></tr>`).join("");
}

function samplingCard(block) {
  const id = safeId("sampling-demo");
  const sampleSize = block.parameters.sample_size;
  return `<section class="study-interactive-model activity-model sampling-demo" data-guide-activity="sampling_demo_v1" data-activity-id="${id}" data-default-method="${block.parameters.method}" data-default-sample-size="${sampleSize}" data-default-seed="${block.parameters.seed}" data-default-random-assignment="${block.parameters.random_assignment}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Statistics sampling lab</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-sampling-reset>Reset</button></div>
    <p class="activity-intro">Use the same fixed, app-authored fictional population to compare who gets sampled and, separately, which treatment selected participants receive.</p>
    <p class="sampling-data-label"><strong>App-authored synthetic illustration; not course source data.</strong> The 24 practice-problem counts are fictional. The front-row subgroup is deliberately constructed with higher values to illustrate one possible bias, not a universal claim.</p>
    <fieldset class="sampling-controls"><legend>Choose the sampling process</legend>
      <label for="${id}-method">Sampling method</label><select id="${id}-method" data-sampling-method>
        <option value="simple_random"${block.parameters.method === "simple_random" ? " selected" : ""}>Simple random sample</option><option value="stratified"${block.parameters.method === "stratified" ? " selected" : ""}>Proportional stratified sample</option><option value="front_row_convenience"${block.parameters.method === "front_row_convenience" ? " selected" : ""}>Front-row convenience sample</option>
      </select>
      <label for="${id}-size">Sample size</label><select id="${id}-size" data-sampling-size>${[4, 6, 8, 10, 12].map((size) => `<option value="${size}"${size === sampleSize ? " selected" : ""}>${size} students</option>`).join("")}</select>
      <label class="sampling-assignment-toggle" for="${id}-assignment"><input id="${id}-assignment" type="checkbox" data-sampling-assignment-toggle${block.parameters.random_assignment ? " checked" : ""}> Randomly assign sampled students to two treatments</label>
      <div class="sampling-actions"><button class="btn btn-secondary" type="button" data-sampling-redraw>Draw sample again</button><span data-sampling-seed>Seed ${block.parameters.seed}</span></div>
    </fieldset>
    <p class="sampling-method-note" data-sampling-method-note></p>
    <p class="activity-result sampling-result" data-sampling-result role="status" aria-live="polite"></p>
    <div class="sampling-table-scroll"><table class="sampling-population-table"><caption>All 24 fictional students. Selection and treatment assignment update from the controls above.</caption><thead><tr><th scope="col">Student</th><th scope="col">Stratum</th><th scope="col">Practice problems last week</th><th scope="col">Sample</th><th scope="col">Assigned treatment</th></tr></thead><tbody data-sampling-population>${samplingPopulationRows()}</tbody></table></div>
    <p class="activity-intro sampling-concept-note">Random sampling concerns who enters a study and supports generalization to its population when done well. Random assignment concerns which treatment sampled participants receive and supports fair treatment comparisons. Assignment does not repair a biased sample. This seeded pseudorandom draw is a repeatable teaching demonstration, not a production sampling method.</p>
    ${modelText(block)}</section>`;
}

function moleculeSvg(moleculeId, id) {
  const molecule = MOLECULES[moleculeId];
  const atomById = Object.fromEntries(molecule.atoms.map((atom) => [atom.id, atom]));
  const point = (atom) => ({ x: 50 + atom.x * 2.6, y: 10 + atom.y * 2.7 });
  const bonds = molecule.bonds.map((bond) => {
    const from = point(atomById[bond.from]);
    const to = point(atomById[bond.to]);
    const dx = to.x - from.x, dy = to.y - from.y;
    const length = Math.hypot(dx, dy) || 1;
    const offset = bond.order === 2 ? 3 : 0;
    const ox = -dy / length * offset, oy = dx / length * offset;
    return Array.from({ length: bond.order }, (_, index) => {
      const shift = bond.order === 1 ? 0 : (index === 0 ? -1 : 1);
      return `<line class="molecule-bond" x1="${from.x + ox * shift}" y1="${from.y + oy * shift}" x2="${to.x + ox * shift}" y2="${to.y + oy * shift}" />`;
    }).join("");
  }).join("");
  const atoms = molecule.atoms.map((atom) => {
    const { x, y } = point(atom);
    return `<g class="molecule-atom" data-molecule-atom="${atom.id}" transform="translate(${x} ${y})" role="button" tabindex="0" aria-pressed="false" aria-label="${escapeHtml(`${atom.element} atom. ${atom.description}`)}"><circle class="molecule-atom-circle atom-${atom.element.toLowerCase()}" r="17" /><text class="molecule-atom-label" text-anchor="middle" dy=".35em">${atom.element}</text></g>`;
  }).join("");
  return `<svg class="molecule-figure" viewBox="0 0 360 210" role="group" aria-label="${escapeHtml(`${molecule.name}, ${molecule.formula}. Select an atom for its description.`)}"><title id="${id}-molecule-title">${escapeHtml(molecule.name)} (${escapeHtml(molecule.formula)})</title>${bonds}${atoms}</svg>`;
}

function moleculeCard(block) {
  const id = safeId("molecule");
  const molecule = MOLECULES[block.molecule_id];
  return `<section class="study-interactive-model activity-model molecule-model" data-guide-activity="molecule_2d_v1" data-molecule-id="${block.molecule_id}" data-activity-id="${id}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">2D molecular model</span><h4 id="${id}-title">${mathHtml(block.title)} <span class="molecule-formula">${escapeHtml(molecule.formula)}</span></h4></div></div>
    <div class="activity-layout"><figure class="activity-figure"><div data-molecule-visual><div data-molecule-fallback>${moleculeSvg(block.molecule_id, id)}</div><div class="guide-library-visual guide-library-molecule" data-smiles-drawer role="img" aria-label="SmilesDrawer molecular structure for ${escapeHtml(molecule.name)}." hidden></div></div><figcaption>${mathHtml(block.alt_text)}</figcaption></figure>
      <div class="molecule-description"><p>${escapeHtml(molecule.description)}</p><p>Select an atom with a mouse, touch, Enter, or Space to read its role in this model.</p><div class="molecule-atom-controls" aria-label="Molecule atoms">${molecule.atoms.map((atom) => `<button class="btn btn-secondary btn-small" type="button" data-molecule-atom="${atom.id}" aria-pressed="false" aria-label="${escapeHtml(`${atom.element} atom. ${atom.description}`)}">${escapeHtml(atom.element)} atom</button>`).join("")}</div><p class="activity-result" data-molecule-status role="status" aria-live="polite">Select an atom to inspect it.</p></div></div>${modelText(block)}</section>`;
}

function processPathwayCard(block) {
  const id = safeId("pathway");
  return `<section class="study-interactive-model activity-model process-pathway" data-guide-activity="process_pathway_v1" data-activity-id="${id}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Process pathway</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div></div>
    <p class="activity-intro">Select a stage to read its explanation. The order follows the supplied course material.</p>
    <div class="guide-library-visual guide-library-diagram" data-mermaid-pathway role="img" aria-label="Mermaid flow diagram of the ordered process stages." hidden></div>
    <ol class="pathway-stage-list">${block.stages.map((stage, index) => `<li><div class="pathway-stage-card" data-pathway-stage-card${index === 0 ? " data-active" : ""}><button class="pathway-stage-button" type="button" data-pathway-stage="${escapeHtml(stage.id)}" data-stage-title="${escapeHtml(stage.title)}"${index === 0 ? ` aria-describedby="${id}-stage-explanation-${index}"` : ""} aria-pressed="${index === 0}"><span>Stage ${index + 1}</span><strong>${mathHtml(stage.title)}</strong></button><p class="pathway-stage-explanation" id="${id}-stage-explanation-${index}" data-pathway-explanation="${escapeHtml(stage.id)}" data-stage-text="${escapeHtml(stage.explanation)}"${index > 0 ? " hidden" : ""}>${mathHtml(stage.explanation)}</p></div>${index < block.stages.length - 1 ? `<span class="pathway-connector" aria-hidden="true">↓</span>` : ""}</li>`).join("")}</ol>
    <p class="activity-result visually-hidden" data-pathway-status role="status" aria-live="polite">Stage 1 of ${block.stages.length}: ${mathHtml(block.stages[0].explanation)}</p>${modelText(block)}</section>`;
}

function animalCellSvg(focusIds, id) {
  const includes = (feature) => focusIds.includes(feature);
  const structures = [
    includes("cytoskeleton") ? '<path class="cell-map-cytoskeleton" d="M78 75 Q158 109 267 66 M82 150 Q178 119 282 153 M127 46 Q176 107 250 182" />' : "",
    includes("endoplasmic_reticulum") ? '<path class="cell-map-er" d="M174 79 C202 68 218 81 195 94 C176 105 198 115 225 103 M174 119 C199 108 222 120 199 133 C181 144 208 154 228 140" />' : "",
    includes("golgi_apparatus") ? '<path class="cell-map-golgi" d="M90 127 Q111 119 132 128 M88 135 Q111 127 135 136 M90 143 Q112 135 132 144" />' : "",
    includes("lysosomes_peroxisomes") ? '<circle class="cell-map-vesicle" cx="275" cy="112" r="11" /><circle class="cell-map-vesicle" cx="251" cy="153" r="8" />' : "",
    includes("mitochondria") ? '<path class="cell-map-mitochondrion" d="M74 92 C78 72 111 72 120 91 C129 110 103 119 84 110 C77 107 72 101 74 92 Z M82 93 Q89 83 96 94 T112 93" />' : "",
    includes("nucleus") ? '<ellipse class="cell-map-nucleus" cx="169" cy="107" rx="34" ry="26" /><path class="cell-map-nucleolus" d="M165 104 C170 94 183 100 181 109 C179 119 164 119 162 110" />' : "",
    includes("ribosomes") ? '<g class="cell-map-ribosomes"><circle cx="220" cy="62" r="2" /><circle cx="235" cy="70" r="2" /><circle cx="247" cy="59" r="2" /><circle cx="205" cy="151" r="2" /><circle cx="217" cy="163" r="2" /></g>' : ""
  ].join("");
  const markers = focusIds.map((feature, index) => {
    const item = ANIMAL_CELL_ORGANELLES[feature];
    const x = item.x * 3.6, y = item.y * 1.8 + 10;
    const label = `${item.name}. ${item.description}`;
    return `<g class="cell-map-marker" data-cell-feature="${feature}" role="button" tabindex="0" aria-pressed="${index === 0}" aria-label="${escapeHtml(label)}" transform="translate(${x} ${y})"><circle r="13" /><text text-anchor="middle" dy=".35em">${index + 1}</text></g>`;
  }).join("");
  const titleId = `${id}-cell-map-title`;
  const descriptionId = `${id}-cell-map-description`;
  return `<svg class="animal-cell-figure" viewBox="0 0 360 210" role="group" aria-labelledby="${titleId} ${descriptionId}">
    <title id="${titleId}">Schematic animal cell</title><desc id="${descriptionId}">${escapeHtml(ANIMAL_CELL_LIMITATION)} Select a numbered feature to inspect it.</desc>
    <path class="cell-map-outline" d="M180 17 C255 15 319 44 325 96 C331 148 280 189 204 194 C130 199 52 173 38 124 C24 74 78 28 147 19 C158 18 169 17 180 17 Z" />
    ${structures}${markers}
  </svg>`;
}

function animalCellMapCard(block) {
  const id = safeId("animal-cell");
  const first = ANIMAL_CELL_ORGANELLES[block.focus_ids[0]];
  return `<section class="study-interactive-model activity-model animal-cell-model" data-guide-activity="animal_cell_map_v1" data-activity-id="${id}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Cell structure map</span><h4 id="${id}-title">${escapeHtml(block.title)}</h4></div></div>
    <p class="activity-intro">${escapeHtml(ANIMAL_CELL_LIMITATION)}</p>
    <div class="animal-cell-layout"><figure class="activity-figure"><div data-cell-map-visual>${animalCellSvg(block.focus_ids, id)}</div><figcaption>${escapeHtml(block.alt_text)}</figcaption></figure>
      <div class="cell-map-explorer"><p class="activity-result" data-cell-map-status role="status" aria-live="polite">${escapeHtml(`${first.name}: ${first.description}`)}</p>
        <ul class="cell-map-feature-list">${block.focus_ids.map((feature, index) => {
    const item = ANIMAL_CELL_ORGANELLES[feature];
    return `<li><button class="cell-map-feature-button" type="button" data-cell-feature="${feature}" aria-pressed="${index === 0}"><span>${index + 1}</span>${escapeHtml(item.name)}</button></li>`;
  }).join("")}</ul></div></div>${modelText(block)}</section>`;
}

function exampleStepsCard(block) {
  const id = safeId("example-steps");
  return `<section class="guide-example-steps" data-guide-activity="example_steps" data-activity-id="${id}" aria-labelledby="${id}-title">
    <div class="activity-heading"><div><span class="guide-meta-label">Worked example</span><h4 id="${id}-title">${mathHtml(block.title)}</h4></div><button class="btn btn-secondary btn-small" type="button" data-example-reset>Start over</button></div>
    <ol class="example-step-list">${block.steps.map((step, index) => `<li data-example-step="${index}"${index > 0 ? " hidden" : ""}><strong>${mathHtml(step.title)}</strong><p>${mathHtml(step.explanation)}</p>${step.expression ? `<p class="example-step-expression">${displayMathHtml(step.expression)}</p>` : ""}</li>`).join("")}</ol>
    <p class="activity-result" data-example-status role="status" aria-live="polite">Step 1 of ${block.steps.length} is shown.</p><button class="btn btn-secondary" type="button" data-example-next${block.steps.length <= 1 ? " disabled" : ""}>Reveal next step</button></section>`;
}

function authoredSvgCard(block) {
  const id = safeId("authored-svg");
  const markup = sanitizeSvgMarkup(block.svg_markup, {
    idPrefix: `guide-svg-${++authoredSvgCount}`,
    altText: block.alt_text
  });
  if (!markup) {
    return `<aside class="guide-activity-unavailable" role="note"><strong>${escapeHtml(block.title)}</strong><p>${mathHtml(block.fallback_text)}</p></aside>`;
  }
  const animationControls = block.animated
    ? `<button class="btn btn-secondary btn-small" type="button" data-svg-play>Play</button>
       <button class="btn btn-secondary btn-small" type="button" data-svg-pause>Pause</button>
       <button class="btn btn-secondary btn-small" type="button" data-svg-restart>Restart</button>`
    : "";
  return `<section class="study-interactive-model authored-svg-model" data-guide-activity="svg_diagram" data-activity-id="${id}" aria-labelledby="${id}-title">
    <div class="activity-heading"><h4 id="${id}-title">${mathHtml(block.title)}</h4></div>
    <figure class="authored-svg-figure"><div id="${id}-frame" class="authored-svg-frame" data-authored-svg>${markup}</div><figcaption>${mathHtml(block.fallback_text)}</figcaption></figure>
    <div class="authored-svg-controls" role="group" aria-label="Diagram and animation controls" aria-controls="${id}-frame">
      ${animationControls}
      <button class="btn btn-secondary btn-small" type="button" data-svg-zoom-out aria-label="Zoom out of diagram" title="Zoom out" disabled>−</button>
      <output data-svg-zoom-status aria-live="polite">100%</output>
      <button class="btn btn-secondary btn-small" type="button" data-svg-zoom-in aria-label="Zoom in on diagram" title="Zoom in">+</button>
      <button class="btn btn-secondary btn-small" type="button" data-svg-zoom-reset>Reset view</button>
    </div>
  </section>`;
}

export function renderAdditionalGuideBlock(block) {
  if (block.type === "svg_diagram") return authoredSvgCard(block);
  if (block.type === "example_steps") return exampleStepsCard(block);
  if (block.type === "labeled_diagram" && block.template_id === "animal_cell_map_v1") return animalCellMapCard(block);
  if (block.type === "labeled_diagram" && block.template_id === "process_pathway_v1") return processPathwayCard(block);
  if (block.type === "labeled_diagram" && block.template_id === "molecule_2d_v1" && MOLECULES[block.molecule_id]) return moleculeCard(block);
  if (block.type === "labeled_diagram" && block.template_id === "molecule_3d_v1") return molecule3dCard(block);
  if (block.type !== "interactive_model") return null;
  if (block.template_id === "linear_graph_v1") return linearGraphCard(block);
  if (block.template_id === "first_order_response_v1") return responseCard(block);
  if (block.template_id === "resistor_divider_v1") return circuitCard(block);
  if (block.template_id === "binary_search_trace_v1") return binarySearchCard(block);
  if (block.template_id === "sampling_demo_v1") return samplingCard(block);
  if (block.template_id === "graph_traversal_v1") return graphTraversalCard(block);
  if (block.template_id === "stress_cube_3d_v1") return stressCube3dCard(block);
  if (block.template_id === "python_algorithm_trace_v1") return pythonAlgorithmCard(block);
  if (block.template_id === "simcir_circuit_v1") return simcirCircuitCard(block);
  return null;
}

function encodedActivityPayload(block) {
  return escapeHtml(JSON.stringify(block));
}

function graphTraversalCard(block) {
  const id = safeId("graph-traversal");
  const nodeLabels = new Map(block.parameters.nodes.map((node) => [node.id, node.label]));
  const relationships = block.parameters.edges.map(({ source, target }) =>
    `<li>${escapeHtml(nodeLabels.get(source))} → ${escapeHtml(nodeLabels.get(target))}</li>`
  ).join("");
  return `<section class="study-interactive-model activity-model" data-guide-activity="graph_traversal_v1" data-activity-id="${id}" data-activity-payload="${encodedActivityPayload(block)}" aria-labelledby="${id}-title">
    <h4 id="${id}-title">${escapeHtml(block.title)}</h4>
    <p class="activity-intro">${escapeHtml(block.alt_text)}</p>
    <div class="guide-graph-traversal-mount" data-graph-traversal-mount>
      <details open><summary>Text version of this graph</summary><p>${escapeHtml(block.fallback_text)}</p><ul>${relationships}</ul></details>
    </div>
    ${modelText(block)}</section>`;
}

function stressCube3dCard(block) {
  const id = safeId("stress-cube-3d");
  return `<section class="study-interactive-model activity-model" data-guide-activity="stress_cube_3d_v1" data-activity-id="${id}" data-activity-payload="${encodedActivityPayload(block)}" aria-labelledby="${id}-title">
    <h4 id="${id}-title">${escapeHtml(block.title)}</h4>
    <p class="activity-intro">${escapeHtml(block.alt_text)}</p>
    <div class="guide-stress-cube-mount" data-stress-cube-mount><p role="status">${escapeHtml(block.fallback_text)}</p></div>
    ${modelText(block)}</section>`;
}

function molecule3dCard(block) {
  const id = safeId("molecule-3d");
  return `<section class="study-interactive-model activity-model" data-guide-activity="molecule_3d_v1" data-activity-id="${id}" data-activity-payload="${encodedActivityPayload(block)}" aria-labelledby="${id}-title">
    <h4 id="${id}-title">${escapeHtml(block.title)}</h4>
    <p class="activity-intro">${escapeHtml(block.alt_text)}</p>
    <div class="guide-molecule-3d-mount" data-molecule-3d-mount><p role="status">${escapeHtml(block.fallback_text)}</p></div>
    ${modelText(block)}</section>`;
}

function pythonAlgorithmCard(block) {
  const id = safeId("python-algorithm");
  const values = block.parameters.values.join(", ");
  const target = Number.isInteger(block.parameters.target) ? `; target ${block.parameters.target}` : "";
  return `<section class="study-interactive-model activity-model" data-guide-activity="python_algorithm_trace_v1" data-activity-id="${id}" data-activity-payload="${encodedActivityPayload(block)}" aria-labelledby="${id}-title">
    <h4 id="${id}-title">${escapeHtml(block.title)}</h4>
    <p class="activity-intro">${escapeHtml(block.alt_text)}</p>
    <p>Example input: <code>[${escapeHtml(values)}]${escapeHtml(target)}</code></p>
    <div class="guide-python-activity-mount" data-python-activity-mount>
      <p role="status">${escapeHtml(block.fallback_text || "The fixed Python example can be reviewed as text if the interactive runtime is unavailable.")}</p>
    </div>
    ${modelText(block)}</section>`;
}

function simcirCircuitCard(block) {
  const id = safeId("simcir-circuit");
  return `<section class="study-interactive-model activity-model" data-guide-activity="simcir_circuit_v1" data-activity-id="${id}" data-activity-payload="${encodedActivityPayload(block)}" aria-labelledby="${id}-title">
    <h4 id="${id}-title">${escapeHtml(block.title)}</h4>
    <p class="activity-intro">${escapeHtml(block.alt_text)}</p>
    <div class="guide-simcir-circuit-mount" data-simcir-circuit-mount><p role="status">${escapeHtml(block.fallback_text)}</p></div>
    ${modelText(block)}</section>`;
}

function activityPayload(card) {
  try {
    const payload = JSON.parse(card.dataset.activityPayload || "null");
    return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : null;
  } catch {
    return null;
  }
}

function clamped(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function mountPairedControls(card, keys, defaults, update, onReset = null) {
  const inputs = [...card.querySelectorAll("[data-activity-control]")]
    .filter((input) => keys.includes(input.dataset.activityControl));
  function readValues() {
    return Object.fromEntries(keys.map((key) => {
      const focused = document.activeElement;
      const active = inputs.find((input) => input.dataset.activityControl === key && input === focused);
      const fallback = inputs.find((input) => input.dataset.activityControl === key && input.type === "number");
      const value = Number((active || fallback)?.value ?? defaults[key]);
      const source = active || fallback;
      return [key, Number.isFinite(value) ? clamped(value, Number(source.min), Number(source.max)) : defaults[key]];
    }));
  }
  function sync() {
    const values = readValues();
    for (const input of inputs) {
      const key = input.dataset.activityControl;
      if (key in values && input !== document.activeElement) input.value = String(values[key]);
    }
    for (const key of keys) {
      const readout = card.querySelector(`[data-activity-readout="${key}"]`);
      if (readout) readout.textContent = `${number(values[key])}${readout.textContent.match(/\s[A-Za-zΩ]+$/)?.[0] || ""}`;
    }
    update(values);
  }
  inputs.forEach((input) => {
    input.addEventListener("input", () => { if (input.value !== "") sync(); });
    input.addEventListener("change", () => {
      const min = Number(input.min), max = Number(input.max), numeric = Number(input.value);
      if (!Number.isFinite(numeric)) input.value = String(defaults[input.dataset.activityControl]);
      else input.value = String(clamped(numeric, min, max));
      sync();
    });
  });
  card.querySelector("[data-activity-reset]")?.addEventListener("click", () => {
    inputs.forEach((input) => { if (input.dataset.activityControl in defaults) input.value = String(defaults[input.dataset.activityControl]); });
    onReset?.();
    sync();
  });
  return { sync, inputs };
}

function mountLinearGraph(card) {
  const id = card.dataset.activityId;
  const defaults = { slope: Number(card.dataset.defaultSlope), intercept: Number(card.dataset.defaultIntercept) };
  const fallback = card.querySelector("[data-library-fallback]") || card.querySelector("[data-activity-visual]");
  const plot = card.querySelector("[data-jsxgraph-board]");
  let graph = null;
  let latest = defaults;
  mountPairedControls(card, Object.keys(defaults), defaults, ({ slope, intercept }) => {
    latest = { slope, intercept };
    if (fallback) fallback.innerHTML = lineSvg(slope, intercept, id);
    graph?.update?.(latest);
    const result = card.querySelector("[data-activity-result]");
    result.innerHTML = mathHtml(linearMath(slope, intercept));
    void typesetMath(result);
  });
  if (plot && fallback) {
    mountGuideLibrary(card, async () => {
      const { mountLinearGraph: mountPlot } = await import("./libraries/plots.js");
      return mountPlot(plot, defaults);
    }, (mounted) => {
      graph = mounted;
      plot.hidden = false;
      fallback.hidden = true;
      graph?.resize?.();
      graph?.update?.(latest);
    }, () => { fallback.hidden = false; plot.hidden = true; });
  }
}

function mountResponse(card) {
  const id = card.dataset.activityId;
  const defaults = {
    initial_value: Number(card.dataset.defaultInitial),
    final_value: Number(card.dataset.defaultFinal),
    time_constant_s: Number(card.dataset.defaultTau)
  };
  let currentTime = 0;
  const fallback = card.querySelector("[data-library-fallback]") || card.querySelector("[data-activity-visual]");
  const plot = card.querySelector("[data-echarts-response]");
  let responseChart = null;
  let latest = { initial: defaults.initial_value, final: defaults.final_value, tau: defaults.time_constant_s, time: currentTime };
  const timeInput = card.querySelector('[data-activity-control="time"]');
  const controller = mountPairedControls(card, Object.keys(defaults), defaults, ({ initial_value: initial, final_value: final, time_constant_s: tau }) => {
    timeInput.max = String(5 * tau);
    timeInput.step = String(Math.max(0.01, tau / 100));
    currentTime = clamped(currentTime, 0, 5 * tau);
    timeInput.value = String(currentTime);
    latest = { initial, final, tau, time: currentTime };
    const current = final + (initial - final) * Math.exp(-currentTime / tau);
    if (fallback) fallback.innerHTML = responseSvg(initial, final, tau, currentTime, id);
    responseChart?.update?.(latest);
    const result = card.querySelector("[data-activity-result]");
    result.innerHTML = mathHtml(`At ${number(currentTime)} s: y = ${number(current)}.`);
    void typesetMath(result);
    card.querySelector('[data-activity-readout="time"]').textContent = `${number(currentTime)} s`;
  }, () => { currentTime = 0; });
  timeInput.addEventListener("input", () => {
    const value = Number(timeInput.value);
    if (Number.isFinite(value)) currentTime = value;
    controller.sync();
  });
  if (plot && fallback) {
    mountGuideLibrary(card, async () => {
      const { mountResponsePlot } = await import("./libraries/plots.js");
      return mountResponsePlot(plot, latest);
    }, (mounted) => {
      responseChart = mounted;
      plot.hidden = false;
      fallback.hidden = true;
      responseChart?.update?.(latest);
    }, () => { fallback.hidden = false; plot.hidden = true; });
  }
}

function mountDivider(card) {
  const defaults = { voltage_v: Number(card.dataset.defaultVoltage), r1_ohm: Number(card.dataset.defaultR1), r2_ohm: Number(card.dataset.defaultR2) };
  mountPairedControls(card, Object.keys(defaults), defaults, ({ voltage_v: voltage, r1_ohm: r1, r2_ohm: r2 }) => {
    const output = voltage * r2 / (r1 + r2);
    const current = voltage / (r1 + r2);
    const result = card.querySelector("[data-activity-result]");
    result.innerHTML = mathHtml(`V_{out} = ${number(output)} V; series current = ${number(current * 1000)} mA.`);
    void typesetMath(result);
  });
}

function mountBinarySearch(card) {
  let values = [];
  try { values = JSON.parse(card.dataset.values); } catch { return; }
  const target = Number(card.dataset.target);
  if (!Array.isArray(values) || values.length > 20 || !values.every(Number.isInteger)) return;
  const { trace, found } = traceFor(values, target);
  const list = card.querySelector("[data-trace-steps]");
  const status = card.querySelector("[data-trace-status]");
  const previous = card.querySelector("[data-trace-previous]");
  const next = card.querySelector("[data-trace-next]");
  let shown = 0;
  list.innerHTML = trace.map((step, index) => `<li data-trace-step="${index}" hidden><strong>Compare ${step.value} at index ${step.middle}</strong><p>Search range: indexes ${step.low}–${step.high}. The target is ${step.comparison === "equal" ? "equal to" : step.comparison === "smaller" ? "less than" : "greater than"} ${step.value}.</p></li>`).join("");
  function draw() {
    list.querySelectorAll("[data-trace-step]").forEach((item, index) => { item.hidden = index >= shown; });
    previous.disabled = shown === 0;
    next.disabled = shown >= trace.length;
    if (shown === 0) status.textContent = `Ready to search ${values.length} sorted values for ${target}.`;
    else if (shown < trace.length) status.textContent = `Comparison ${shown} of ${trace.length} complete. Continue to inspect the next half.`;
    else status.textContent = found == null ? `Search complete. ${target} is not in the list.` : `Search complete. ${target} is at index ${found}.`;
    next.textContent = shown >= trace.length ? "Trace complete" : "Next comparison";
  }
  next.addEventListener("click", () => { shown = Math.min(trace.length, shown + 1); draw(); });
  previous.addEventListener("click", () => { shown = Math.max(0, shown - 1); draw(); });
  card.querySelector("[data-trace-reset]").addEventListener("click", () => { shown = 0; draw(); });
  draw();
}

function mountSamplingDemo(card) {
  const methodInput = card.querySelector("[data-sampling-method]");
  const sizeInput = card.querySelector("[data-sampling-size]");
  const assignmentInput = card.querySelector("[data-sampling-assignment-toggle]");
  const status = card.querySelector("[data-sampling-result]");
  const methodNote = card.querySelector("[data-sampling-method-note]");
  const seedLabel = card.querySelector("[data-sampling-seed]");
  const rows = [...card.querySelectorAll("[data-sampling-id]")];
  const defaults = {
    method: card.dataset.defaultMethod,
    sampleSize: Number(card.dataset.defaultSampleSize),
    seed: Number(card.dataset.defaultSeed),
    randomAssignment: card.dataset.defaultRandomAssignment === "true"
  };
  let seed = defaults.seed;

  function draw() {
    const method = methodInput.value;
    const sampleSize = Number(sizeInput.value);
    const randomAssignment = assignmentInput.checked;
    const result = samplingDemoResult(method, sampleSize, seed, randomAssignment);
    const selectedIds = new Set(result.selected.map((row) => row.id));
    rows.forEach((rowElement) => {
      const studentId = rowElement.dataset.samplingId;
      const selected = selectedIds.has(studentId);
      rowElement.classList.toggle("sampling-selected", selected);
      rowElement.querySelector("[data-sampling-status]").textContent = selected ? "Selected" : "Not selected";
      rowElement.querySelector("[data-sampling-treatment]").textContent = result.assignment[studentId] || "—";
    });
    const methodName = methodInput.selectedOptions[0]?.textContent || "sample";
    status.textContent = `${methodName}: ${sampleSize} of 24 students selected. Sample mean: ${samplingDemoNumber(result.sampleMean)} practice problems; population mean: ${samplingDemoNumber(result.populationMean)}. Seed ${seed} makes this illustration reproducible.`;
    methodNote.textContent = method === "front_row_convenience"
      ? "This convenience sample selects only fictional front-row students. Its values were deliberately set higher in this illustration; a convenience sample can over- or under-represent a population, and this is not a universal pattern."
      : method === "stratified"
        ? `This proportional stratified sample selects ${sampleSize / 2} students from each equally sized section.`
        : "This simple random sample selects students without replacement; every fictional student has the same chance in this seeded draw.";
    if (randomAssignment) {
      const treatmentA = result.selected.filter((row) => result.assignment[row.id] === "Treatment A").map((row) => row.id).join(", ");
      const treatmentB = result.selected.filter((row) => result.assignment[row.id] === "Treatment B").map((row) => row.id).join(", ");
      status.textContent += ` Random assignment put ${treatmentA} in Treatment A and ${treatmentB} in Treatment B. This changes treatment allocation, not who was sampled.`;
    }
    seedLabel.textContent = `Seed ${seed}`;
  }

  methodInput.addEventListener("change", draw);
  sizeInput.addEventListener("change", draw);
  assignmentInput.addEventListener("change", draw);
  card.querySelector("[data-sampling-redraw]").addEventListener("click", () => {
    seed = seed >= 9999 ? 1 : seed + 1;
    draw();
  });
  card.querySelector("[data-sampling-reset]").addEventListener("click", () => {
    methodInput.value = defaults.method;
    sizeInput.value = String(defaults.sampleSize);
    assignmentInput.checked = defaults.randomAssignment;
    seed = defaults.seed;
    draw();
  });
  draw();
}

function mountMolecule(card) {
  const molecule = MOLECULES[card.dataset.moleculeId];
  if (!molecule) return;
  const status = card.querySelector("[data-molecule-status]");
  const atoms = [...card.querySelectorAll("[data-molecule-atom]")];
  function select(atomElement) {
    const atom = molecule.atoms.find((item) => item.id === atomElement.dataset.moleculeAtom);
    if (!atom) return;
    atoms.forEach((item) => item.setAttribute("aria-pressed", String(item === atomElement)));
    status.textContent = `${atom.element}: ${atom.description}`;
  }
  atoms.forEach((atom) => {
    atom.addEventListener("click", () => select(atom));
    atom.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(atom); }
    });
  });
  const fallback = card.querySelector("[data-molecule-fallback]");
  const visual = card.querySelector("[data-smiles-drawer]");
  if (fallback && visual) {
    mountGuideLibrary(card, async () => {
      const { mountMolecule: mountSmilesDrawer } = await import("./libraries/molecules.js");
      return mountSmilesDrawer(visual, card.dataset.moleculeId);
    }, () => {
      visual.hidden = false;
      fallback.hidden = true;
    }, () => { fallback.hidden = false; visual.hidden = true; });
  }
}

function mountProcessPathway(card) {
  const buttons = [...card.querySelectorAll("[data-pathway-stage]")];
  const status = card.querySelector("[data-pathway-status]");
  function select(button, index) {
    buttons.forEach((item) => {
      const active = item === button;
      item.setAttribute("aria-pressed", String(active));
      const stageCard = item.closest?.("[data-pathway-stage-card]");
      stageCard?.toggleAttribute?.("data-active", active);
      const explanation = card.querySelector(`[data-pathway-explanation="${item.dataset.pathwayStage}"]`);
      if (explanation) explanation.hidden = !active;
      if (active && explanation?.id) item.setAttribute("aria-describedby", explanation.id);
      else item.removeAttribute?.("aria-describedby");
    });
    const explanation = card.querySelector(`[data-pathway-explanation="${button.dataset.pathwayStage}"]`);
    status.innerHTML = mathHtml(`Stage ${index + 1} of ${buttons.length}: ${explanation?.dataset.stageText || ""}`);
    void typesetMath(status);
  }
  buttons.forEach((button, index) => button.addEventListener("click", () => select(button, index)));
  const diagram = card.querySelector("[data-mermaid-pathway]");
  if (diagram && buttons.length >= 2) {
    const stages = buttons.map((button) => ({
      id: button.dataset.pathwayStage,
      title: button.dataset.stageTitle || button.querySelector("strong")?.textContent || ""
    }));
    mountGuideLibrary(card, async () => {
      const { mountMermaidFlow } = await import("./libraries/mermaid-flow.js");
      return mountMermaidFlow(diagram, stages);
    }, () => { diagram.hidden = false; }, () => { diagram.hidden = true; });
  }
}

function mountAnimalCellMap(card) {
  const status = card.querySelector("[data-cell-map-status]");
  const controls = [...card.querySelectorAll("[data-cell-feature]")];
  function select(feature) {
    const item = ANIMAL_CELL_ORGANELLES[feature];
    if (!item) return;
    controls.forEach((control) => {
      control.setAttribute("aria-pressed", String(control.dataset.cellFeature === feature));
    });
    status.textContent = `${item.name}: ${item.description}`;
  }
  controls.forEach((control) => {
    control.addEventListener("click", () => select(control.dataset.cellFeature));
    control.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(control.dataset.cellFeature);
      }
    });
  });
}

function mountGraphTraversal(card) {
  const block = activityPayload(card);
  const host = card.querySelector("[data-graph-traversal-mount]");
  if (!block || !host) return;
  mountGuideLibrary(card, async () => {
    const [{ mountCytoscapeTraversal }, cytoscapeModule] = await Promise.all([
      import("./libraries/cytoscape-traversal.js"),
      import("cytoscape")
    ]);
    const cytoscape = cytoscapeModule.default || cytoscapeModule;
    return mountCytoscapeTraversal(host, block, cytoscape);
  });
}

function mountStressCube3d(card) {
  const block = activityPayload(card);
  const host = card.querySelector("[data-stress-cube-mount]");
  if (!block || !host) return;
  mountGuideLibrary(card, async () => {
    const { mountStressCube3D } = await import("./libraries/stress-cube-3d.js");
    let three = null;
    try {
      three = await import("three");
    } catch {
      // The app-authored SVG fallback remains usable without WebGL or the package chunk.
    }
    return mountStressCube3D(host, block.parameters, three);
  });
}

function mountMolecule3d(card) {
  const block = activityPayload(card);
  const host = card.querySelector("[data-molecule-3d-mount]");
  if (!block || !host) return;
  mountGuideLibrary(card, async () => {
    const { mountMolecule3D } = await import("./libraries/molecule-3d.js");
    let threeMol = null;
    try {
      const module = await import("3dmol");
      threeMol = module.default || module;
    } catch {
      // The viewer mounts its fixed SVG and text fallback when 3Dmol is unavailable.
    }
    return mountMolecule3D(host, block.molecule_id, threeMol);
  });
}

function mountPythonAlgorithm(card) {
  const block = activityPayload(card);
  const host = card.querySelector("[data-python-activity-mount]");
  if (!block || !host) return;
  mountGuideLibrary(card, async () => {
    const [{ mountPythonActivity }, state, view, language, highlight, pythonLanguage] = await Promise.all([
      import("./libraries/python-activity.js"),
      import("@codemirror/state"),
      import("@codemirror/view"),
      import("@codemirror/language"),
      import("@lezer/highlight"),
      import("@codemirror/lang-python")
    ]);
    const codeMirrorLoader = async () => ({
      EditorState: state.EditorState,
      EditorView: view.EditorView,
      HighlightStyle: language.HighlightStyle,
      syntaxHighlighting: language.syntaxHighlighting,
      tags: highlight.tags,
      python: pythonLanguage.python
    });
    return mountPythonActivity(host, block, { codeMirrorLoader });
  });
}

function mountSimcirCircuit(card) {
  const block = activityPayload(card);
  const host = card.querySelector("[data-simcir-circuit-mount]");
  if (!block || !host) return;
  mountGuideLibrary(card, async () => {
    const { mountSimcirCircuit: mountCircuit } = await import("./libraries/simcir-circuits.js");
    return mountCircuit(host, block.parameters.circuit_id);
  });
}

function mountExampleSteps(card) {
  const steps = [...card.querySelectorAll("[data-example-step]")];
  const next = card.querySelector("[data-example-next]");
  const status = card.querySelector("[data-example-status]");
  let shown = 1;
  function draw() {
    steps.forEach((step, index) => { step.hidden = index >= shown; });
    status.textContent = shown >= steps.length ? `All ${steps.length} steps are shown.` : `Step ${shown} of ${steps.length} is shown.`;
    next.disabled = shown >= steps.length;
  }
  next.addEventListener("click", () => { shown = Math.min(steps.length, shown + 1); draw(); });
  card.querySelector("[data-example-reset]").addEventListener("click", () => { shown = 1; draw(); });
}

export function mountAdditionalGuideBlocks(root) {
  root.querySelectorAll("[data-guide-activity]").forEach((card) => {
    const type = card.dataset.guideActivity;
    if (type === "linear_graph_v1") mountLinearGraph(card);
    else if (type === "first_order_response_v1") mountResponse(card);
    else if (type === "resistor_divider_v1") mountDivider(card);
    else if (type === "binary_search_trace_v1") mountBinarySearch(card);
    else if (type === "molecule_2d_v1") mountMolecule(card);
    else if (type === "animal_cell_map_v1") mountAnimalCellMap(card);
    else if (type === "sampling_demo_v1") mountSamplingDemo(card);
    else if (type === "process_pathway_v1") mountProcessPathway(card);
    else if (type === "graph_traversal_v1") mountGraphTraversal(card);
    else if (type === "stress_cube_3d_v1") mountStressCube3d(card);
    else if (type === "molecule_3d_v1") mountMolecule3d(card);
    else if (type === "python_algorithm_trace_v1") mountPythonAlgorithm(card);
    else if (type === "simcir_circuit_v1") mountSimcirCircuit(card);
    else if (type === "example_steps") mountExampleSteps(card);
    else if (type === "svg_diagram") mountAuthoredSvg(card);
  });
}

function mountAuthoredSvg(card) {
  const svg = card.querySelector("[data-authored-svg] svg");
  const controls = card.querySelector(".authored-svg-controls");
  if (!svg || !controls) return;

  const zoomStatus = controls.querySelector("[data-svg-zoom-status]");
  const zoomOut = controls.querySelector("[data-svg-zoom-out]");
  const zoomIn = controls.querySelector("[data-svg-zoom-in]");
  let zoom = 1;
  const setZoom = (value) => {
    zoom = Math.max(1, Math.min(3, Math.round(value * 2) / 2));
    svg.style.width = zoom > 1 ? `${zoom * 100}%` : "";
    svg.style.maxWidth = zoom > 1 ? "none" : "";
    svg.style.maxHeight = zoom > 1 ? "none" : "";
    if (zoomStatus) zoomStatus.textContent = `${Math.round(zoom * 100)}%`;
    if (zoomOut) zoomOut.disabled = zoom <= 1;
    if (zoomIn) zoomIn.disabled = zoom >= 3;
  };
  zoomOut?.addEventListener("click", () => setZoom(zoom - 0.5));
  zoomIn?.addEventListener("click", () => setZoom(zoom + 0.5));
  controls.querySelector("[data-svg-zoom-reset]")?.addEventListener("click", () => setZoom(1));
  setZoom(1);

  const animatedSvg = typeof svg.pauseAnimations === "function";
  const animationButtons = [
    controls.querySelector("[data-svg-play]"),
    controls.querySelector("[data-svg-pause]"),
    controls.querySelector("[data-svg-restart]")
  ];
  if (!animatedSvg) {
    animationButtons.forEach((button) => button?.remove());
  } else {
    const pause = () => svg.pauseAnimations();
    const play = () => svg.unpauseAnimations();
    const restart = () => {
      svg.setCurrentTime(0);
      svg.unpauseAnimations();
    };
    // Begin from a legible still frame so students can inspect the diagram before
    // deciding to play the process. This also makes repeated visits predictable.
    pause();
    svg.setCurrentTime(0);
    animationButtons[0]?.addEventListener("click", play);
    animationButtons[1]?.addEventListener("click", pause);
    animationButtons[2]?.addEventListener("click", restart);
    if (globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) pause();
  }
}
