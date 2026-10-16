import test from "node:test";
import assert from "node:assert/strict";
import { buildMermaidFlowchart } from "../src/study-guide/libraries/mermaid-flow.js";

test("validates stage IDs and preserves the supplied stage order", () => {
  const source = buildMermaidFlowchart([
    { id: "collect-data", title: "Collect data", explanation: "Ignored extra field." },
    { id: "analyze_data", title: "Analyze data", source: "flowchart LR; bad" },
    { id: "report", title: "Report results", mermaid: "classDef evil" }
  ]);

  assert.equal(source, [
    "flowchart TD",
    '  stage_0["Collect data"]',
    '  stage_1["Analyze data"]',
    '  stage_2["Report results"]',
    "  stage_0 --> stage_1",
    "  stage_1 --> stage_2"
  ].join("\n"));
  assert.doesNotMatch(source, /Ignored extra field|flowchart LR|classDef/);
});

test("sanitizes Mermaid syntax and control characters inside titles", () => {
  const source = buildMermaidFlowchart([
    { id: "unsafe", title: 'Start ";\nclick x "https://bad"; <script> & [x] | `code`' },
    { id: "finish", title: "Finish" }
  ]);

  assert.match(source, /Start ＂； click x ＂https：\/\/bad＂； ‹script› ＆ ［x］ ｜ ｀code｀/);
  assert.doesNotMatch(source, /<script>|"https|\nclick\s|^click\s/m);
  assert.equal(source.split("\n").filter((line) => line.includes(" --> ")).length, 1);
});

test("accepts the supported stage-count bounds and rejects malformed inputs", () => {
  const makeStages = (count) => Array.from({ length: count }, (_, index) => ({ id: `step-${index}`, title: `Step ${index + 1}` }));

  assert.match(buildMermaidFlowchart(makeStages(2)), /^flowchart TD/);
  assert.match(buildMermaidFlowchart(makeStages(12)), /stage_11/);
  assert.throws(() => buildMermaidFlowchart(makeStages(1)), RangeError);
  assert.throws(() => buildMermaidFlowchart(makeStages(13)), RangeError);
  assert.throws(() => buildMermaidFlowchart(null), RangeError);
  assert.throws(() => buildMermaidFlowchart([
    { id: "duplicate", title: "First" }, { id: "duplicate", title: "Second" }
  ]), /unique, valid stage ID/);
  assert.throws(() => buildMermaidFlowchart([
    { id: "bad id", title: "First" }, { id: "second", title: "Second" }
  ]), /unique, valid stage ID/);
  assert.throws(() => buildMermaidFlowchart([
    { id: "first", title: "  " }, { id: "second", title: "Second" }
  ]), /needs a title/);
  assert.throws(() => buildMermaidFlowchart([
    { id: "first", title: "x".repeat(101) }, { id: "second", title: "Second" }
  ]), /needs a title/);
});
