import { escapeHtml } from "./ui.js";

// Runtime LaTeX formatting for AI-written text.
//
// The tutor and generated study material sometimes include inline LaTeX such as
// "$r\theta$-plane". This module finds the math, shows everything else as plain
// escaped text, and lets KaTeX typeset the math. It works in two steps so it can be
// used inside template strings: mathHtml() returns safe HTML straight away (math is
// shown as its raw source), then typesetMath() swaps the sources for rendered math
// once KaTeX has loaded. KaTeX itself loads only when a page actually has math.
//
// Recognised: $...$ and \(...\) inline, $$...$$ and \[...\] display, \$ for a literal
// dollar sign. A single $ only opens math when it looks like math, so prices such as
// "$5 and $10" stay text (the same rules as Pandoc).

const MAX_SOURCE_LENGTH = 2000;
const MAX_SEGMENTS = 200;

const isSpace = (char) => char === undefined || /\s/.test(char);
const isDigit = (char) => char !== undefined && char >= "0" && char <= "9";

function findClose(text, start, close) {
  let index = text.indexOf(close, start);
  while (index !== -1 && text[index - 1] === "\\" && text[index - 2] !== "\\") {
    index = text.indexOf(close, index + 1);
  }
  return index;
}

// The end of an inline $...$ expression that begins at `open`, or -1 if it is not math.
function findInlineDollarEnd(text, open) {
  if (isSpace(text[open + 1])) return -1;
  for (let index = open + 1; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\n") return -1;
    if (char === "\\") {
      index += 1;
      continue;
    }
    if (char === "$") {
      if (index === open + 1) return -1;
      if (isSpace(text[index - 1])) continue;
      if (isDigit(text[index + 1])) continue;
      return index;
    }
  }
  return -1;
}

export function splitMath(text) {
  const source = String(text ?? "");
  const segments = [];
  let plain = "";
  let mathCount = 0;

  const flush = () => {
    if (plain) segments.push({ type: "text", value: plain });
    plain = "";
  };
  const pushMath = (raw, tex, display) => {
    const cleaned = tex.trim();
    if (!cleaned || cleaned.length > MAX_SOURCE_LENGTH || mathCount >= MAX_SEGMENTS) return false;
    flush();
    segments.push({ type: "math", raw, tex: cleaned.replace(/\s*\n\s*/g, " "), display });
    mathCount += 1;
    return true;
  };

  for (let index = 0; index < source.length; ) {
    if (source.startsWith("\\$", index)) {
      plain += "$";
      index += 2;
    } else if (source.startsWith("$$", index)) {
      const end = source.indexOf("$$", index + 2);
      if (end !== -1 && pushMath(source.slice(index, end + 2), source.slice(index + 2, end), true)) {
        index = end + 2;
      } else {
        plain += "$$";
        index += 2;
      }
    } else if (source.startsWith("\\[", index)) {
      const end = findClose(source, index + 2, "\\]");
      if (end !== -1 && pushMath(source.slice(index, end + 2), source.slice(index + 2, end), true)) {
        index = end + 2;
      } else {
        plain += "\\[";
        index += 2;
      }
    } else if (source.startsWith("\\(", index)) {
      const end = findClose(source, index + 2, "\\)");
      if (end !== -1 && pushMath(source.slice(index, end + 2), source.slice(index + 2, end), false)) {
        index = end + 2;
      } else {
        plain += "\\(";
        index += 2;
      }
    } else if (source[index] === "$") {
      const end = findInlineDollarEnd(source, index);
      if (end !== -1 && pushMath(source.slice(index, end + 1), source.slice(index + 1, end), false)) {
        index = end + 1;
      } else {
        plain += "$";
        index += 1;
      }
    } else {
      plain += source[index];
      index += 1;
    }
  }
  flush();
  return segments;
}

// Safe HTML: text is escaped; each math expression becomes a span that holds its
// original text until typesetMath() renders it.
export function mathHtml(text) {
  return splitMath(text)
    .map((segment) => {
      if (segment.type === "text") return escapeHtml(segment.value);
      return `<span class="tex-math${segment.display ? " is-display" : ""}" data-display="${segment.display ? "1" : "0"}" data-tex="${escapeHtml(segment.tex)}">${escapeHtml(segment.raw)}</span>`;
    })
    .join("");
}

let katexLoad = null;

function loadKatex() {
  katexLoad ??= Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([module]) => module.default ?? module);
  return katexLoad;
}

const KATEX_OPTIONS = {
  throwOnError: true,
  strict: "ignore",
  trust: false,
  maxExpand: 1000,
  output: "htmlAndMathml"
};

// Render every not-yet-rendered expression inside `root`. Anything KaTeX cannot parse
// stays as its original text, so a typo never hides the tutor's words.
export async function typesetMath(root) {
  if (!root?.querySelectorAll) return;
  const pending = [...root.querySelectorAll(".tex-math:not([data-typeset])")];
  if (!pending.length) return;

  let katex;
  try {
    katex = await loadKatex();
  } catch {
    katexLoad = null;
    return;
  }

  for (const node of pending) {
    node.dataset.typeset = "1";
    try {
      node.innerHTML = katex.renderToString(node.dataset.tex, {
        ...KATEX_OPTIONS,
        displayMode: node.dataset.display === "1"
      });
      node.classList.add("is-rendered");
    } catch {
      node.classList.add("is-error");
    }
  }
}

// Convenience for plain elements, such as a chat bubble.
export function renderMathInto(element, text) {
  element.innerHTML = mathHtml(text);
  void typesetMath(element);
}
