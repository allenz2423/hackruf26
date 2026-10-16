import test from "node:test";
import assert from "node:assert/strict";
import { normalizeGuideBlocks } from "../src/study-guide/normalize.js";
import { mountAdditionalGuideBlocks, renderAdditionalGuideBlock } from "../src/study-guide/activity-blocks.js";
import { renderStudyGuideBlock } from "../src/study-guide/force-components.js";

const section = { heading: "Engineering guide", body: "Readable section fallback.", sources: [] };

const validBlocks = [
  {
    type: "interactive_model", template_id: "force_components_v1", title: "Force components",
    parameters: { force_n: 100, angle_deg: 30 }, alt_text: "A 100 N force and components.",
    fallback_text: "Horizontal component is 86.6 N; vertical component is 50 N."
  },
  {
    type: "interactive_model", template_id: "linear_graph_v1", title: "Linear graph",
    parameters: { slope: -2, intercept: 3 }, alt_text: "A descending line.",
    fallback_text: "The line is y = -2x + 3."
  },
  {
    type: "interactive_model", template_id: "first_order_response_v1", title: "First-order response",
    parameters: { initial_value: 0, final_value: 10, time_constant_s: 2 }, alt_text: "Response approaches ten.",
    fallback_text: "After one time constant, the response is about 6.32."
  },
  {
    type: "interactive_model", template_id: "resistor_divider_v1", title: "Resistor divider",
    parameters: { voltage_v: 12, r1_ohm: 1000, r2_ohm: 2000 }, alt_text: "Ideal divider circuit.",
    fallback_text: "Output across R2 is 8 V; series current is 4 mA."
  },
  {
    type: "interactive_model", template_id: "binary_search_trace_v1", title: "Binary search",
    parameters: { values: [1, 4, 9, 16, 25], target: 17 }, alt_text: "Search trace.",
    fallback_text: "Target 17 is not in the sorted values."
  }
];

