const MIN_STAGES = 2;
const MAX_STAGES = 12;
const MAX_TITLE_LENGTH = 100;
const STAGE_ID_PATTERN = /^[a-zA-Z0-9_-]{1,40}$/;

let mermaidImport;
let renderSequence = 0;

const MERMAID_LABEL_REPLACEMENTS = new Map([
  ["\\", "＼"],
  ["[", "［"], ["]", "］"],
  ["{", "｛"], ["}", "｝"],
  ["(", "（"], [")", "）"],
  ["<", "‹"], [">", "›"],
  ["|", "｜"], ["#", "＃"], ["&", "＆"],
  ["\"", "＂"], ["'", "＇"], ["`", "｀"],
  [";", "；"], [":", "："]
]);

function validateStages(stages) {
  if (!Array.isArray(stages) || stages.length < MIN_STAGES || stages.length > MAX_STAGES) {
    throw new RangeError(`A process flow needs ${MIN_STAGES} to ${MAX_STAGES} stages.`);
  }

  const seenIds = new Set();
  return stages.map((stage, index) => {
    if (!stage || typeof stage !== "object" || Array.isArray(stage)
      || typeof stage.id !== "string" || !STAGE_ID_PATTERN.test(stage.id)
      || seenIds.has(stage.id)) {
      throw new TypeError(`Stage ${index + 1} needs a unique, valid stage ID.`);
    }
    if (typeof stage.title !== "string" || stage.title.trim().length === 0
      || stage.title.length > MAX_TITLE_LENGTH) {
      throw new TypeError(`Stage ${index + 1} needs a title of 1 to ${MAX_TITLE_LENGTH} characters.`);
    }
    seenIds.add(stage.id);
    return { id: stage.id, title: sanitizeMermaidLabel(stage.title) };
  });
}

function sanitizeMermaidLabel(title) {
  return title
    .replace(/[\u0000-\u001f\u007f-\u009f]/gu, " ")
    .replace(/\\|[\[\]{}()<>&|#"'`;:]/gu, (character) => MERMAID_LABEL_REPLACEMENTS.get(character))
    .replace(/\s+/gu, " ")
    .trim();
}

/**
 * Build a constrained Mermaid flowchart from the same ordered stage IDs and
 * titles used by process_pathway_v1. No model-provided Mermaid source is read.
 */
export function buildMermaidFlowchart(stages) {
  const safeStages = validateStages(stages);
  const nodeId = (index) => `stage_${index}`;
  const nodes = safeStages.map(({ title }, index) => `  ${nodeId(index)}["${title}"]`);
  const edges = safeStages.slice(1).map((_, index) => `  ${nodeId(index)} --> ${nodeId(index + 1)}`);
  return ["flowchart TD", ...nodes, ...edges].join("\n");
}

function stageOrderText(stages) {
  if (!Array.isArray(stages)) return "";
  const titles = stages.slice(0, MAX_STAGES)
    .map((stage) => (typeof stage?.title === "string" ? stage.title.replace(/\s+/gu, " ").trim().slice(0, MAX_TITLE_LENGTH) : ""))
    .filter(Boolean);
  return titles.length ? ` Ordered stages: ${titles.join(" → ")}.` : " Review the ordered stage list in the guide.";
}

function clearContainer(container) {
  if (typeof container?.replaceChildren === "function") container.replaceChildren();
  else if (container && "innerHTML" in container) container.innerHTML = "";
}

function failedHandle(container, error, stages) {
  const fallbackText = `Interactive process flowchart unavailable.${stageOrderText(stages)}`;
  clearContainer(container);
  let cleaned = false;
  return {
    fallbackText,
    ready: Promise.resolve({ ok: false, error, fallbackText }),
    cleanup() {
      if (cleaned) return;
      cleaned = true;
      clearContainer(container);
    }
  };
}

async function loadMermaid() {
  if (!mermaidImport) {
    mermaidImport = import("mermaid").then((module) => {
      const mermaid = module.default;
      if (!mermaid?.initialize || !mermaid?.render) throw new Error("Mermaid's rendering API is unavailable.");
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
        flowchart: { htmlLabels: false, useMaxWidth: true, curve: "linear" }
      });
      return mermaid;
    });
  }
  return mermaidImport;
}

/**
 * Lazily render a process pathway. `ready` resolves with the render outcome;
 * callers should reveal the diagram target only when `ok` is true. On failure,
 * `fallbackText` describes the ordered stages for use alongside the text list.
 * `cleanup()` prevents late rendering and clears the mounted diagram.
 */
export function mountMermaidFlow(container, stages) {
  let source;
  try {
    if (!container || !("innerHTML" in container) || !("textContent" in container)) {
      throw new TypeError("A DOM container is required to render the process flowchart.");
    }
    source = buildMermaidFlowchart(stages);
  } catch (error) {
    return failedHandle(container, error, stages);
  }

  let cancelled = false;
  const fallbackText = `Interactive process flowchart unavailable.${stageOrderText(stages)}`;

  const ready = (async () => {
    try {
      const mermaid = await loadMermaid();
      if (cancelled) return { ok: false, cancelled: true };
      const renderId = `studyGuideFlow${++renderSequence}`;
      const { svg } = await mermaid.render(renderId, source);
      if (cancelled) return { ok: false, cancelled: true };
      container.innerHTML = svg;
      return { ok: true };
    } catch (error) {
      if (!cancelled) clearContainer(container);
      return { ok: false, error, fallbackText };
    }
  })();

  return {
    fallbackText,
    ready,
    cleanup() {
      if (cancelled) return;
      cancelled = true;
      clearContainer(container);
    }
  };
}
