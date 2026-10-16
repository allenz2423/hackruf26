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
// Recognised: standard inline/display delimiters, equation environments, bare TeX
// commands and scripts, and Unicode math notation. Unmatched text is escaped, while
// notation KaTeX cannot parse stays visible as its original source.

const MAX_SOURCE_LENGTH = 2000;
const MAX_SEGMENTS = 200;

const UNICODE_TEX = new Map(Object.entries({
  "α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta",
  "ε": "\\epsilon", "ϵ": "\\varepsilon", "ζ": "\\zeta", "η": "\\eta",
  "θ": "\\theta", "ϑ": "\\vartheta", "ι": "\\iota", "κ": "\\kappa",
  "λ": "\\lambda", "μ": "\\mu", "ν": "\\nu", "ξ": "\\xi",
  "π": "\\pi", "ϖ": "\\varpi", "ρ": "\\rho", "σ": "\\sigma",
  "ς": "\\varsigma", "τ": "\\tau", "υ": "\\upsilon", "φ": "\\phi",
  "ϕ": "\\varphi", "χ": "\\chi", "ψ": "\\psi", "ω": "\\omega",
  "Γ": "\\Gamma", "Δ": "\\Delta", "Θ": "\\Theta", "Λ": "\\Lambda",
  "Ξ": "\\Xi", "Π": "\\Pi", "Σ": "\\Sigma", "Υ": "\\Upsilon",
  "Φ": "\\Phi", "Ψ": "\\Psi", "Ω": "\\Omega",
  "∂": "\\partial", "∇": "\\nabla", "∞": "\\infty", "∝": "\\propto",
  "≤": "\\leq", "≥": "\\geq", "≠": "\\neq", "≈": "\\approx",
  "≡": "\\equiv", "×": "\\times", "÷": "\\div", "·": "\\cdot",
  "±": "\\pm", "∓": "\\mp", "−": "-", "→": "\\rightarrow",
  "←": "\\leftarrow", "↔": "\\leftrightarrow", "∫": "\\int",
  "∑": "\\sum", "√": "\\sqrt", "∈": "\\in", "∉": "\\notin",
  "⊥": "\\perp", "∥": "\\parallel", "∠": "\\angle", "∅": "\\emptyset",
  "∩": "\\cap", "∪": "\\cup", "⊂": "\\subset", "⊆": "\\subseteq",
  "∧": "\\land", "∨": "\\lor", "¬": "\\lnot", "∀": "\\forall",
  "∃": "\\exists", "∴": "\\therefore", "∵": "\\because",
  "ℝ": "\\mathbb{R}", "ℤ": "\\mathbb{Z}", "ℕ": "\\mathbb{N}",
  "ℚ": "\\mathbb{Q}", "ℂ": "\\mathbb{C}", "°": "^{\\circ}",
}));

const UNICODE_SUBSCRIPT = new Map(Object.entries({
  "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
  "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
  "₌": "=", "₍": "(", "₎": ")", "ₐ": "a", "ₑ": "e", "ₕ": "h",
  "ᵢ": "i", "ₖ": "k", "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o",
  "ₚ": "p", "ᵣ": "r", "ₛ": "s", "ₜ": "t", "ᵤ": "u", "ᵥ": "v",
  "ₓ": "x", "ᵧ": "y",
}));

const UNICODE_SUPERSCRIPT = new Map(Object.entries({
  "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
  "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
  "⁼": "=", "⁽": "(", "⁾": ")", "ᵃ": "a", "ᵇ": "b", "ᶜ": "c",
  "ᵈ": "d", "ᵉ": "e", "ᶠ": "f", "ᵍ": "g", "ʰ": "h", "ⁱ": "i",
  "ʲ": "j", "ᵏ": "k", "ˡ": "l", "ᵐ": "m", "ⁿ": "n", "ᵒ": "o",
  "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u", "ᵛ": "v",
  "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z", "ᵧ": "y",
}));

