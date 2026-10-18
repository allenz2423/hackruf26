import test from "node:test";
import assert from "node:assert/strict";

import { renderStudyGuideText } from "../src/study-guide/text-renderer.js";

test("guide text renders math, chemistry notation, safe code, and accessible tables", () => {
  const input = [
    String.raw`Balance $\ce{2H2 + O2 -> 2H2O}$.`,
    "",
    "| Term | Meaning |",
    "| --- | --- |",
    "| Osmosis | Water movement |",
    "",
    "```python",
    'print("<script>alert(1)</script>")',
    "```"
  ].join("\n");
  const html = renderStudyGuideText(input);
  assert.match(html, /data-tex="\\ce\{2H2 \+ O2 -&gt; 2H2O\}"/);
  assert.match(html, /class="guide-table-scroll" role="region" aria-label="Study guide table" tabindex="0"/);
  assert.match(html, /<th scope="col">Term<\/th>/);
  assert.match(html, /class="guide-code-window"/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
});

test("malformed Markdown tables stay readable prose and raw HTML stays escaped", () => {
  const html = renderStudyGuideText("| no | separator |\n| still | text |\n\n<img src=x onerror=alert(1)>");
  assert.doesNotMatch(html, /class="guide-table-scroll"/);
  assert.match(html, /\| no \| separator \|/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});
