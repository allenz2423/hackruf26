import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import katex from "katex";

import { displayMathHtml, mathHtml, splitMath } from "../src/math.js";

const kinds = (text) => splitMath(text).map((s) => (s.type === "math" ? `${s.display ? "D" : "I"}:${s.tex}` : `T:${s.value}`));

test("finds the inline math in the tutor's own example", () => {
  const text =
    "The line in the $r\\theta$-plane shows that $r$ is constant while $\\theta$ varies. What kind of curve is that in the $xy$-plane?";
  const math = splitMath(text).filter((s) => s.type === "math");
  assert.deepEqual(math.map((s) => s.tex), ["r\\theta", "r", "\\theta", "xy"]);
  assert.ok(math.every((s) => !s.display));
  // The words between the math are untouched.
  assert.ok(splitMath(text).some((s) => s.type === "text" && s.value === "-plane shows that "));
});

test("recognises the other delimiters", () => {
  assert.deepEqual(kinds("so \\(x^2\\) here"), ["T:so ", "I:x^2", "T: here"]);
  assert.deepEqual(kinds("$$a+b$$"), ["D:a+b"]);
  assert.deepEqual(kinds("see \\[\\frac{1}{2}\\] now"), ["T:see ", "D:\\frac{1}{2}", "T: now"]);
  assert.deepEqual(kinds("$$\n a \\\\ b \n$$"), ["D:a \\\\ b"]);
  assert.deepEqual(
    kinds("\\begin{alignat}{2}x&=1&y&=2\\end{alignat}"),
    ["D:\\begin{alignedat}{2}x&=1&y&=2\\end{alignedat}"]
  );
});

test("prices and stray dollar signs stay plain text", () => {
  for (const text of [
    "It costs $5 and $10 in total.",
    "I have $5, you have $10.",
    "Pay $ 5 now",
    "Only one $ sign here",
    "A $5.00 fee",
    "$",
    "$$",
    "empty $$ pair",
    "line one $x\nline two$"
  ]) {
    assert.ok(splitMath(text).every((s) => s.type === "text"), `should not find math in: ${text}`);
    assert.equal(splitMath(text).map((s) => s.value).join(""), text);
  }
});

test("an escaped dollar sign is a literal dollar sign", () => {
  assert.deepEqual(kinds("a \\$5 fee and $x$"), ["T:a $5 fee and ", "I:x"]);
});

test("math next to punctuation and digits works", () => {
  assert.deepEqual(kinds("($x$), then $y$."), ["T:(", "I:x", "T:), then ", "I:y", "T:."]);
  assert.deepEqual(kinds("area is $\\pi r^2$ cm"), ["T:area is ", "I:\\pi r^2", "T: cm"]);
});

test("an unclosed formula cannot swallow prose or later formulas", () => {
  const text = "The transformed tensor is $\\boldsymbol{\\sigma}'=\\mathbf{A}\\boldsymbol{\\sigma}\\mathbfand each new component is obtained by projection. The maximum shear acts at $45^\\circ$ from the principal directions.";
  const segments = splitMath(text);
  const math = segments.filter((segment) => segment.type === "math");
  const prose = segments.filter((segment) => segment.type === "text").map((segment) => segment.value).join("");

  assert.deepEqual(math.map((segment) => segment.tex), ["45^\\circ"]);
  assert.match(prose, /and each new component is obtained by projection/);
  assert.match(prose, /The transformed tensor is \$/);
  assert.match(prose, /from the principal directions\./);
});

test("multi-letter math names inside groups remain renderable", () => {
  assert.deepEqual(kinds("$x_{max}=\\text{kg}$ and $xy$"), ["I:x_{max}=\\text{kg}", "T: and ", "I:xy"]);
});

test("detects and KaTeX-renders the undelimited glossary symbols from study guide asa", () => {
  const asaPreview = JSON.parse(readFileSync(
    new URL("./fixtures/asa-active-preview.json", import.meta.url),
    "utf8"
  ));
  const symbols = asaPreview.sections
    .flatMap((section) => Array.isArray(section.blocks) ? section.blocks : [])
    .filter((block) => block.type === "formula")
    .flatMap((block) => Array.isArray(block.definitions) ? block.definitions : [])
    .map((definition) => definition.symbol)
    .filter((symbol) => typeof symbol === "string");
  const targets = ["\\sigma_{max}", "\\sigma_{min}", "\\sigma_{ut}", "\\sigma_{uc}"];
  const targetDefinitions = symbols.filter((symbol) => targets.some((target) => symbol.includes(target)));

  assert.ok(targetDefinitions.some((symbol) => symbol.includes("\\sigma_{max},\\sigma_{min}")));
  assert.ok(targetDefinitions.some((symbol) => symbol === "\\sigma_{ut}"));
  assert.ok(targetDefinitions.some((symbol) => symbol === "\\sigma_{uc}"));

  const math = targetDefinitions.flatMap((symbol) =>
    splitMath(symbol).filter((segment) => segment.type === "math")
  );
  assert.deepEqual([...new Set(math.map((segment) => segment.tex))].sort(), [...targets].sort());
  assert.ok(math.every((segment) => segment.raw === segment.tex));

  for (const segment of math) {
    assert.match(mathHtml(segment.raw), /class="tex-math"/);
    assert.match(katex.renderToString(segment.tex, {
      throwOnError: true,
      trust: false,
      output: "htmlAndMathml"
    }), /class="katex"/);
  }
});