const MATH_ENTITIES = new Map(Object.entries({
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", theta: "θ",
  lambda: "λ", mu: "μ", nu: "ν", pi: "π", rho: "ρ", sigma: "σ", tau: "τ",
  phi: "φ", omega: "ω", Gamma: "Γ", Delta: "Δ", Sigma: "Σ", Omega: "Ω",
  infin: "∞", plusmn: "±", minus: "−", times: "×", divide: "÷", middot: "·",
  le: "≤", ge: "≥", ne: "≠", equiv: "≡", approx: "≈", radic: "√", sum: "∑",
  int: "∫", part: "∂", nabla: "∇", rarr: "→", larr: "←", perp: "⊥", parallel: "∥",
}));

const MATH_WORDS = new Set(["sin", "cos", "tan", "cot", "log", "ln", "exp", "lim", "max", "min"]);
const PROSE_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could",
  "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "how",
  "if", "in", "into", "is", "it", "its", "may", "might", "must", "no", "nor", "not",
  "of", "on", "or", "our", "she", "should", "so", "than", "that", "the", "their",
  "them", "then", "there", "these", "they", "this", "those", "to", "was", "we", "were",
  "what", "when", "where", "which", "who", "will", "with", "would", "you", "your",
]);
const BARE_ENVIRONMENTS = new Set([
  "math", "equation", "equation*", "displaymath", "align", "align*", "gather", "gather*",
  "multline", "multline*", "flalign", "flalign*", "alignat", "alignat*", "alignedat",
  "aligned", "gathered", "split", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix",
  "Vmatrix", "smallmatrix", "cases", "array", "subarray",
]);

const isSpace = (char) => char === undefined || /\s/.test(char);
const isDigit = (char) => char !== undefined && char >= "0" && char <= "9";

function findClose(text, start, close) {
  let index = text.indexOf(close, start);
  while (index !== -1 && text[index - 1] === "\\" && text[index - 2] !== "\\") {
    index = text.indexOf(close, index + 1);
  }
  return index;
}

