import assert from "node:assert/strict";
import test from "node:test";

import { mathHtml, splitMath } from "../src/math.js";

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