test("detects bare TeX inline in prose without consuming the surrounding words", () => {
  const inline = "The limit is \\sigma_{max} for this condition.";
  assert.deepEqual(kinds(inline), [
    "T:The limit is ",
    "I:\\sigma_{max}",
    "T: for this condition."
  ]);
  const segments = splitMath(inline);
  assert.equal(segments.map((segment) => segment.raw ?? segment.value).join(""), inline);
});

test("renders math emitted in code fences, inline code, HTML entities, and double-escaped TeX", () => {
  const samples = [
    "```latex\n\\sigma_{max}=3\n```",
    "Use `\\tau_{xy}` here.",
    "&sigma;_{max} = 3",
    String.raw`\\nu`,
    "\\begin{alignat}{2}x&=1&y&=2\\end{alignat}",
  ];
  assert.deepEqual(kinds(samples[0]), ["D:\\sigma_{max}=3"]);
  assert.deepEqual(kinds(samples[1]), ["T:Use ", "I:\\tau_{xy}", "T: here."]);
  assert.deepEqual(kinds(samples[2]), ["I:\\sigma_{max} = 3"]);
  assert.deepEqual(kinds(samples[3]), ["I:\\nu"]);

  const expressions = samples.flatMap((sample) => splitMath(sample).filter((part) => part.type === "math"));
  expressions.push(...["$σₓ=1$", "$\\text{H₂O}$", "$$\\sigma_{max}=3$$"]
    .flatMap((sample) => splitMath(sample).filter((part) => part.type === "math")));
  for (const expression of expressions) {
    assert.match(katex.renderToString(expression.tex, {
      throwOnError: true,
      trust: false,
      displayMode: expression.display,
      output: "htmlAndMathml"
    }), /class="katex"/);
  }
});

test("formula fields normalize their existing delimiters and Unicode math", () => {
  assert.match(displayMathHtml("$$\\sigma_{max}=3$$"), /data-tex="\\sigma_\{max\}=3"/);
  assert.match(mathHtml("$σₓ=1$"), /data-tex="\\sigma_\{x\}=1"/);
  assert.match(mathHtml("$\\text{H₂O}$"), /data-tex="\\text\{H\}_\{2\}\\text\{O\}"/);
});

test("text around math is escaped, so HTML in the text cannot run", () => {
  const html = mathHtml('<img src=x onerror=alert(1)> and $x$ & <b>bold</b>');
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<b>"));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(html.includes('data-tex="x"'));
});

test("HTML inside the math is escaped in both the attribute and the fallback text", () => {
  const html = mathHtml('$x"><script>alert(1)</script>$');
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('"><'));
  assert.ok(html.includes("&quot;"));
});

test("the raw source stays visible until it is typeset", () => {
  assert.match(mathHtml("so $\\alpha$ ok"), /class="tex-math"[^>]*>\$\\alpha\$<\/span>/);
});

test("display math is marked and newlines inside math cannot break the markup", () => {
  const html = mathHtml("$$\nx\n+\ny\n$$");
  assert.match(html, /class="tex-math is-display"/);
  assert.ok(!/data-tex="[^"]*\n/.test(html));
});

test("limits: an enormous expression or a flood of them is left as text", () => {
  assert.ok(splitMath(`$${"x".repeat(2500)}$`).every((s) => s.type === "text"));
  const many = Array.from({ length: 260 }, (_, i) => `$a${i}$`).join(" ");
  assert.equal(splitMath(many).filter((s) => s.type === "math").length, 200);
});

test("empty and missing input is fine", () => {
  assert.equal(mathHtml(""), "");
  assert.equal(mathHtml(null), "");
  assert.equal(mathHtml(undefined), "");
  assert.equal(mathHtml("no math at all"), "no math at all");
});
