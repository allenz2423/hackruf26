import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { normalizeGuideBlocks } from "../src/study-guide/normalize.js";
import { mountAdditionalGuideBlocks } from "../src/study-guide/activity-blocks.js";
import { mountStudyGuideBlocks, renderStudyGuideBlock } from "../src/study-guide/force-components.js";
import { renderStudyGuideText } from "../src/study-guide/text-renderer.js";
import { formatRemoteMaterial } from "../src/study.js";
import { openMaterialViewer, parseGuideSections } from "../src/material-viewer.js";

const section = { heading: "Guide", body: "Keep this readable fallback.", sources: [] };

test("legacy guide sections normalize into paragraph blocks", () => {
  assert.deepEqual(normalizeGuideBlocks({ ...section }, 1), [{
    id: "section-2-legacy",
    type: "paragraph",
    text: section.body,
    sources: []
  }]);
});

test("failed remote guides keep their last preview available to the viewer", () => {
  const material = formatRemoteMaterial({
    id: "guide-1",
    kind: "study_guide",
    title: "Study Guide: asa",
    status: "failed",
    preview_revision: 16,
    preview_content: {
      format: "study_guide",
      sections: [{ heading: "Stress tensor", body: "Provisional section text." }]
    },
    download_urls: {}
  }, "class-1", "Study guide", []);

  assert.equal(material.status, "failed");
  assert.equal(material.previewRevision, 16);
  assert.deepEqual(parseGuideSections(material).map((item) => item.heading), ["Stress tensor"]);
  assert.deepEqual(material.downloadUrls, {});
});