test("engineering and CS activities expose accessible controls, graphics, and text fallbacks", () => {
  const blocks = normalizeGuideBlocks({ ...section, blocks: validBlocks });
  assert.equal(blocks.length, validBlocks.length);

  for (const [index, block] of blocks.entries()) {
    const html = renderStudyGuideBlock(block);
    if (index > 0) {
      assert.match(html, /<details class="activity-text-alternative">/);
      assert.match(html, /<summary>Text description<\/summary>/);
      const plainText = html.replace(/<[^>]*>/g, "");
      assert.ok(plainText.includes(block.fallback_text));
    }
    assert.doesNotMatch(html, /<script\b|javascript:/i);
  }

  const force = renderStudyGuideBlock(blocks[0]);
  assert.match(force, /<svg[^>]+role="img"[^>]+aria-label=/);
  assert.match(force, /aria-label="Force magnitude in newtons"/);
  assert.match(force, /aria-label="Angle in degrees"/);

  const graph = renderAdditionalGuideBlock(blocks[1]);
  assert.match(graph, /Graph of y equals/);
  assert.match(graph, /type="range"[^>]+data-activity-control="slope"/);
  assert.match(graph, /type="number"[^>]+data-activity-control="intercept"/);

  const response = renderAdditionalGuideBlock(blocks[2]);
  assert.match(response, /First-order response starting at 0, approaching 10/);
  assert.match(response, /aria-label="Elapsed time in seconds"/);

  const divider = renderAdditionalGuideBlock(blocks[3]);
  assert.match(divider, /output voltage is measured across R two/i);
  assert.match(divider, /Upper resistor R₁/);
  assert.match(divider, /Lower resistor R₂/);

  const search = renderAdditionalGuideBlock(blocks[4]);
  assert.match(search, /<ol class="binary-search-trace"/);
  assert.match(search, /role="status" aria-live="polite"/);
  assert.match(search, />Next comparison<\/button>/);
  assert.match(search, />Previous<\/button>/);
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
    this.children = new Map();
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this._textContent = this._innerHTML.replace(/<[^>]*>/g, "");
    const stepIds = [...this._innerHTML.matchAll(/data-trace-step="(\d+)"/g)].map((match) => match[1]);
    if (stepIds.length) {
      this.children.set("[data-trace-step]", stepIds.map((traceStep) => new FakeElement({
        dataset: { traceStep }
      })));
    }
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
  querySelectorAll(selector) { return this.children.get(selector) || []; }
  dispatch(type, extra = {}) {
    globalThis.document.activeElement = this;
    for (const handler of this.handlers.get(type) || []) handler({ preventDefault() {}, ...extra });
  }
}

test("binary-search trace reveals, reverses, completes, and resets deterministically", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const list = new FakeElement();
    const status = new FakeElement();
    const previous = new FakeElement();
    const next = new FakeElement();
    const reset = new FakeElement();
    const card = {
      dataset: {
        guideActivity: "binary_search_trace_v1",
        values: "[1,4,9,16,25]",
        target: "17"
      },
      querySelectorAll: () => [],
      querySelector(selector) {
        return new Map([
          ["[data-trace-steps]", list], ["[data-trace-status]", status],
          ["[data-trace-previous]", previous], ["[data-trace-next]", next],
          ["[data-trace-reset]", reset]
        ]).get(selector) || null;
      }
    };

    mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
    const traceSteps = list.querySelectorAll("[data-trace-step]");
    assert.equal(traceSteps.length, 3);
    assert.equal(status.textContent, "Ready to search 5 sorted values for 17.");
    assert.equal(previous.disabled, true);
    assert.equal(next.disabled, false);

    next.dispatch("click");
    assert.equal(traceSteps[0].hidden, false);
    assert.equal(status.textContent, "Comparison 1 of 3 complete. Continue to inspect the next half.");
    next.dispatch("click");
    next.dispatch("click");
    assert.equal(traceSteps[2].hidden, false);
    assert.equal(status.textContent, "Search complete. 17 is not in the list.");
    assert.equal(next.disabled, true);

    previous.dispatch("click");
    assert.equal(traceSteps[2].hidden, true);
    assert.equal(status.textContent, "Comparison 2 of 3 complete. Continue to inspect the next half.");
    reset.dispatch("click");
    assert.ok(traceSteps.every((step) => step.hidden));
    assert.equal(status.textContent, "Ready to search 5 sorted values for 17.");
    assert.equal(previous.disabled, true);
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("resistor-divider controls recalculate both electrical values and reset to authored defaults", () => {
  const priorDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  try {
    const voltageRange = new FakeElement({ dataset: { activityControl: "voltage_v" }, type: "range", value: 12, min: 0, max: 24 });
    const voltageNumber = new FakeElement({ dataset: { activityControl: "voltage_v" }, type: "number", value: 12, min: 0, max: 24 });
    const r1Range = new FakeElement({ dataset: { activityControl: "r1_ohm" }, type: "range", value: 1000, min: 1, max: 1_000_000 });
    const r1Number = new FakeElement({ dataset: { activityControl: "r1_ohm" }, type: "number", value: 1000, min: 1, max: 1_000_000 });
    const r2Range = new FakeElement({ dataset: { activityControl: "r2_ohm" }, type: "range", value: 2000, min: 1, max: 1_000_000 });
    const r2Number = new FakeElement({ dataset: { activityControl: "r2_ohm" }, type: "number", value: 2000, min: 1, max: 1_000_000 });
    const result = new FakeElement();
    const reset = new FakeElement();
    const controls = [voltageRange, voltageNumber, r1Range, r1Number, r2Range, r2Number];
    const card = {
      dataset: {
        guideActivity: "resistor_divider_v1", defaultVoltage: "12", defaultR1: "1000", defaultR2: "2000"
      },
      querySelectorAll: (selector) => selector === "[data-activity-control]" ? controls : [],
      querySelector(selector) {
        return new Map([["[data-activity-result]", result], ["[data-activity-reset]", reset]]).get(selector) || null;
      }
    };

    mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
    voltageNumber.value = "18";
    voltageNumber.dispatch("input");
    assert.equal(voltageRange.value, "18");
    assert.equal(result.textContent, "V_{out} = 12 V; series current = 6 mA.");

    r1Number.value = "2000";
    r1Number.dispatch("input");
    assert.equal(result.textContent, "V_{out} = 9 V; series current = 4.5 mA.");

    reset.dispatch("click");
    assert.deepEqual(controls.map((input) => input.value), ["12", "12", "1000", "1000", "2000", "2000"]);
    assert.equal(result.textContent, "V_{out} = 8 V; series current = 4 mA.");
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});

test("force-component renderer mounting keeps a zero-force result finite and accessible", () => {
  const block = normalizeGuideBlocks({
    ...section,
    blocks: [{
      type: "interactive_model", template_id: "force_components_v1", title: "Zero force",
      parameters: { force_n: 0, angle_deg: 0 }, alt_text: "Zero force vector.", fallback_text: "Both components are zero."
    }]
  })[0];
  const html = renderStudyGuideBlock(block);
  assert.match(html, /aria-label="0 newtons at 0 degrees; horizontal component 0 newtons and vertical component 0 newtons/);
  assert.doesNotMatch(html, /NaN|Infinity/);
  assert.match(html, /Force magnitude in newtons/);
  assert.match(html, /Angle in degrees/);
});
