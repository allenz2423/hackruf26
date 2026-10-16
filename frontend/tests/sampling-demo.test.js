import test from "node:test";
import assert from "node:assert/strict";
import { normalizeGuideBlocks } from "../src/study-guide/normalize.js";
import { mountAdditionalGuideBlocks } from "../src/study-guide/activity-blocks.js";
import { renderStudyGuideBlock } from "../src/study-guide/force-components.js";
import { SAMPLING_DEMO_POPULATION, samplingDemoResult } from "../src/study-guide/sampling-demo.js";

const section = { heading: "Sampling", body: "Sampling fallback.", sources: [] };
const sampleBlock = {
  type: "interactive_model",
  template_id: "sampling_demo_v1",
  title: "Sampling and assignment",
  parameters: { method: "stratified", sample_size: 6, seed: 17, random_assignment: false }
};

test("sampling demo normalizes only bounded methods and app-authored data", () => {
  const [normalized] = normalizeGuideBlocks({ ...section, blocks: [sampleBlock] });
  assert.equal(normalized.type, "interactive_model");
  assert.equal(normalized.template_id, "sampling_demo_v1");
  assert.match(normalized.fallback_text, /app-authored synthetic illustration, not source data/i);
  assert.match(normalized.alt_text, /fictional students/);

  for (const parameters of [
    { ...sampleBlock.parameters, method: "cluster" },
    { ...sampleBlock.parameters, sample_size: 3 },
    { ...sampleBlock.parameters, sample_size: 5 },
    { ...sampleBlock.parameters, sample_size: 14 },
    { ...sampleBlock.parameters, seed: 0 },
    { ...sampleBlock.parameters, random_assignment: "yes" },
    { ...sampleBlock.parameters, population: [{ id: "invented" }] }
  ]) {
    assert.deepEqual(normalizeGuideBlocks({ ...section, blocks: [{ ...sampleBlock, parameters }] }), [{
      id: "section-1-fallback", type: "paragraph", text: section.body, sources: []
    }]);
  }
});

test("sampling result uses balanced strata and random assignment is a separate step", () => {
  assert.equal(SAMPLING_DEMO_POPULATION.length, 24);
  const sampled = samplingDemoResult("stratified", 8, 5, false);
  const repeated = samplingDemoResult("stratified", 8, 5, false);
  assert.deepEqual(sampled.selected.map((row) => row.id), repeated.selected.map((row) => row.id));
  assert.deepEqual(sampled.stratumCounts, { "Section A": 4, "Section B": 4 });

  const assigned = samplingDemoResult("stratified", 8, 5, true);
  assert.deepEqual(assigned.selected.map((row) => row.id), sampled.selected.map((row) => row.id));
  assert.equal(Object.values(assigned.assignment).filter((group) => group === "Treatment A").length, 4);
  assert.equal(Object.values(assigned.assignment).filter((group) => group === "Treatment B").length, 4);
});

test("inclusive parameter bounds select complete unique samples with even treatment groups", () => {
  for (const method of ["simple_random", "stratified", "front_row_convenience"]) {
    for (const [sampleSize, seed] of [[4, 1], [12, 9999]]) {
      const result = samplingDemoResult(method, sampleSize, seed, true);
      const ids = result.selected.map((row) => row.id);
      assert.equal(ids.length, sampleSize);
      assert.equal(new Set(ids).size, sampleSize);
      assert.equal(Object.keys(result.assignment).length, sampleSize);
      assert.equal(Object.values(result.assignment).filter((group) => group === "Treatment A").length, sampleSize / 2);
      assert.equal(Object.values(result.assignment).filter((group) => group === "Treatment B").length, sampleSize / 2);
      assert.ok(Number.isFinite(result.sampleMean));
    }
  }
});