test("failed guide viewer labels provisional content and offers no exports", () => {
  class FakeElement {
    constructor() { this.textContent = ""; this.innerHTML = ""; this.scrollTop = 0; }
    addEventListener() {}
    querySelectorAll() { return []; }
    querySelector() { return null; }
  }
  class FakeDialog extends FakeElement {
    constructor() {
      super();
      this.open = false;
      this.root = new FakeElement();
      this.title = new FakeElement();
      this.meta = new FakeElement();
      this.closeButton = new FakeElement();
    }
    setAttribute() {}
    showModal() { this.open = true; }
    remove() {}
    querySelector(selector) {
      return {
        "#material-session": this.root,
        "#material-dialog-title": this.title,
        "#material-dialog-meta": this.meta,
        "[data-close]": this.closeButton
      }[selector] || null;
    }
  }

  const priorDocument = globalThis.document;
  globalThis.document = { createElement: () => new FakeDialog(), body: { append() {} } };
  try {
    const material = {
      id: "guide-1", title: "BIO_170_Full-Course_Study_Guide", scope: "BIO_170_Comprehensive_Master_Study_Guide.pdf",
      type: "Study guide", status: "failed",
      isRemote: true, previewContent: {
        sections: [{ heading: "Stress tensor", body: "Provisional section text." }]
      }
    };
    const viewer = openMaterialViewer(material);
    assert.equal(viewer.isOpen, true);
    assert.match(viewer.dialog.className, /material-dialog-resizable/);
    assert.equal(viewer.dialog.title.textContent, "BIO_170_Full-Course_Study_Guide");
    assert.equal(viewer.dialog.title.innerHTML, "");
    assert.equal(
      viewer.dialog.meta.textContent,
      "BIO_170_Comprehensive_Master_Study_Guide.pdf - 0 items - Generation failed"
    );
    assert.match(viewer.dialog.root.innerHTML, /Generation stopped before the guide was saved/);
    assert.match(viewer.dialog.root.innerHTML, /partial preview is not saved or export-checked/);
    assert.match(viewer.dialog.root.innerHTML, /Provisional section text/);
    assert.doesNotMatch(viewer.dialog.root.innerHTML, /data-download-guide/);

    const emptyViewer = openMaterialViewer({ ...material, previewContent: null });
    assert.match(emptyViewer.dialog.className, /material-dialog-resizable/);
    assert.match(emptyViewer.dialog.root.innerHTML, /No preview was saved before generation stopped/);
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("a source filename is human-readable in material metadata while citation names stay exact", () => {
  const originalName = "BIO_170_Comprehensive_Master_Study_Guide.pdf";
  const material = formatRemoteMaterial({
    id: "guide-1",
    kind: "study_guide",
    status: "ready",
    scope: { kind: "uploads", upload_ids: ["upload-1"] },
    content: { format: "study_guide", schema_version: 2, sections: [] }
  }, "class-1", "Study guide", [{ id: "upload-1", isRemote: true, name: originalName }]);

  assert.equal(material.scope, "BIO 170 Comprehensive Master Study Guide.pdf");
  assert.equal(material.sources[0], originalName);
});

test("long material metadata filenames wrap inside narrow viewer headers", async () => {
  const css = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
  const rule = css.match(/\.preview-header \.row-meta\s*\{([^}]*)\}/);
  assert.ok(rule, "material viewer metadata must have a dedicated wrapping rule");
  assert.match(rule[1], /overflow-wrap:\s*anywhere\s*;/);
});

test("malformed and oversized structured sections fall back to their body", () => {
  for (const blocks of [
    [{ type: "paragraph", text: "valid" }, { type: "unknown", text: "bad" }],
    Array.from({ length: 25 }, () => ({ type: "paragraph", text: "too many" })),
    [{ type: "interactive_model", template_id: "force_components_v1", parameters: { force_n: 1001, angle_deg: 45 } }],
    [{ type: "labeled_diagram", template_id: "animal_cell_map_v1", parameters: { focus_ids: ["nucleus", "unknown"] } }],
    [{ type: "labeled_diagram", template_id: "animal_cell_map_v1", parameters: { focus_ids: ["nucleus", "nucleus"] } }]
  ]) {
    assert.deepEqual(normalizeGuideBlocks({ ...section, blocks }), [{
      id: "section-1-fallback",
      type: "paragraph",
      text: section.body,
      sources: []
    }]);
  }
});

test("study guide prose renders inline code and fenced code as escaped IDE-style blocks", () => {
  const html = renderStudyGuideBlock({
    type: "paragraph",
    text: "Use `iter_seq(i)` to read values.\n\n```python\nfor value in values:\n    print(value)\n```\n\nTildes work too:\n~~~js\nconst value = 1;\n~~~"
  });
  assert.match(html, /<code class="guide-inline-code">iter_seq\(i\)<\/code>/);
  assert.match(html, /class="guide-code-window" role="group" aria-label="python code example"/);
  assert.match(html, /example\.python/);
  assert.match(html, /class="guide-code-line-number" aria-hidden="true">2/);
  assert.match(html, /class="guide-code-window" role="group" aria-label="js code example"/);
  assert.match(html, /class="guide-code-line-text">const value = 1;/);
});

test("labeled Markdown math fences still render as math blocks", () => {
  const html = renderStudyGuideBlock({ type: "paragraph", text: "```math\nx^2 + y^2 = 1\n```" });
  assert.match(html, /class="guide-fenced-math"/);
  assert.match(html, /class="tex-math is-display"/);
  assert.doesNotMatch(html, /guide-code-window/);
});

test("study guide Markdown creates readable structure without interpreting code as formatting", () => {
  const html = renderStudyGuideText([
    "### Core idea",
    "",
    "Use **bold emphasis** and keep this wrapped",
    "onto a natural next line. The expression **$H_2O$** stays emphasized.",
    "",
    "1. Compare the cases",
    "2. Identify `mask = a ** b` literally",
    "",
    "- First comparison",
    "- Second comparison",
    "",
    "Do not render <img src=x onerror=alert(1)> as HTML."
  ].join("\n"));

  assert.match(html, /<h4 class="guide-inline-heading">Core idea<\/h4>/);
  assert.match(html, /<strong>bold emphasis<\/strong>/);
  assert.match(html, /<strong><span class="tex-math/);
  assert.match(html, /<ol><li>Compare the cases<\/li><li>Identify <code class="guide-inline-code">mask = a \*\* b<\/code> literally<\/li><\/ol>/);
  assert.match(html, /<ul><li>First comparison<\/li><li>Second comparison<\/li><\/ul>/);
  assert.match(html, /wrapped onto a natural next line/);
  assert.doesNotMatch(html, /<br>/);
  assert.doesNotMatch(html, /<img src=/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("an unterminated code fence remains visible instead of hiding the rest of a guide block", () => {
  const html = renderStudyGuideText("```js\nconst result = 1;\n// final line");
  assert.match(html, /class="guide-code-window"/);
  assert.match(html, /const result = 1;/);
  assert.match(html, /\/\/ final line/);
});

test("long study guides render a section contents navigator with matching targets", () => {
  class FakeElement {
    constructor() { this.textContent = ""; this.innerHTML = ""; this.scrollTop = 0; }
    addEventListener() {}
    querySelectorAll() { return []; }
    querySelector() { return null; }
  }
  class FakeDialog extends FakeElement {
    constructor() {
      super();
      this.open = false;
      this.root = new FakeElement();
      this.title = new FakeElement();
      this.meta = new FakeElement();
      this.closeButton = new FakeElement();
    }
    setAttribute() {}
    showModal() { this.open = true; }
    remove() {}
    querySelector(selector) {
      return {
        "#material-session": this.root,
        "#material-dialog-title": this.title,
        "#material-dialog-meta": this.meta,
        "[data-close]": this.closeButton
      }[selector] || null;
    }
  }

  const priorDocument = globalThis.document;
  globalThis.document = {
    createElement: () => new FakeDialog(),
    body: { append() {} },
    addEventListener() {},
    removeEventListener() {}
  };
  try {
    const sections = Array.from({ length: 6 }, (_, index) => ({
      heading: `Section ${index + 1}`,
      body: `Content ${index + 1}.`,
      sources: []
    }));
    const viewer = openMaterialViewer({
      id: "guide:bio/1",
      title: "BIO Guide",
      scope: "BIO 170",
      type: "Study guide",
      status: "ready",
      sections,
      isRemote: false
    });
    assert.match(viewer.dialog.root.innerHTML, /<nav class="guide-toc"/);
    assert.match(viewer.dialog.root.innerHTML, /aria-label="Study guide sections"/);
    for (let index = 1; index <= sections.length; index += 1) {
      const target = `guide-guide-bio-1-section-${index}`;
      assert.match(viewer.dialog.root.innerHTML, new RegExp(`href="#${target}"`));
      assert.match(viewer.dialog.root.innerHTML, new RegExp(`id="${target}"`));
    }
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("custom SVG animations start at time zero and wait for the learner to play", () => {
  const events = [];
  const svg = {
    style: {},
    pauseAnimations() { events.push("pause"); },
    unpauseAnimations() { events.push("play"); },
    setCurrentTime(time) { events.push(`time:${time}`); }
  };
  const makeButton = () => ({
    disabled: false,
    listeners: {},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    click() { this.listeners.click?.(); }
  });
  const buttons = {
    "[data-svg-play]": makeButton(),
    "[data-svg-pause]": makeButton(),
    "[data-svg-restart]": makeButton(),
    "[data-svg-zoom-out]": makeButton(),
    "[data-svg-zoom-in]": makeButton(),
    "[data-svg-zoom-reset]": makeButton(),
    "[data-svg-zoom-status]": { textContent: "" }
  };
  const controls = { querySelector: (selector) => buttons[selector] || null };
  const card = {
    dataset: { guideActivity: "svg_diagram" },
    querySelector(selector) {
      if (selector === "[data-authored-svg] svg") return svg;
      if (selector === ".authored-svg-controls") return controls;
      return null;
    }
  };
  mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
  assert.deepEqual(events, ["pause", "time:0"]);
  buttons["[data-svg-play]"].click();
  buttons["[data-svg-pause]"].click();
  buttons["[data-svg-restart]"].click();
  assert.deepEqual(events, ["pause", "time:0", "play", "pause", "time:0", "play"]);
});

test("all fixed visual templates normalize and render without generated markup", () => {
  const blocks = [
    { type: "paragraph", text: "A paragraph <img src=x onerror=alert(1)>" },
    { type: "formula", expression: "F=ma", definitions: [{ symbol: "F", meaning: "force", unit: "N\" onmouseover=alert(1)" }] },
    { type: "takeaway", text: "Remember <script>alert(1)</script>" },
    {
      type: "example_steps", title: "Example <svg onload=alert(1)>", steps: [
        { title: "First", explanation: "Read <b>carefully</b>.", expression: "x=1" },
        { title: "Second", explanation: "Finish safely." }
      ]
    },
    {
      type: "labeled_diagram", template_id: "process_pathway_v1", title: "Path",
      parameters: { stages: [
        { id: "start", title: "Start", explanation: "First stage" },
        { id: "finish", title: "Finish", explanation: "Last stage" }
      ] }
    },
    {
      type: "labeled_diagram", template_id: "animal_cell_map_v1", title: "Cell features",
      parameters: { focus_ids: ["nucleus", "mitochondria", "ribosomes"] }
    },
    ...["water", "carbon_dioxide", "methane", "ammonia"].map((molecule_id) => ({
      type: "labeled_diagram", template_id: "molecule_2d_v1", parameters: { molecule_id }
    })),
    {
      type: "labeled_diagram", template_id: "molecule_3d_v1", title: "Molecular geometry",
      parameters: { molecule_id: "water" }, alt_text: "A bent water molecule in 3D."
    },
    {
      type: "interactive_model", template_id: "graph_traversal_v1", title: "Signal path",
      alt_text: "A directed graph of source-supported relationships.",
      fallback_text: "A reaches B.",
      parameters: {
        algorithm: "bfs", start_node: "a",
        nodes: [{ id: "a", label: "A", description: "Start." }, { id: "b", label: "B" }],
        edges: [{ source: "a", target: "b" }]
      }
    },
    {
      type: "interactive_model", template_id: "stress_cube_3d_v1", title: "Maximum shear plane",
      alt_text: "A symmetric stress tensor and its absolute maximum-shear plane.",
      fallback_text: "Principal stresses and the plane at 45 degrees.",
      parameters: { stress_mpa: [[30, -40, 20], [-40, -10, 80], [20, 80, 100]] }
    },
    { type: "interactive_model", template_id: "force_components_v1", parameters: { force_n: 100, angle_deg: 30 } },
    { type: "interactive_model", template_id: "linear_graph_v1", parameters: { slope: -2, intercept: 3 } },
    { type: "interactive_model", template_id: "first_order_response_v1", parameters: { initial_value: 0, final_value: 10, time_constant_s: 2 } },
    { type: "interactive_model", template_id: "resistor_divider_v1", parameters: { voltage_v: 12, r1_ohm: 1000, r2_ohm: 2000 } },
    { type: "interactive_model", template_id: "binary_search_trace_v1", parameters: { values: [1, 4, 9, 16, 25], target: 16 } },
    { type: "interactive_model", template_id: "python_algorithm_trace_v1", title: "Walk through binary search", parameters: { algorithm: "binary_search", values: [1, 3, 5, 7, 9], target: 7 } },
    { type: "interactive_model", template_id: "simcir_circuit_v1", title: "Half adder logic", parameters: { circuit_id: "half_adder" } },
    { type: "interactive_model", template_id: "sampling_demo_v1", parameters: { method: "stratified", sample_size: 6, seed: 17, random_assignment: false } }
  ];
  const normalized = normalizeGuideBlocks({ ...section, blocks });

  assert.equal(normalized.length, blocks.length);
  assert.ok(normalized.every(Boolean));
  for (const block of normalized) {
    const html = renderStudyGuideBlock(block);
    assert.equal(typeof html, "string");
    assert.ok(html.length > 0);
    assert.doesNotMatch(html, /<script\b|<img\b|<svg\s+onload=|javascript:/i);
  }
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "binary_search_trace_v1")), /data-values="\[1,4,9,16,25\]"/);
  const cellMap = normalized.find((block) => block.template_id === "animal_cell_map_v1");
  const cellHtml = renderStudyGuideBlock(cellMap);
  assert.match(cellHtml, /role="group" aria-labelledby=/);
  assert.match(cellHtml, /data-cell-feature="nucleus" role="button" tabindex="0"/);
  assert.match(cellHtml, /Nucleus and nuclear envelope/);
  assert.match(cellHtml, /not to scale/);
  assert.doesNotMatch(cellHtml, /model-authored function|<script\b|<img\b/i);
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "graph_traversal_v1")), /data-guide-activity="graph_traversal_v1"/);
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "stress_cube_3d_v1")), /data-guide-activity="stress_cube_3d_v1"/);
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "molecule_3d_v1")), /data-guide-activity="molecule_3d_v1"/);
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "simcir_circuit_v1")), /data-guide-activity="simcir_circuit_v1"/);
  assert.match(renderStudyGuideBlock(normalized.find((block) => block.template_id === "python_algorithm_trace_v1")), /data-guide-activity="python_algorithm_trace_v1"/);
});

