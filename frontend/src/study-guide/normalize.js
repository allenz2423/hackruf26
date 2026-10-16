import {
  ANIMAL_CELL_ORGANELLES,
  animalCellAltText,
  animalCellFallback
} from "./animal-cell-data.js";
import { samplingDemoAltText, samplingDemoFallback } from "./sampling-demo.js";
import { normalizePythonAlgorithmBlock } from "./libraries/python-activity-contract.js";
import { sanitizeSvgMarkup, svgHasAnimation } from "./safe-svg.js";

const MAX_BLOCK_TEXT = 10_000;

function boundedText(value, maxLength = MAX_BLOCK_TEXT) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maxLength;
}

function blockId(value, fallback) {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value) ? value : fallback;
}

function normalizedSources(value, fallback) {
  const sources = Array.isArray(value) ? value : fallback;
  return Array.isArray(sources) ? sources.filter((source) => source && typeof source === "object").slice(0, 20) : [];
}

function finiteBetween(value, minimum, maximum) {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function matchesStep(value, decimalPlaces) {
  return Number.isFinite(value) && Math.abs(value - Number(value.toFixed(decimalPlaces))) < 1e-8;
}

function unavailableBlock(block, id, sources) {
  if (!boundedText(block.fallback_text, 5_000)) return null;
  return { id, type: "unavailable", text: block.fallback_text, sources };
}

function normalizeBlock(block, section, sectionIndex, blockIndex) {
  if (!block || typeof block !== "object" || Array.isArray(block)) return null;
  const id = blockId(block.id, `section-${sectionIndex + 1}-block-${blockIndex + 1}`);
  const sources = normalizedSources(block.sources, section.sources);

  if (block.type === "paragraph" || block.type === "takeaway") {
    if (!boundedText(block.text)) return null;
    return { id, type: block.type, text: block.text, sources };
  }
  if (block.type === "formula") {
    if (!boundedText(block.expression, 2_000)) return null;
    const definitions = Array.isArray(block.definitions) ? block.definitions : [];
    if (definitions.length > 20 || definitions.some((item) =>
      !item || typeof item !== "object" || !boundedText(item.symbol, 32)
      || !boundedText(item.meaning, 300)
      || (item.unit != null && (typeof item.unit !== "string" || item.unit.length > 32)))) return null;
    return { id, type: "formula", expression: block.expression, definitions, sources };
  }
  if (block.type === "example_steps") {
    if (!boundedText(block.title, 200) || !Array.isArray(block.steps) || block.steps.length < 2 || block.steps.length > 12) return null;
    const steps = block.steps.map((step) => {
      if (!step || typeof step !== "object" || Array.isArray(step)
        || !boundedText(step.title, 160) || !boundedText(step.explanation, 2_000)
        || (step.expression != null && !boundedText(step.expression, 500))) return null;
      return { title: step.title, explanation: step.explanation, expression: step.expression || "" };
    });
    if (steps.some((step) => step === null)
      || steps.reduce((total, step) => total + step.title.length + step.explanation.length + step.expression.length, 0) > 4_000) return null;
    return { id, type: "example_steps", title: block.title, steps, sources };
  }
  if (block.type === "svg_diagram") {
    if (!boundedText(block.title, 200) || !boundedText(block.alt_text, 500)
      || !boundedText(block.fallback_text, 5_000)
      || typeof block.svg !== "string" || block.svg.length > 60_000) return null;
    const svgMarkup = sanitizeSvgMarkup(block.svg, { idPrefix: `normalized-${id}`, altText: block.alt_text });
    if (!svgMarkup) return unavailableBlock(block, id, sources);
    return {
      id, type: "svg_diagram", title: block.title, svg_markup: svgMarkup,
      alt_text: block.alt_text, fallback_text: block.fallback_text,
      animated: svgHasAnimation(svgMarkup), sources
    };
  }
  if (block.type === "labeled_diagram") {
    const moleculeId = block.parameters?.molecule_id;
    if (block.template_id === "process_pathway_v1") {
      const stages = block.parameters?.stages;
      if (!Array.isArray(stages) || stages.length < 2 || stages.length > 12) return null;
      const normalizedStages = stages.map((stage) => {
        if (!stage || typeof stage !== "object" || Array.isArray(stage)
          || typeof stage.id !== "string" || !/^[a-zA-Z0-9_-]{1,40}$/.test(stage.id)
          || !boundedText(stage.title, 100) || !boundedText(stage.explanation, 600)) return null;
        return { id: stage.id, title: stage.title, explanation: stage.explanation };
      });
      if (normalizedStages.some((stage) => stage === null)
        || new Set(normalizedStages.map((stage) => stage.id)).size !== normalizedStages.length
        || normalizedStages.reduce((total, stage) => total + stage.title.length + stage.explanation.length, 0) > 4_000) return null;
      return {
        id, type: "labeled_diagram", template_id: "process_pathway_v1", stages: normalizedStages,
        title: boundedText(block.title, 200) ? block.title : "Explore this process",
        alt_text: boundedText(block.alt_text, 500) ? block.alt_text : "An ordered process pathway with selectable stages.",
        fallback_text: boundedText(block.fallback_text, 5_000) ? block.fallback_text : section.body,
        sources
      };
    }
    if (block.template_id === "animal_cell_map_v1") {
      const focusIds = block.parameters?.focus_ids;
      if (!Array.isArray(focusIds) || focusIds.length < 1 || focusIds.length > 9
        || focusIds.some((item) => typeof item !== "string" || !Object.hasOwn(ANIMAL_CELL_ORGANELLES, item))
        || new Set(focusIds).size !== focusIds.length) return null;
      return {
        id, type: "labeled_diagram", template_id: "animal_cell_map_v1", focus_ids: [...focusIds],
        title: boundedText(block.title, 200) ? block.title : "Explore an animal cell",
        alt_text: animalCellAltText(focusIds),
        fallback_text: animalCellFallback(focusIds),
        sources
      };
    }
    if (block.template_id === "molecule_3d_v1") {
      if (!["water", "carbon_dioxide", "methane", "ammonia"].includes(moleculeId)) {
        return unavailableBlock(block, id, sources);
      }
      return {
        id, type: "labeled_diagram", template_id: "molecule_3d_v1", molecule_id: moleculeId,
        title: boundedText(block.title, 200) ? block.title : "Explore a 3D molecular model",
        alt_text: boundedText(block.alt_text, 500) ? block.alt_text : "A rotatable idealized 3D molecular geometry.",
        fallback_text: boundedText(block.fallback_text, 5_000) ? block.fallback_text : section.body,
        sources
      };
    }
    if (block.template_id !== "molecule_2d_v1" || !["water", "carbon_dioxide", "methane", "ammonia"].includes(moleculeId)) {
      return unavailableBlock(block, id, sources);
    }
    return {
      id, type: "labeled_diagram", template_id: "molecule_2d_v1", molecule_id: moleculeId,
      title: boundedText(block.title, 200) ? block.title : "Explore a 2D molecular model",
      alt_text: boundedText(block.alt_text, 500) ? block.alt_text : "An app-provided two-dimensional molecular model.",
      fallback_text: boundedText(block.fallback_text, 5_000) ? block.fallback_text : section.body,
      sources
    };
  }
  if (block.type === "interactive_model") {
    const parameters = block.parameters;
    let normalizedParameters;
    let pythonTitle = null;
    let pythonAltText = null;
    if (block.template_id === "force_components_v1") {
      if (!finiteBetween(parameters?.force_n, 0, 1000) || !matchesStep(parameters.force_n, 1)
        || !finiteBetween(parameters?.angle_deg, 0, 90) || !matchesStep(parameters.angle_deg, 1)) return null;
      normalizedParameters = { force_n: parameters.force_n, angle_deg: parameters.angle_deg };
    } else if (block.template_id === "linear_graph_v1") {
      if (!finiteBetween(parameters?.slope, -10, 10) || !matchesStep(parameters.slope, 1)
        || !finiteBetween(parameters?.intercept, -10, 10) || !matchesStep(parameters.intercept, 1)) return null;
      normalizedParameters = { slope: parameters.slope, intercept: parameters.intercept };
    } else if (block.template_id === "first_order_response_v1") {
      if (!finiteBetween(parameters?.initial_value, -1000, 1000)
        || !Number.isInteger(parameters.initial_value)
        || !finiteBetween(parameters?.final_value, -1000, 1000)
        || !Number.isInteger(parameters.final_value)
        || !finiteBetween(parameters?.time_constant_s, 0.01, 3600)
        || !matchesStep(parameters.time_constant_s, 2)) return null;
      normalizedParameters = {
        initial_value: parameters.initial_value,
        final_value: parameters.final_value,
        time_constant_s: parameters.time_constant_s
      };
    } else if (block.template_id === "resistor_divider_v1") {
      if (!finiteBetween(parameters?.voltage_v, 0, 24)
        || !matchesStep(parameters.voltage_v, 1)
        || !finiteBetween(parameters?.r1_ohm, 1, 1_000_000)
        || !Number.isInteger(parameters.r1_ohm)
        || !finiteBetween(parameters?.r2_ohm, 1, 1_000_000)
        || !Number.isInteger(parameters.r2_ohm)) return null;
      normalizedParameters = {
        voltage_v: parameters.voltage_v, r1_ohm: parameters.r1_ohm, r2_ohm: parameters.r2_ohm
      };
    } else if (block.template_id === "binary_search_trace_v1") {
      const values = parameters?.values;
      const target = parameters?.target;
      if (!Array.isArray(values) || values.length < 2 || values.length > 20
        || values.some((value) => !Number.isInteger(value) || value < -10_000 || value > 10_000)
        || values.some((value, index) => index > 0 && values[index - 1] >= value)
        || !Number.isInteger(target) || target < -10_000 || target > 10_000) return null;
      normalizedParameters = { values: [...values], target };
    } else if (block.template_id === "sampling_demo_v1") {
      if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)
        || Object.keys(parameters).some((key) => !["method", "sample_size", "seed", "random_assignment"].includes(key))) return null;
      const method = parameters?.method;
      const sampleSize = parameters?.sample_size;
      const seed = parameters?.seed;
      const randomAssignment = parameters.random_assignment === undefined ? false : parameters.random_assignment;
      if (!["simple_random", "stratified", "front_row_convenience"].includes(method)
        || !Number.isInteger(sampleSize) || sampleSize < 4 || sampleSize > 12 || sampleSize % 2 !== 0
        || !Number.isInteger(seed) || seed < 1 || seed > 9999
        || typeof randomAssignment !== "boolean") return null;
      normalizedParameters = {
        method, sample_size: sampleSize, seed, random_assignment: randomAssignment
      };
    } else if (block.template_id === "graph_traversal_v1") {
      const graph = parameters;
      if (!graph || typeof graph !== "object" || Array.isArray(graph)
        || Object.keys(graph).some((key) => !["algorithm", "start_node", "nodes", "edges"].includes(key))) return null;
      const nodes = graph.nodes;
      const edges = graph.edges;
      if (!Array.isArray(nodes) || nodes.length < 2 || nodes.length > 24
        || !Array.isArray(edges) || edges.length < 1 || edges.length > 48) return null;
      const nodeIds = new Set();
      const normalizedNodes = nodes.map((node) => {
        if (!node || typeof node !== "object" || Array.isArray(node)
          || typeof node.id !== "string" || !/^[A-Za-z0-9_-]{1,40}$/u.test(node.id)
          || nodeIds.has(node.id) || !boundedText(node.label, 80)
          || (node.description !== undefined && node.description !== "" && !boundedText(node.description, 240))) return null;
        nodeIds.add(node.id);
        return { id: node.id, label: node.label.trim(), description: typeof node.description === "string" ? node.description.trim() : "" };
      });
      if (normalizedNodes.some((node) => node === null)
        || !["bfs", "dfs"].includes(graph.algorithm)
        || typeof graph.start_node !== "string" || !nodeIds.has(graph.start_node)) return null;
      const edgeIds = new Set();
      const normalizedEdges = edges.map((edge) => {
        if (!edge || typeof edge !== "object" || Array.isArray(edge)
          || typeof edge.source !== "string" || typeof edge.target !== "string"
          || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return null;
        const pair = `${edge.source}\u0000${edge.target}`;
        if (edgeIds.has(pair)) return null;
        edgeIds.add(pair);
        return { source: edge.source, target: edge.target };
      });
      if (normalizedEdges.some((edge) => edge === null)) return null;
      normalizedParameters = {
        algorithm: graph.algorithm,
        start_node: graph.start_node,
        nodes: normalizedNodes,
        edges: normalizedEdges
      };
    } else if (block.template_id === "stress_cube_3d_v1") {
      const matrix = parameters?.stress_mpa;
      if (!Array.isArray(matrix) || matrix.length !== 3
        || matrix.some((row) => !Array.isArray(row) || row.length !== 3
          || row.some((value) => !finiteBetween(value, -1000, 1000)))) return null;
      if (Math.abs(matrix[0][1] - matrix[1][0]) > 1e-8
        || Math.abs(matrix[0][2] - matrix[2][0]) > 1e-8
        || Math.abs(matrix[1][2] - matrix[2][1]) > 1e-8) return null;
      normalizedParameters = { stress_mpa: matrix.map((row) => [...row]) };
    } else if (block.template_id === "python_algorithm_trace_v1") {
      try {
        const normalized = normalizePythonAlgorithmBlock(block);
        normalizedParameters = normalized.parameters;
        pythonTitle = normalized.title;
        pythonAltText = normalized.alt_text;
      } catch {
        return null;
      }
    } else if (block.template_id === "simcir_circuit_v1") {
      const circuitId = parameters?.circuit_id;
      if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)
        || Object.keys(parameters).some((key) => key !== "circuit_id")
        || !["and_gate", "or_gate", "half_adder"].includes(circuitId)) return null;
      normalizedParameters = { circuit_id: circuitId };
    } else {
      return unavailableBlock(block, id, sources);
    }
    const normalizedBlock = {
      id,
      type: "interactive_model",
      template_id: block.template_id,
      title: pythonTitle || (boundedText(block.title, 200) ? block.title : "Explore this model"),
      parameters: normalizedParameters,
      alt_text: pythonAltText || (boundedText(block.alt_text, 500) ? block.alt_text : "An interactive educational model."),
      fallback_text: boundedText(block.fallback_text, 5_000) ? block.fallback_text : section.body,
      sources
    };
    if (block.template_id === "sampling_demo_v1") {
      normalizedBlock.alt_text = samplingDemoAltText();
      normalizedBlock.fallback_text = samplingDemoFallback(
        normalizedParameters.method,
        normalizedParameters.sample_size,
        normalizedParameters.seed,
        normalizedParameters.random_assignment
      );
    }
    return normalizedBlock;
  }
  return null;
}

/** Adapt legacy guides to a paragraph block and fail closed to the section fallback. */
export function normalizeGuideBlocks(section, sectionIndex = 0) {
  if (!section || typeof section !== "object") return [];
  const fallback = boundedText(section.body, 20_000) ? section.body : "This section is available as text in the saved guide.";
  const fallbackSources = normalizedSources(section.sources, []);
  if (!Array.isArray(section.blocks) || section.blocks.length === 0) {
    return [{ id: `section-${sectionIndex + 1}-legacy`, type: "paragraph", text: fallback, sources: fallbackSources }];
  }
  if (section.blocks.length > 24) {
    return [{ id: `section-${sectionIndex + 1}-fallback`, type: "paragraph", text: fallback, sources: fallbackSources }];
  }
  const blocks = section.blocks.map((block, index) => normalizeBlock(block, section, sectionIndex, index));
  if (blocks.some((block) => block === null)) {
    return [{ id: `section-${sectionIndex + 1}-fallback`, type: "paragraph", text: fallback, sources: fallbackSources }];
  }
  return blocks;
}