test("sampling demo renders labeled controls, an accessible population table, and synthetic-data caveat", () => {
  const [normalized] = normalizeGuideBlocks({ ...section, blocks: [sampleBlock] });
  const html = normalized.type === "interactive_model"
    ? renderSamplingBlock(normalized)
    : "";
  assert.match(html, /<fieldset class="sampling-controls"><legend>Choose the sampling process<\/legend>/);
  assert.match(html, /<label for="[^"]+-method">Sampling method<\/label>/);
  assert.match(html, /<option value="stratified" selected>Proportional stratified sample<\/option>/);
  assert.match(html, /<option value="6" selected>6 students<\/option>/);
  assert.match(html, /<input id="[^"]+-assignment" type="checkbox" data-sampling-assignment-toggle>/);
  assert.match(html, /<button class="btn btn-secondary btn-small" type="button" data-sampling-reset>Reset<\/button>/);
  assert.match(html, /<button class="btn btn-secondary" type="button" data-sampling-redraw>Draw sample again<\/button>/);
  assert.match(html, /<table class="sampling-population-table">/);
  assert.match(html, /<caption>All 24 fictional students\./);
  assert.match(html, /role="status" aria-live="polite"/);
  assert.match(html, /App-authored synthetic illustration; not course source data\./);
  assert.doesNotMatch(html, /<script\b|javascript:/i);
});

function renderSamplingBlock(block) {
  // Use the public block renderer to keep this test on the same path as saved guides.
  return renderStudyGuideBlock(block);
}

class FakeControl {
  constructor({ value = "", checked = false, dataset = {}, textContent = "" } = {}) {
    this.value = String(value);
    this.checked = checked;
    this.dataset = dataset;
    this.textContent = textContent;
    this.handlers = new Map();
  }
  addEventListener(type, callback) { this.handlers.set(type, callback); }
  dispatch(type) { this.handlers.get(type)?.({ type }); }
}

class FakeRow {
  constructor(id) {
    this.dataset = { samplingId: id };
    this.classList = { toggle: (name, active) => { this.selected = active; } };
    this.cells = { "[data-sampling-status]": new FakeControl(), "[data-sampling-treatment]": new FakeControl() };
  }
  querySelector(selector) { return this.cells[selector] || null; }
}

test("sampling controls redraw, randomize assignment, and reset to authored defaults", () => {
  const method = new FakeControl({ value: "stratified" });
  Object.defineProperty(method, "selectedOptions", {
    get() {
      const names = {
        simple_random: "Simple random sample",
        stratified: "Proportional stratified sample",
        front_row_convenience: "Front-row convenience sample"
      };
      return [{ textContent: names[this.value] }];
    }
  });
  const size = new FakeControl({ value: 6 });
  const assignment = new FakeControl({ checked: false });
  const status = new FakeControl();
  const note = new FakeControl();
  const seed = new FakeControl();
  const redraw = new FakeControl();
  const reset = new FakeControl();
  const rows = SAMPLING_DEMO_POPULATION.map((row) => new FakeRow(row.id));
  const card = {
    dataset: {
      guideActivity: "sampling_demo_v1", defaultMethod: "stratified",
      defaultSampleSize: "6", defaultSeed: "17", defaultRandomAssignment: "false"
    },
    querySelectorAll: (selector) => selector === "[data-sampling-id]" ? rows : [],
    querySelector(selector) {
      return {
        "[data-sampling-method]": method,
        "[data-sampling-size]": size,
        "[data-sampling-assignment-toggle]": assignment,
        "[data-sampling-result]": status,
        "[data-sampling-method-note]": note,
        "[data-sampling-seed]": seed,
        "[data-sampling-redraw]": redraw,
        "[data-sampling-reset]": reset
      }[selector] || null;
    }
  };

  mountAdditionalGuideBlocks({ querySelectorAll: () => [card] });
  assert.equal(rows.filter((row) => row.selected).length, 6);
  assert.match(note.textContent, /3 students from each equally sized section/);

  method.value = "front_row_convenience";
  method.dispatch("change");
  assert.equal(rows.filter((row) => row.selected).length, 6);
  assert.match(note.textContent, /deliberately set higher/);
  assert.ok(rows.slice(0, 6).every((row) => row.selected));

  assignment.checked = true;
  assignment.dispatch("change");
  assert.match(status.textContent, /Random assignment put .* Treatment A and .* Treatment B/);
  redraw.dispatch("click");
  assert.equal(seed.textContent, "Seed 18");

  reset.dispatch("click");
  assert.equal(method.value, "stratified");
  assert.equal(size.value, "6");
  assert.equal(assignment.checked, false);
  assert.equal(seed.textContent, "Seed 17");
});