test("new graph, stress, circuit, and molecule templates reject unsafe or malformed data", () => {
  const invalid = [
    { type: "interactive_model", template_id: "graph_traversal_v1", parameters: {
      algorithm: "bfs", start_node: "a", nodes: [{ id: "a", label: "A" }, { id: "b", label: "B" }],
      edges: [{ source: "a", target: "missing" }]
    } },
    { type: "interactive_model", template_id: "graph_traversal_v1", parameters: {
      algorithm: "bfs", start_node: "a", nodes: [{ id: "a", label: "A" }, { id: "a", label: "Duplicate" }],
      edges: [{ source: "a", target: "a" }]
    } },
    { type: "interactive_model", template_id: "stress_cube_3d_v1", parameters: {
      stress_mpa: [[30, -40, 20], [-39, -10, 80], [20, 80, 100]]
    } },
    { type: "interactive_model", template_id: "stress_cube_3d_v1", parameters: {
      stress_mpa: [[30, -40, 20], [-40, -10, 80], [20, 80, "100"]]
    } },
    { type: "labeled_diagram", template_id: "molecule_3d_v1", parameters: { molecule_id: "not-reviewed" } },
    { type: "interactive_model", template_id: "simcir_circuit_v1", parameters: { circuit_id: "arbitrary_user_circuit" } },
    { type: "interactive_model", template_id: "simcir_circuit_v1", parameters: { circuit_id: "half_adder", devices: [] } }
  ];
  for (const block of invalid) {
    assert.deepEqual(normalizeGuideBlocks({ ...section, blocks: [block] }), [{
      id: "section-1-fallback", type: "paragraph", text: section.body, sources: []
    }]);
  }
});

