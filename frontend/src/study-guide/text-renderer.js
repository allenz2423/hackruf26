import { escapeHtml } from "../ui.js";
import { displayMathHtml, mathHtml } from "../math.js";

const MATH_FENCE_LANGUAGES = new Set(["math", "latex", "tex", "equation"]);

function renderCodeLines(source) {
  return source.split("\n").map((line, index) => `<span class="guide-code-line"><span class="guide-code-line-number" aria-hidden="true">${index + 1}</span><span class="guide-code-line-text">${escapeHtml(line) || " "}</span></span>`).join("");
}

function renderCodeBlock(source, language) {
  const label = language || "Text";
  const extension = language || "txt";
  return `<div class="guide-code-window" role="group" aria-label="${escapeHtml(label)} code example">
    <div class="guide-code-toolbar" aria-hidden="true"><span class="python-code-window-dots"><i></i><i></i><i></i></span><span class="python-code-filename">example.${escapeHtml(extension)}</span><span class="python-code-language">${escapeHtml(label)}</span><span class="python-code-readonly">Read only</span></div>
    <pre class="guide-code-pre"><code>${renderCodeLines(source)}</code></pre>
  </div>`;
}

function openingFence(line) {
  const match = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match || (match[2][0] === "`" && match[3].includes("`"))) return null;
  return { marker: match[2][0], length: match[2].length, info: match[3].trim() };
}

function isClosingFence(line, fence) {
  const marker = fence.marker === "`" ? "`" : "~";
  return new RegExp(`^ {0,3}${marker}{${fence.length},}[ \\t]*$`).test(line);
}

function fenceLanguage(info) {
  const rawLanguage = info.split(/\s+/, 1)[0] || "";
  return /^[\w.+#-]{1,24}$/.test(rawLanguage) ? rawLanguage.toLowerCase() : "";
}

/** Render study-guide paragraphs and Markdown fenced code as safe HTML. */
export function renderStudyGuideText(value) {
  const lines = String(value ?? "").split(/\r?\n/u);
  const output = [];
  let paragraph = [];

  const flushParagraph = () => {
    const content = paragraph.join("\n").trim();
    paragraph = [];
    if (content) output.push(`<p>${mathHtml(content).replace(/\n/g, "<br>")}</p>`);
  };

  for (let index = 0; index < lines.length; index += 1) {
    const fence = openingFence(lines[index]);
    if (fence) {
      flushParagraph();
      const codeLines = [];
      let foundClosingFence = false;
      while (++index < lines.length) {
        if (isClosingFence(lines[index], fence)) {
          foundClosingFence = true;
          break;
        }
        codeLines.push(lines[index]);
      }
      const language = fenceLanguage(fence.info);
      const code = codeLines.join("\n");
      if (MATH_FENCE_LANGUAGES.has(language)) {
        output.push(`<div class="guide-fenced-math">${displayMathHtml(code)}</div>`);
      } else {
        output.push(renderCodeBlock(code, language));
      }
      if (!foundClosingFence) break;
    } else if (!lines[index].trim()) {
      flushParagraph();
    } else {
      paragraph.push(lines[index]);
    }
  }

  flushParagraph();
  return output.join("");
}