function findBalancedGroupEnd(text, start) {
  if (text[start] !== "{") return -1;
  let depth = 0;
  for (let index = start; index < text.length; index += 1) {
    if (text[index] === "\\") {
      index += 1;
      continue;
    }
    if (text[index] === "{") depth += 1;
    else if (text[index] === "}") {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return -1;
}

// Find one undelimited TeX command, including its immediately attached groups
// and sub/superscripts. Keeping this to a single command avoids swallowing nearby
// prose; KaTeX validates the candidate later and leaves invalid input readable.
function findBareTexCommandEnd(text, start, nesting = 0) {
  if (nesting > 8 || text[start] !== "\\") return -1;
  const command = /^\\[A-Za-z]+/.exec(text.slice(start));
  if (!command) return -1;

  let index = start + command[0].length;
  while (index < text.length) {
    if (text[index] === "_" || text[index] === "^") {
      const argumentStart = index + 1;
      if (text[argumentStart] === "{") {
        const groupEnd = findBalancedGroupEnd(text, argumentStart);
        if (groupEnd === -1) break;
        index = groupEnd;
      } else if (text[argumentStart] === "\\") {
        const commandEnd = findBareTexCommandEnd(text, argumentStart, nesting + 1);
        if (commandEnd === -1) break;
        index = commandEnd;
      } else if (!isSpace(text[argumentStart])) {
        index = argumentStart + 1;
      } else {
        break;
      }
    } else if (text[index] === "{") {
      const groupEnd = findBalancedGroupEnd(text, index);
      if (groupEnd === -1) break;
      index = groupEnd;
    } else {
      break;
    }
  }
  return index;
}

function normalizeBareMath(tex) {
  const textCommands = new Set(["text", "textrm", "textsf", "texttt", "textbf", "textit", "mbox"]);
  const normalizeSegment = (segment) => {
    let normalized = [...segment].map((char) => UNICODE_TEX.get(char) ?? char).join("");
    const subscriptPattern = new RegExp(`[${[...UNICODE_SUBSCRIPT.keys()].join("")}]+`, "gu");
    const superscriptPattern = new RegExp(`[${[...UNICODE_SUPERSCRIPT.keys()].join("")}]+`, "gu");
    normalized = normalized.replace(subscriptPattern, (script) =>
      `_{${[...script].map((char) => UNICODE_SUBSCRIPT.get(char)).join("")}}`
    );
    return normalized.replace(superscriptPattern, (script) =>
      `^{${[...script].map((char) => UNICODE_SUPERSCRIPT.get(char)).join("")}}`
    );
  };

  let result = "";
  let cursor = 0;
  let segmentStart = 0;
  while (cursor < tex.length) {
    if (tex[cursor] === "\\") {
      const command = /^\\([A-Za-z]+)/.exec(tex.slice(cursor));
      if (command && textCommands.has(command[1])) {
        const groupStart = cursor + command[0].length;
        const groupEnd = tex[groupStart] === "{" ? findBalancedGroupEnd(tex, groupStart) : -1;
        if (groupEnd !== -1) {
          const original = tex.slice(cursor, groupEnd);
          const inner = tex.slice(groupStart + 1, groupEnd - 1);
          const hasUnicodeScript = [...inner].some((char) =>
            UNICODE_SUBSCRIPT.has(char) || UNICODE_SUPERSCRIPT.has(char)
          );
          if (hasUnicodeScript) {
            let converted = "";
            let text = "";
            const flushText = () => {
              if (text) converted += `${command[0]}{${text}}`;
              text = "";
            };
            for (let offset = 0; offset < inner.length; ) {
              const char = inner[offset];
              const scriptMap = UNICODE_SUBSCRIPT.has(char)
                ? UNICODE_SUBSCRIPT
                : UNICODE_SUPERSCRIPT.has(char) ? UNICODE_SUPERSCRIPT : null;
              if (!scriptMap) {
                text += char;
                offset += 1;
                continue;
              }
              flushText();
              let script = "";
              while (offset < inner.length && scriptMap.has(inner[offset])) {
                script += scriptMap.get(inner[offset]);
                offset += 1;
              }
              converted += `${scriptMap === UNICODE_SUBSCRIPT ? "_" : "^"}{${script}}`;
            }
            flushText();
            result += normalizeSegment(tex.slice(segmentStart, cursor)) + converted;
          } else {
            result += normalizeSegment(tex.slice(segmentStart, cursor)) + original;
          }
          cursor = groupEnd;
          segmentStart = cursor;
          continue;
        }
      }
    }
    cursor += 1;
  }
  return result + normalizeSegment(tex.slice(segmentStart));
}

function decodeMathEntities(text) {
  return text.replace(/&(#(?:x[\da-f]+|\d+)|[A-Za-z][A-Za-z\d]+);/gi, (raw, entity) => {
    let symbol = MATH_ENTITIES.get(entity);
    if (!symbol && entity[0] === "#") {
      const codePoint = entity[1]?.toLowerCase() === "x"
        ? Number.parseInt(entity.slice(2), 16)
        : Number.parseInt(entity.slice(1), 10);
      if (Number.isInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff) {
        symbol = String.fromCodePoint(codePoint);
      }
    }
    return symbol && (
      UNICODE_TEX.has(symbol) || UNICODE_SUBSCRIPT.has(symbol) || UNICODE_SUPERSCRIPT.has(symbol)
    ) ? symbol : raw;
  });
}

function looksLikeMathSource(value) {
  return /\\[A-Za-z]+|[_^]\s*(?:\{|[A-Za-z0-9\\])|[=+*/<>≤≥≠≈±∓×÷∑∫√∞∂∇]|[₀-₉⁰-⁹]/u.test(value);
}

function findMathFence(text, start) {
  if (start > 0 && text[start - 1] !== "\n") return null;
  const match = /^```([A-Za-z]*)[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*(?=\r?\n|$)/.exec(text.slice(start));
  if (!match) return null;
  const language = match[1].toLowerCase();
  if (language && !["math", "latex", "tex", "equation"].includes(language)) return null;
  if (!language && !looksLikeMathSource(match[2])) return null;
  return { end: start + match[0].length, raw: match[0], tex: match[2].trim() };
}

function findInlineCodeSpan(text, start) {
  let delimiterEnd = start;
  while (text[delimiterEnd] === "`") delimiterEnd += 1;
  const delimiterLength = delimiterEnd - start;
  if (!delimiterLength) return null;

  let cursor = delimiterEnd;
  while (cursor < text.length) {
    const next = text.indexOf("`", cursor);
    if (next === -1) return null;
    let runEnd = next;
    while (text[runEnd] === "`") runEnd += 1;
    if (runEnd - next === delimiterLength) {
      let value = text.slice(delimiterEnd, next);
      if (value.includes("\n") || value.includes("\r")) return null;
      if (/^ .* $/.test(value) && /[^ ]/.test(value)) value = value.slice(1, -1);
      return { end: runEnd, value };
    }
    cursor = runEnd;
  }
  return null;
}

function stripMathDelimiters(value) {
  const trimmed = String(value ?? "").trim();
  const wrappers = [
    /^\$\$([\s\S]*?)\$\$$/,
    /^\$([\s\S]*?)\$$/,
    /^\\\[([\s\S]*?)\\\]$/,
    /^\\\(([\s\S]*?)\\\)$/,
  ];
  for (const wrapper of wrappers) {
    const match = wrapper.exec(trimmed);
    if (match) return match[1].trim();
  }
  return trimmed;
}

function findBareScriptEnd(text, start) {
  const base = /^[\p{L}\p{N}]+/u.exec(text.slice(start));
  if (!base) return -1;
  let index = start + base[0].length;
  let hasScript = false;
  while (index < text.length) {
    if (text[index] === "_" || text[index] === "^") {
      const argumentStart = index + 1;
      if (text[argumentStart] === "{") {
        const groupEnd = findBalancedGroupEnd(text, argumentStart);
        if (groupEnd === -1) break;
        index = groupEnd;
      } else if (text[argumentStart] === "\\") {
        const commandEnd = findBareTexCommandEnd(text, argumentStart);
        if (commandEnd === -1) break;
        index = commandEnd;
      } else {
        const argument = /^[\p{L}\p{N}]+/u.exec(text.slice(argumentStart));
        if (!argument) break;
        index = argumentStart + argument[0].length;
      }
      hasScript = true;
    } else if (UNICODE_SUBSCRIPT.has(text[index]) || UNICODE_SUPERSCRIPT.has(text[index])) {
      while (UNICODE_SUBSCRIPT.has(text[index]) || UNICODE_SUPERSCRIPT.has(text[index])) index += 1;
      hasScript = true;
    } else if (text[index] === "'" || text[index] === "′" || text[index] === "°") {
      index += 1;
      hasScript = true;
    } else {
      break;
    }
  }
  return hasScript ? index : -1;
}

function findBareEnvironment(text, start) {
  const opening = /^\\begin\{([A-Za-z]+\*?)\}/.exec(text.slice(start));
  if (!opening || !BARE_ENVIRONMENTS.has(opening[1])) return null;
  const environment = opening[1];
  const close = `\\end{${environment}}`;
  const contentStart = start + opening[0].length;
  const closeIndex = findClose(text, contentStart, close);
  if (closeIndex === -1) return null;
  const end = closeIndex + close.length;
  let tex = text.slice(contentStart, closeIndex);
  const replacements = {
    "math": null, "equation": null, "equation*": null, "displaymath": null,
    "align": "aligned", "align*": "aligned", "flalign": "aligned", "flalign*": "aligned",
    "alignat": "alignedat", "alignat*": "alignedat",
    "gather": "gathered", "gather*": "gathered", "multline": "gathered", "multline*": "gathered",
  };
  const replacement = Object.hasOwn(replacements, environment) ? replacements[environment] : environment;
  if (replacement) tex = `\\begin{${replacement}}${tex}\\end{${replacement}}`;
  return { end, tex, display: environment !== "math" };
}

function normalizeMathEnvironment(tex) {
  const match = /^\s*\\begin\{([A-Za-z]+\*?)\}([\s\S]*?)\\end\{\1\}\s*$/.exec(tex);
  if (!match) return { tex, display: false };
  const [, environment, body] = match;
  const replacements = {
    math: null, equation: null, "equation*": null, displaymath: null,
    align: "aligned", "align*": "aligned", flalign: "aligned", "flalign*": "aligned",
    alignat: "alignedat", "alignat*": "alignedat",
    gather: "gathered", "gather*": "gathered", multline: "gathered", "multline*": "gathered",
  };
  const replacement = Object.hasOwn(replacements, environment) ? replacements[environment] : environment;
  if (!replacement) return { tex: body, display: environment !== "math" };
  return {
    tex: `\\begin{${replacement}}${body}\\end{${replacement}}`,
    display: true
  };
}

function findBareEquationEnd(text, start) {
  if (!/[\p{L}\p{N}\\∂∇∫∑√]/u.test(text[start] ?? "")) return -1;
  if (start > 0 && /[\p{L}\p{N}_]/u.test(text[start - 1])) return -1;
  let index = start;
  let hasOperator = false;
  let hasValue = false;
  while (index < text.length) {
    const char = text[index];
    if (char === "\n") break;
    if (char === "\\") {
      const commandEnd = findBareTexCommandEnd(text, index);
      if (commandEnd === -1) break;
      index = commandEnd;
      hasValue = true;
      continue;
    }
    if (/[\p{L}\p{N}]+/u.test(char)) {
      const token = /^[\p{L}\p{N}]+/u.exec(text.slice(index))[0];
      const word = token.toLocaleLowerCase();
      if (PROSE_WORDS.has(word) || (token.length > 3 && !MATH_WORDS.has(word))) break;
      index += token.length;
      hasValue = true;
      continue;
    }
    if (/[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ᵢₙ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ]/u.test(char)) {
      index += 1;
      hasValue = true;
      continue;
    }
    if (/\s/.test(char) || "{}_^.,'′+-−=*/×÷<>≤≥≠≈≡∝()[]|:∂∇∫∑√∞".includes(char) || UNICODE_TEX.has(char)) {
      if ("=+-−=*/×÷<>≤≥≠≈≡∝".includes(char)) hasOperator = true;
      index += 1;
      continue;
    }
    break;
  }
  const candidate = text.slice(start, index).trim().replace(/[.,;:]+$/, "");
  if (!hasOperator || !hasValue || candidate.length < 3) return -1;
  return start + candidate.length;
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

// A missing closing `$` can otherwise consume the rest of a prose paragraph and
// close on a later, unrelated formula. Treat unmistakable prose as an unclosed
// expression so the prose stays readable and later formulas can still render.
// Multi-letter names remain valid inside groups (for example `x_{max}` and
// `\\text{kg}`), while ordinary words outside groups are not math notation.
function looksLikeInlineMath(tex) {
  let depth = 0;
  for (let index = 0; index < tex.length; ) {
    const char = tex[index];
    if (char === "\\") {
      index += 1;
      if (/[A-Za-z]/.test(tex[index] || "")) {
        while (/[A-Za-z]/.test(tex[index] || "")) index += 1;
      } else if (index < tex.length) {
        index += 1;
      }
      continue;
    }
    if (char === "{") depth += 1;
    else if (char === "}") depth = Math.max(0, depth - 1);

    if (depth === 0 && /[A-Za-z]/.test(char)) {
      let end = index + 1;
      while (/[A-Za-z]/.test(tex[end] || "")) end += 1;
      if (end - index >= 4) return false;
      index = end;
      continue;
    }
    index += 1;
  }
  return true;
}

export function splitMath(text) {
  const source = decodeMathEntities(String(text ?? ""));
  const segments = [];
  let plain = "";
  let mathCount = 0;
  let suppressBareTex = false;

  const flush = () => {
    if (plain) segments.push({ type: "text", value: plain });
    plain = "";
  };
  const pushMath = (raw, tex, display) => {
    const environment = normalizeMathEnvironment(normalizeBareMath(tex.trim()));
    const cleaned = environment.tex.trim();
    if (!cleaned || cleaned.length > MAX_SOURCE_LENGTH || mathCount >= MAX_SEGMENTS) return false;
    flush();
    segments.push({
      type: "math",
      raw,
      tex: cleaned.replace(/\s*\n\s*/g, " "),
      display: display || environment.display
    });
    mathCount += 1;
    return true;
  };

  for (let index = 0; index < source.length; ) {
    const fence = source.startsWith("```", index) ? findMathFence(source, index) : null;
    if (fence) {
      if (pushMath(fence.raw, normalizeBareMath(stripMathDelimiters(fence.tex)), true)) {
        index = fence.end;
      } else {
        plain += fence.raw;
        index = fence.end;
      }
    } else if (source[index] === "<") {
      const tag = /^<\/?[A-Za-z][^>]*>/.exec(source.slice(index));
      if (tag) {
        plain += tag[0];
        index += tag[0].length;
      } else {
        plain += source[index];
        index += 1;
      }
    } else if (source.startsWith("\\$", index)) {
      plain += "$";
      index += 2;
    } else if (source[index] === "`") {
      const codeSpan = findInlineCodeSpan(source, index);
      if (codeSpan) {
        flush();
        segments.push({ type: "code", value: codeSpan.value });
        index = codeSpan.end;
      } else {
        plain += source[index];
        index += 1;
      }
    } else if (source.startsWith("\\begin{", index)) {
      const environment = findBareEnvironment(source, index);
      if (environment && pushMath(source.slice(index, environment.end), environment.tex, true)) {
        index = environment.end;
      } else {
        plain += source[index];
        index += 1;
      }
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
      const tex = end === -1 ? "" : source.slice(index + 1, end);
      if (end !== -1 && looksLikeInlineMath(tex) && pushMath(source.slice(index, end + 1), tex, false)) {
        suppressBareTex = false;
        index = end + 1;
      } else {
        if (end !== -1 && tex.includes("\\")) suppressBareTex = true;
        else if (suppressBareTex && end === -1) suppressBareTex = false;
        plain += "$";
        index += 1;
      }
    } else if (source.startsWith("\\\\", index) && /[A-Za-z]/.test(source[index + 2] || "")) {
      const commandStart = index + 1;
      const equationEnd = suppressBareTex ? -1 : findBareEquationEnd(source, commandStart);
      const commandEnd = equationEnd === -1 ? findBareTexCommandEnd(source, commandStart) : equationEnd;
      const tex = commandEnd === -1 ? "" : source.slice(commandStart, commandEnd);
      if (!suppressBareTex && commandEnd !== -1
        && pushMath(source.slice(index, commandEnd), normalizeBareMath(tex), false)) {
        index = commandEnd;
      } else {
        plain += source[index];
        index += 1;
      }
    } else if (source[index] === "\\") {
      const equationEnd = suppressBareTex ? -1 : findBareEquationEnd(source, index);
      const commandEnd = equationEnd === -1 ? findBareTexCommandEnd(source, index) : equationEnd;
      const tex = source.slice(index, commandEnd);
      if (!suppressBareTex && commandEnd !== -1 && pushMath(source.slice(index, commandEnd), normalizeBareMath(tex), false)) {
        index = commandEnd;
      } else {
        plain += source[index];
        index += 1;
      }
    } else if (!suppressBareTex) {
      const equationEnd = findBareEquationEnd(source, index);
      const scriptEnd = equationEnd === -1 ? findBareScriptEnd(source, index) : -1;
      const bareEnd = equationEnd !== -1 ? equationEnd : scriptEnd;
      const bare = bareEnd === -1 ? "" : source.slice(index, bareEnd);
      const unicodeMath = UNICODE_TEX.has(source[index]) && !isSpace(source[index]);
      if (bareEnd !== -1 && pushMath(bare, normalizeBareMath(bare), false)) {
        index = bareEnd;
      } else if (unicodeMath && pushMath(source[index], normalizeBareMath(source[index]), false)) {
        index += 1;
      } else {
        plain += source[index];
        if (source[index] === "\n") suppressBareTex = false;
        index += 1;
      }
    } else {
      plain += source[index];
      if (source[index] === "\n") suppressBareTex = false;
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
      if (segment.type === "code") return `<code class="guide-inline-code">${escapeHtml(segment.value)}</code>`;
      return `<span class="tex-math${segment.display ? " is-display" : ""}" data-display="${segment.display ? "1" : "0"}" data-tex="${escapeHtml(segment.tex)}">${escapeHtml(segment.raw)}</span>`;
    })
    .join("");
}

// Formula fields are intended to be displayed as equations, but models sometimes
// include their own delimiters. Strip one complete wrapper before applying ours.
export function displayMathHtml(text) {
  const expression = stripMathDelimiters(text);
  if (!expression) return "";
  const environment = findBareEnvironment(expression, 0);
  if (environment?.end === expression.length) return mathHtml(expression);
  const fence = findMathFence(expression, 0);
  if (fence?.end === expression.length) return mathHtml(expression);
  return mathHtml(`\\[${expression}\\]`);
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