test("animal-cell map ignores model-supplied descriptions and escapes its plain-text title", () => {
  const [block] = normalizeGuideBlocks({
    ...section,
    blocks: [{
      type: "labeled_diagram", template_id: "animal_cell_map_v1",
      title: "<svg onload=alert(1)>", alt_text: "Forged label", fallback_text: "Forged claim",
      parameters: { focus_ids: ["endoplasmic_reticulum", "golgi_apparatus"] }
    }]
  });
  const html = renderStudyGuideBlock(block);
  assert.match(html, /&lt;svg onload=alert\(1\)&gt;/);
  assert.match(html, /The ER helps process molecules created by the cell/);
  assert.doesNotMatch(html, /Forged label|Forged claim|<svg onload=/i);
});

test("unsupported fixed templates display their bounded fallback instead of executing content", () => {
  const [block] = normalizeGuideBlocks({
    ...section,
    blocks: [{
      type: "interactive_model", id: "future-model", template_id: "future_v9",
      fallback_text: "A safe fallback <img src=x onerror=alert(1)>"
    }]
  });
  assert.equal(block.type, "unavailable");
  const html = renderStudyGuideBlock(block);
  assert.match(html, /Interactive visual unavailable/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img\b/i);
});

test("malicious labels and descriptions are escaped in HTML and SVG contexts", () => {
  const [block] = normalizeGuideBlocks({
    ...section,
    blocks: [{
      type: "interactive_model", id: "linear", template_id: "linear_graph_v1",
      title: "<svg onload=alert(1)>", alt_text: "<img src=x onerror=alert(1)>",
      fallback_text: "<script>alert(1)</script>", parameters: { slope: 1, intercept: 0 }
    }]
  });
  const html = renderStudyGuideBlock(block);
  assert.match(html, /&lt;svg onload=alert\(1\)&gt;/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<script\b|<img\b|<svg\s+onload=/i);
});

test("authored SVG control template exposes accessible zoom controls", async () => {
  const source = await readFile(new URL("../src/study-guide/activity-blocks.js", import.meta.url), "utf8");
  assert.match(source, /class="authored-svg-frame"[^>]*data-authored-svg/);
  assert.match(source, /aria-label="Diagram and animation controls"/);
  assert.match(source, /data-svg-zoom-out aria-label="Zoom out of diagram"/);
  assert.match(source, /data-svg-zoom-status aria-live="polite"/);
  assert.match(source, /data-svg-zoom-in aria-label="Zoom in on diagram"/);
  assert.match(source, /data-svg-zoom-reset/);
});

class FakeElement {
  constructor({ dataset = {}, type = "", value = "", min = "", max = "", textContent = "" } = {}) {
    this.dataset = dataset;
    this.type = type;
    this.value = String(value);
    this.min = String(min);
    this.max = String(max);
    this._innerHTML = "";
    this._textContent = "";
    this.textContent = textContent;
    this.hidden = false;
    this.disabled = false;
    this.handlers = new Map();
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this._textContent = this._innerHTML.replace(/<[^>]*>/g, "");
  }
  get textContent() { return this._textContent; }
  set textContent(value) {
    this._textContent = String(value);
    this._innerHTML = this._textContent;
  }
  addEventListener(type, handler) {
    const handlers = this.handlers.get(type) || [];
    handlers.push(handler);
    this.handlers.set(type, handlers);
  }
  setAttribute(name, value) { this[name] = String(value); }
  dispatch(type, extra = {}) {
    globalThis.document.activeElement = this;
    for (const handler of this.handlers.get(type) || []) handler({ preventDefault() {}, ...extra });
  }
}

function activityCard(dataset, controls, selectors) {
  return {
    dataset,
    querySelectorAll(selector) {
      return selector === "[data-activity-control]" ? controls : [];
    },
    querySelector(selector) { return selectors.get(selector) || null; }
  };
}

test("paired activity inputs stay synchronized, clamp values, and reset to authored defaults", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const slopeRange = new FakeElement({ dataset: { activityControl: "slope" }, type: "range", value: 1, min: -10, max: 10 });
    const slopeNumber = new FakeElement({ dataset: { activityControl: "slope" }, type: "number", value: 1, min: -10, max: 10 });
    const interceptRange = new FakeElement({ dataset: { activityControl: "intercept" }, type: "range", value: 0, min: -10, max: 10 });
    const interceptNumber = new FakeElement({ dataset: { activityControl: "intercept" }, type: "number", value: 0, min: -10, max: 10 });
    const slopeReadout = new FakeElement({ textContent: "1" });
    const interceptReadout = new FakeElement({ textContent: "0" });
    const visual = new FakeElement();
    const result = new FakeElement();
    const reset = new FakeElement();
    const controls = [slopeRange, slopeNumber, interceptRange, interceptNumber];
    const card = activityCard({
      activityId: "linear-graph-test", defaultSlope: "1", defaultIntercept: "0"
    }, controls, new Map([
      ['[data-activity-readout="slope"]', slopeReadout],
      ['[data-activity-readout="intercept"]', interceptReadout],
      ["[data-activity-visual]", visual],
      ["[data-activity-result]", result],
      ["[data-activity-reset]", reset]
    ]));

    mountAdditionalGuideBlocks({ querySelectorAll: () => [Object.assign(card, { dataset: { ...card.dataset, guideActivity: "linear_graph_v1" } })] });
    slopeNumber.value = "2.5";
    slopeNumber.dispatch("input");
    assert.equal(slopeRange.value, "2.5");
    assert.equal(slopeReadout.textContent, "2.5");
    assert.match(result.innerHTML, /y=2\.5x\+0/);
    assert.match(visual.innerHTML, /<polyline/);

    interceptNumber.value = "-12";
    interceptNumber.dispatch("change");
    assert.equal(interceptNumber.value, "-10");
    assert.equal(interceptRange.value, "-10");

    reset.dispatch("click");
    assert.deepEqual(controls.map((input) => input.value), ["1", "1", "0", "0"]);
    assert.equal(slopeReadout.textContent, "1");
    assert.equal(interceptReadout.textContent, "0");
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("custom SVG zoom controls scale and reset the drawing inside its scroll frame", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const svg = new FakeElement();
    svg.style = {};
    const status = new FakeElement({ textContent: "100%" });
    const zoomOut = new FakeElement();
    const zoomIn = new FakeElement();
    const reset = new FakeElement();
    const controlMap = new Map([
      ["[data-svg-zoom-status]", status],
      ["[data-svg-zoom-out]", zoomOut],
      ["[data-svg-zoom-in]", zoomIn],
      ["[data-svg-zoom-reset]", reset],
      ["[data-svg-play]", null],
      ["[data-svg-pause]", null],
      ["[data-svg-restart]", null]
    ]);
    const controls = new FakeElement();
    controls.querySelector = (selector) => controlMap.get(selector) || null;
    const card = {
      dataset: { guideActivity: "svg_diagram" },
      querySelector(selector) {
        if (selector === "[data-authored-svg] svg") return svg;
        if (selector === ".authored-svg-controls") return controls;
        return null;
      }
    };

    mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
    assert.equal(zoomOut.disabled, true);
    zoomIn.dispatch("click");
    assert.equal(svg.style.width, "150%");
    assert.equal(svg.style.maxHeight, "none");
    assert.equal(status.textContent, "150%");
    assert.equal(zoomOut.disabled, false);

    zoomIn.dispatch("click");
    zoomIn.dispatch("click");
    zoomIn.dispatch("click");
    assert.equal(svg.style.width, "300%");
    assert.equal(status.textContent, "300%");
    assert.equal(zoomIn.disabled, true);

    zoomOut.dispatch("click");
    assert.equal(status.textContent, "250%");
    reset.dispatch("click");
    assert.equal(svg.style.width, "");
    assert.equal(svg.style.maxWidth, "");
    assert.equal(svg.style.maxHeight, "");
    assert.equal(status.textContent, "100%");
    assert.equal(zoomOut.disabled, true);
    assert.equal(zoomIn.disabled, false);
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("animal-cell features respond to pointer and keyboard selection", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const nucleus = new FakeElement({ dataset: { cellFeature: "nucleus" } });
    const mitochondriaMarker = new FakeElement({ dataset: { cellFeature: "mitochondria" } });
    const mitochondriaButton = new FakeElement({ dataset: { cellFeature: "mitochondria" } });
    const status = new FakeElement();
    const card = {
      dataset: { guideActivity: "animal_cell_map_v1" },
      querySelectorAll: (selector) => selector === "[data-cell-feature]"
        ? [nucleus, mitochondriaMarker, mitochondriaButton] : [],
      querySelector: (selector) => selector === "[data-cell-map-status]" ? status : null
    };

    mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
    mitochondriaButton.dispatch("click");
    assert.equal(status.textContent, "Mitochondria: Mitochondria convert energy from food into a form the cell can use. They have their own genetic material, separate from DNA in the nucleus, and can make copies of themselves.");
    assert.equal(nucleus["aria-pressed"], "false");
    assert.equal(mitochondriaMarker["aria-pressed"], "true");
    assert.equal(mitochondriaButton["aria-pressed"], "true");

    nucleus.dispatch("keydown", { key: "Enter" });
    assert.match(status.textContent, /^Nucleus and nuclear envelope:/);
    assert.equal(nucleus["aria-pressed"], "true");
    assert.equal(mitochondriaButton["aria-pressed"], "false");
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("response time control updates the computed value and reset restores time zero", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const inputs = [
      ...[["initial_value", 0], ["final_value", 10], ["time_constant_s", 2]].flatMap(([key, value]) => [
        new FakeElement({ dataset: { activityControl: key }, type: "range", value, min: key === "time_constant_s" ? 0.01 : -1000, max: key === "time_constant_s" ? 3600 : 1000 }),
        new FakeElement({ dataset: { activityControl: key }, type: "number", value, min: key === "time_constant_s" ? 0.01 : -1000, max: key === "time_constant_s" ? 3600 : 1000 })
      ]),
      new FakeElement({ dataset: { activityControl: "time" }, type: "range", value: 0, min: 0, max: 10 })
    ];
    const timeInput = inputs.at(-1);
    const timeReadout = new FakeElement({ textContent: "0 s" });
    const result = new FakeElement();
    const visual = new FakeElement();
    const reset = new FakeElement();
    const card = activityCard({
      activityId: "response-test", defaultInitial: "0", defaultFinal: "10", defaultTau: "2"
    }, inputs, new Map([
      ['[data-activity-control="time"]', timeInput],
      ['[data-activity-readout="time"]', timeReadout],
      ["[data-activity-visual]", visual],
      ["[data-activity-result]", result],
      ["[data-activity-reset]", reset]
    ]));
    card.dataset.guideActivity = "first_order_response_v1";
    mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });

    timeInput.value = "2";
    timeInput.dispatch("input");
    assert.match(result.textContent, /y = 6\.32/);
    assert.equal(timeReadout.textContent, "2 s");
    assert.match(visual.innerHTML, /activity-current-point/);

    reset.dispatch("click");
    assert.equal(timeInput.value, "0");
    assert.equal(timeReadout.textContent, "0 s");
    assert.match(result.textContent, /y = 0/);
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("force model updates its vector components and prediction feedback locally", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const forceRange = new FakeElement({ dataset: { forceValue: "force" }, type: "range", value: 100, min: 0, max: 1000 });
    const forceNumber = new FakeElement({ dataset: { forceValue: "force" }, type: "number", value: 100, min: 0, max: 1000 });
    const angleRange = new FakeElement({ dataset: { forceValue: "angle" }, type: "range", value: 30, min: 0, max: 90 });
    const angleNumber = new FakeElement({ dataset: { forceValue: "angle" }, type: "number", value: 30, min: 0, max: 90 });
    const values = new FakeElement();
    const diagram = new FakeElement();
    const graph = new FakeElement();
    const prediction = new FakeElement({ value: "" });
    const reveal = new FakeElement();
    const feedback = new FakeElement();
    const reset = new FakeElement();
    const card = {
      dataset: { forceModel: "force-test", defaultForce: "100", defaultAngle: "30" },
      querySelectorAll(selector) {
        if (selector === '[data-force-value="force"]') return [forceRange, forceNumber];
        if (selector === '[data-force-value="angle"]') return [angleRange, angleNumber];
        return [];
      },
      querySelector(selector) {
        return new Map([
          ["[data-force-values]", values], ["[data-force-diagram]", diagram],
          ["[data-force-graph]", graph], ["[data-force-prediction]", prediction],
          ["[data-force-reveal]", reveal], ["[data-force-feedback]", feedback],
          ["[data-force-reset]", reset]
        ]).get(selector) || null;
      }
    };
    const root = { querySelectorAll: (selector) => selector === "[data-force-model]" ? [card] : [] };
    mountStudyGuideBlocks(root);

    angleNumber.value = "60";
    angleNumber.dispatch("input");
    assert.equal(angleRange.value, "60");
    assert.match(values.textContent, /Fₓ = 50 N; Fᵧ = 86\.6 N/);
    assert.match(graph.innerHTML, /current angle 60 degrees/);

    prediction.value = "x-down-y-up";
    reveal.dispatch("click");
    assert.equal(feedback.hidden, false);
    assert.match(feedback.textContent, /That matches the model/);

    reset.dispatch("click");
    assert.equal(angleNumber.value, "30");
    assert.equal(prediction.value, "");
    assert.equal(feedback.hidden, true);
    assert.equal(feedback.textContent, "");
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("interactive layouts collapse to one column on narrow screens", async () => {
  const css = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
  const layoutRule = css.indexOf(".activity-layout {\n    grid-template-columns: minmax(0, 1fr);");
  assert.notEqual(layoutRule, -1);
  assert.notEqual(css.lastIndexOf("@media (max-width: 700px)", layoutRule), -1);
  assert.match(css, /@media \(max-width: 700px\)\s*\{[\s\S]*?\.force-model-layout\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\);/);
});
