const MIN_NODES = 2;
const MAX_NODES = 24;
const MIN_EDGES = 1;
const MAX_EDGES = 48;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/u;
const MAX_TITLE_LENGTH = 100;
const MAX_ALT_LENGTH = 500;
const MAX_FALLBACK_LENGTH = 500;
const MAX_NODE_LABEL_LENGTH = 80;
const MAX_NODE_DESCRIPTION_LENGTH = 240;

function isRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function requiredText(value, field, maxLength, errorType = TypeError) {
  if (typeof value !== "string") throw new errorType(`${field} must be text.`);
  const text = value.trim();
  if (!text || text.length > maxLength || /[\u0000-\u001f\u007f-\u009f]/u.test(text)) {
    throw new errorType(`${field} must contain 1 to ${maxLength} printable characters.`);
  }
  return text;
}

function compareIds(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

/**
 * Validate and copy the app-owned graph-traversal payload. Unknown properties
 * are deliberately ignored; no Cytoscape options, selectors, styles, or layout
 * configuration are accepted from a guide payload.
 */
export function normalizeGraphTraversalParameters(parameters = {}) {
  if (!isRecord(parameters)) throw new TypeError("Graph activity parameters must be an object.");

  const { nodes, edges } = parameters;
  if (!Array.isArray(nodes) || nodes.length < MIN_NODES || nodes.length > MAX_NODES) {
    throw new RangeError(`A graph needs ${MIN_NODES} to ${MAX_NODES} nodes.`);
  }
  if (!Array.isArray(edges) || edges.length < MIN_EDGES || edges.length > MAX_EDGES) {
    throw new RangeError(`A graph needs ${MIN_EDGES} to ${MAX_EDGES} edges.`);
  }

  const nodeIds = new Set();
  const normalizedNodes = nodes.map((node, index) => {
    if (!isRecord(node) || typeof node.id !== "string" || !ID_PATTERN.test(node.id)
      || nodeIds.has(node.id)) {
      throw new TypeError(`Node ${index + 1} needs a unique ID containing 1 to 40 letters, numbers, underscores, or hyphens.`);
    }
    nodeIds.add(node.id);
    const label = requiredText(node.label, `Node ${index + 1} label`, MAX_NODE_LABEL_LENGTH);
    const description = node.description === undefined || node.description === ""
      ? ""
      : requiredText(node.description, `Node ${index + 1} description`, MAX_NODE_DESCRIPTION_LENGTH);
    return { id: node.id, label, description };
  });

  const edgePairs = new Set();
  const normalizedEdges = edges.map((edge, index) => {
    if (!isRecord(edge) || typeof edge.source !== "string" || typeof edge.target !== "string"
      || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      throw new TypeError(`Edge ${index + 1} must connect two declared node IDs.`);
    }
    const pair = `${edge.source}\u0000${edge.target}`;
    if (edgePairs.has(pair)) throw new TypeError(`Edge ${index + 1} duplicates an existing directed edge.`);
    edgePairs.add(pair);
    return { source: edge.source, target: edge.target };
  });

  const algorithm = parameters.algorithm === undefined ? "bfs" : parameters.algorithm;
  if (algorithm !== "bfs" && algorithm !== "dfs") {
    throw new TypeError('Graph traversal algorithm must be "bfs" or "dfs".');
  }
  if (typeof parameters.start_node !== "string" || !nodeIds.has(parameters.start_node)) {
    throw new TypeError("The start node must match a declared node ID.");
  }

  return {
    algorithm,
    start_node: parameters.start_node,
    nodes: normalizedNodes,
    edges: normalizedEdges
  };
}

function adjacencyFor(graph) {
  const adjacency = new Map(graph.nodes.map(({ id }) => [id, []]));
  for (const { source, target } of graph.edges) adjacency.get(source).push(target);
  for (const neighbors of adjacency.values()) neighbors.sort(compareIds);
  return adjacency;
}

/** Return a deterministic BFS or recursive-preorder DFS traversal. */
export function buildGraphTraversal(parameters = {}) {
  const graph = normalizeGraphTraversalParameters(parameters);
  const adjacency = adjacencyFor(graph);
  const visits = [];
  const visited = new Set();

  if (graph.algorithm === "bfs") {
    const queue = [{ nodeId: graph.start_node, parentId: null, depth: 0 }];
    visited.add(graph.start_node);
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      visits.push(current);
      for (const neighborId of adjacency.get(current.nodeId)) {
        if (visited.has(neighborId)) continue;
        visited.add(neighborId);
        queue.push({ nodeId: neighborId, parentId: current.nodeId, depth: current.depth + 1 });
      }
    }
  } else {
    const visitDepthFirst = (nodeId, parentId, depth) => {
      if (visited.has(nodeId)) return;
      visited.add(nodeId);
      visits.push({ nodeId, parentId, depth });
      for (const neighborId of adjacency.get(nodeId)) {
        visitDepthFirst(neighborId, nodeId, depth + 1);
      }
    };
    visitDepthFirst(graph.start_node, null, 0);
  }

  return {
    algorithm: graph.algorithm,
    start_node: graph.start_node,
    visits,
    unreachable_node_ids: graph.nodes
      .map(({ id }) => id)
      .filter((id) => !visited.has(id))
      .sort(compareIds)
  };
}

/** Describe a selected node using directed outgoing edges in stable ID order. */
export function selectGraphNode(parameters, nodeId) {
  const graph = normalizeGraphTraversalParameters(parameters);
  const node = graph.nodes.find(({ id }) => id === nodeId);
  if (!node) return null;
  const outgoingNodeIds = graph.edges
    .filter(({ source }) => source === nodeId)
    .map(({ target }) => target)
    .sort(compareIds);
  return {
    node: { ...node },
    outgoing_neighbors: outgoingNodeIds.map((id) => ({ ...graph.nodes.find((candidate) => candidate.id === id) }))
  };
}

function buildElements(graph) {
  const radius = Math.max(68, Math.min(118, graph.nodes.length * 5));
  const centerX = 180;
  const centerY = 150;
  const nodes = graph.nodes.map((node, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / graph.nodes.length;
    return {
      group: "nodes",
      data: { ...node },
      position: {
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle)
      }
    };
  });
  const edges = graph.edges.map(({ source, target }, index) => ({
    group: "edges",
    data: { id: `guide-edge-${index}`, source, target }
  }));
  return [...nodes, ...edges];
}

const CYTOSCAPE_STYLE = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      color: "#18212f",
      "font-size": 12,
      "font-weight": 600,
      "text-wrap": "wrap",
      "text-max-width": 100,
      "text-valign": "center",
      "text-halign": "center",
      width: "label",
      height: "label",
      padding: "12px",
      "background-color": "#e6efff",
      "border-width": 2,
      "border-color": "#4776bd"
    }
  },
  {
    selector: "edge",
    style: {
      width: 2,
      "line-color": "#728096",
      "target-arrow-color": "#728096",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "arrow-scale": 1.1
    }
  },
  { selector: ".is-visited", style: { "background-color": "#d9f2e5", "border-color": "#16834b" } },
  { selector: ".is-current", style: { "background-color": "#fff0c2", "border-color": "#a66100", "border-width": 4 } },
  { selector: ".is-selected", style: { "border-color": "#6639a6", "border-width": 4 } },
  { selector: ".is-traversed", style: { "line-color": "#16834b", "target-arrow-color": "#16834b", width: 4 } }
];

function createElement(document, tagName, text, className) {
  const element = document.createElement(tagName);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function setInlineStyle(element, style) {
  Object.assign(element.style, style);
}

function makeFallbackText(parameters, altText, fallbackText) {
  if (fallbackText) return fallbackText;
  const nodeLabels = new Map(parameters.nodes.map(({ id, label }) => [id, label]));
  const connections = parameters.edges.map(({ source, target }) => `${nodeLabels.get(source)} → ${nodeLabels.get(target)}`);
  return `${altText || "Directed graph activity."} Connections: ${connections.join("; ")}.`;
}

function failedHandle(container, fallbackText, error = null) {
  let disposed = false;
  if (container?.ownerDocument && typeof container.append === "function") {
    const message = createElement(container.ownerDocument, "p", fallbackText || "Graph activity unavailable.");
    message.setAttribute("role", "status");
    container.replaceChildren(message);
  }
  return {
    fallbackText: fallbackText || "Graph activity unavailable.",
    ready: Promise.resolve({ ok: false, error }),
    reset() {},
    step() { return null; },
    selectNode() { return null; },
    describeState() { return null; },
    resize() {},
    dispose() {
      if (disposed) return;
      disposed = true;
      container?.replaceChildren?.();
    }
  };
}

/**
 * Mount the fixed graph-traversal activity with an injected Cytoscape factory.
 * The input can select graph content and BFS/DFS, but it cannot set renderer
 * options, styles, selectors, plugins, layouts, or executable library config.
 */
export function mountCytoscapeTraversal(container, block, cytoscapeApi) {
  let graph;
  let title;
  let altText;
  let fallbackText;
  try {
    if (!container || typeof container.append !== "function" || !container.ownerDocument?.createElement) {
      throw new TypeError("A DOM container is required for the graph activity.");
    }
    if (!isRecord(block)) throw new TypeError("A graph activity block is required.");
    if (block.template_id !== "graph_traversal_v1") throw new TypeError("Unsupported graph activity template.");
    title = requiredText(block.title, "Graph activity title", MAX_TITLE_LENGTH);
    graph = normalizeGraphTraversalParameters(block.parameters);
    altText = requiredText(block.alt_text, "Graph activity alt text", MAX_ALT_LENGTH);
    fallbackText = block.fallback_text === undefined
      ? ""
      : requiredText(block.fallback_text, "Graph activity fallback text", MAX_FALLBACK_LENGTH);
    fallbackText = makeFallbackText(graph, altText, fallbackText);
  } catch (error) {
    return failedHandle(container, typeof block?.fallback_text === "string" ? block.fallback_text.slice(0, MAX_FALLBACK_LENGTH) : "Graph activity unavailable.", error);
  }

  const document = container.ownerDocument;
  const root = createElement(document, "section", undefined, "study-guide-cytoscape-traversal");
  setInlineStyle(root, { display: "grid", gap: "0.75rem", minWidth: "0" });
  const heading = createElement(document, "h3", title);
  const explanation = createElement(document, "p", altText);
  const controls = createElement(document, "div");
  setInlineStyle(controls, { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" });

  const algorithmLabel = createElement(document, "span", "Traversal:");
  const bfsButton = createElement(document, "button", "Breadth-first");
  const dfsButton = createElement(document, "button", "Depth-first");
  bfsButton.type = "button";
  dfsButton.type = "button";
  bfsButton.setAttribute("aria-pressed", String(graph.algorithm === "bfs"));
  dfsButton.setAttribute("aria-pressed", String(graph.algorithm === "dfs"));
  const startLabel = createElement(document, "label", "Start node ");
  const startSelect = createElement(document, "select");
  startSelect.setAttribute("aria-label", "Traversal start node");
  for (const node of graph.nodes) {
    const option = createElement(document, "option", node.label);
    option.value = node.id;
    option.selected = node.id === graph.start_node;
    startSelect.append(option);
  }
  startLabel.append(startSelect);
  const previousButton = createElement(document, "button", "Previous step");
  const nextButton = createElement(document, "button", "Start traversal");
  const resetButton = createElement(document, "button", "Reset");
  for (const button of [previousButton, nextButton, resetButton]) button.type = "button";
  previousButton.disabled = true;

  controls.append(algorithmLabel, bfsButton, dfsButton, startLabel, previousButton, nextButton, resetButton);
  const status = createElement(document, "p", "Traversal is ready. Choose a method and start node, then step through the graph.");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("aria-atomic", "true");

  const graphHost = createElement(document, "div", undefined, "study-guide-cytoscape-canvas");
  graphHost.setAttribute("role", "img");
  graphHost.setAttribute("aria-label", altText);
  graphHost.setAttribute("aria-describedby", "");
  setInlineStyle(graphHost, {
    width: "100%",
    height: "340px",
    minHeight: "260px",
    minWidth: "0",
    boxSizing: "border-box",
    border: "1px solid #cbd5e1",
    borderRadius: "0.5rem",
    background: "#fff",
    position: "relative"
  });
  const selectedHeading = createElement(document, "h4", "Selected node");
  const selectedName = createElement(document, "p", "");
  const selectedDescription = createElement(document, "p", "");
  const neighborList = createElement(document, "ul");
  const nodeListHeading = createElement(document, "h4", "Select a node");
  const nodeList = createElement(document, "ul");
  const textFallback = createElement(document, "details");
  const fallbackSummary = createElement(document, "summary", "Text version of this graph");
  const fallbackDescription = createElement(document, "p", fallbackText);
  const edgeList = createElement(document, "ul");
  textFallback.append(fallbackSummary, fallbackDescription, edgeList);

  root.append(heading, explanation, controls, status, graphHost, selectedHeading, selectedName, selectedDescription, neighborList, nodeListHeading, nodeList, textFallback);
  container.replaceChildren(root);

  let algorithm = graph.algorithm;
  let startNode = graph.start_node;
  let traversal = buildGraphTraversal({ ...graph, algorithm, start_node: startNode });
  let stepIndex = -1;
  let selectedNode = startNode;
  let cy = null;
  let disposed = false;
  const listeners = [];
  const cytoscapeHandlers = [];

  const listen = (element, eventName, handler) => {
    element.addEventListener(eventName, handler);
    listeners.push(() => element.removeEventListener(eventName, handler));
  };

  const nodeButtons = new Map();
  for (const node of graph.nodes) {
    const item = createElement(document, "li");
    const button = createElement(document, "button", node.label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(node.id === selectedNode));
    listen(button, "click", () => selectNode(node.id));
    item.append(button);
    nodeList.append(item);
    nodeButtons.set(node.id, button);
  }
  for (const { source, target } of graph.edges) {
    edgeList.append(createElement(document, "li", `${graph.nodes.find(({ id }) => id === source).label} → ${graph.nodes.find(({ id }) => id === target).label}`));
  }

  const describeSelection = () => selectGraphNode(graph, selectedNode);

  const updateGraphClasses = () => {
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().removeClass("is-visited is-current is-selected");
      cy.edges().removeClass("is-traversed");
      const visitedIds = traversal.visits.slice(0, stepIndex + 1).map(({ nodeId }) => nodeId);
      for (const id of visitedIds) cy.getElementById(id).addClass("is-visited");
      const currentVisit = traversal.visits[stepIndex];
      if (currentVisit) cy.getElementById(currentVisit.nodeId).addClass("is-current");
      cy.getElementById(selectedNode).addClass("is-selected");
      for (const visit of traversal.visits.slice(1, stepIndex + 1)) {
        const edgeIndex = graph.edges.findIndex(({ source, target }) => source === visit.parentId && target === visit.nodeId);
        if (edgeIndex >= 0) cy.getElementById(`guide-edge-${edgeIndex}`).addClass("is-traversed");
      }
    });
  };

  const updateSelectionText = () => {
    const selection = describeSelection();
    if (!selection) return;
    selectedName.textContent = `${selection.node.label} (${selection.node.id})`;
    selectedDescription.textContent = selection.node.description || "No additional description was provided for this node.";
    neighborList.replaceChildren();
    if (!selection.outgoing_neighbors.length) {
      neighborList.append(createElement(document, "li", "No outgoing connections."));
    } else {
      for (const neighbor of selection.outgoing_neighbors) neighborList.append(createElement(document, "li", neighbor.label));
    }
    for (const [id, button] of nodeButtons) button.setAttribute("aria-pressed", String(id === selectedNode));
    graphHost.setAttribute("aria-label", `${altText} Selected node: ${selection.node.label}. Outgoing connections: ${selection.outgoing_neighbors.map(({ label }) => label).join(", ") || "none"}.`);
    updateGraphClasses();
  };

  const updateControls = () => {
    bfsButton.setAttribute("aria-pressed", String(algorithm === "bfs"));
    dfsButton.setAttribute("aria-pressed", String(algorithm === "dfs"));
    startSelect.value = startNode;
    previousButton.disabled = stepIndex <= 0;
    nextButton.disabled = stepIndex >= traversal.visits.length - 1;
    nextButton.textContent = stepIndex < 0 ? "Start traversal" : stepIndex >= traversal.visits.length - 1 ? "Traversal complete" : "Next step";
    updateGraphClasses();
  };

  const setTraversal = () => {
    traversal = buildGraphTraversal({ ...graph, algorithm, start_node: startNode });
    stepIndex = -1;
    selectedNode = startNode;
    updateControls();
    updateSelectionText();
    status.textContent = `${algorithm === "bfs" ? "Breadth-first" : "Depth-first"} traversal from ${graph.nodes.find(({ id }) => id === startNode).label} is ready. ${traversal.unreachable_node_ids.length ? `${traversal.unreachable_node_ids.length} node(s) are unreachable from this start.` : "All nodes are reachable."}`;
  };

  function selectNode(nodeId) {
    if (disposed || !graph.nodes.some(({ id }) => id === nodeId)) return null;
    selectedNode = nodeId;
    const selection = describeSelection();
    status.textContent = `Selected ${selection.node.label}. Outgoing connections: ${selection.outgoing_neighbors.map(({ label }) => label).join(", ") || "none"}.`;
    updateSelectionText();
    return selection;
  }

  function step(direction = 1) {
    if (disposed || !Number.isInteger(direction) || (direction !== 1 && direction !== -1)) return null;
    const nextIndex = Math.min(traversal.visits.length - 1, Math.max(-1, stepIndex + direction));
    if (nextIndex === stepIndex) return null;
    stepIndex = nextIndex;
    const visit = traversal.visits[stepIndex];
    if (visit) {
      selectedNode = visit.nodeId;
      const node = graph.nodes.find(({ id }) => id === visit.nodeId);
      const from = visit.parentId ? `, reached from ${graph.nodes.find(({ id }) => id === visit.parentId).label}` : " (start node)";
      status.textContent = `Step ${stepIndex + 1} of ${traversal.visits.length}: ${node.label}${from}; depth ${visit.depth}.`;
    } else {
      selectedNode = startNode;
      status.textContent = "Traversal reset to its starting state.";
    }
    updateControls();
    updateSelectionText();
    return visit || null;
  }

  const changeAlgorithm = (nextAlgorithm) => {
    if (nextAlgorithm !== "bfs" && nextAlgorithm !== "dfs") return;
    if (algorithm === nextAlgorithm) return;
    algorithm = nextAlgorithm;
    setTraversal();
  };

  listen(bfsButton, "click", () => changeAlgorithm("bfs"));
  listen(dfsButton, "click", () => changeAlgorithm("dfs"));
  listen(startSelect, "change", () => {
    if (graph.nodes.some(({ id }) => id === startSelect.value)) {
      startNode = startSelect.value;
      setTraversal();
    }
  });
  listen(previousButton, "click", () => step(-1));
  listen(nextButton, "click", () => step(1));
  listen(resetButton, "click", () => reset());

  let visualAvailable = false;
  try {
    if (typeof cytoscapeApi !== "function") throw new TypeError("Cytoscape.js is unavailable.");
    cy = cytoscapeApi({
      container: graphHost,
      elements: buildElements(graph),
      style: CYTOSCAPE_STYLE,
      layout: { name: "preset", fit: true, padding: 26 },
      minZoom: 0.55,
      maxZoom: 2.25,
      wheelSensitivity: 0.2,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      autoungrabify: true,
      boxSelectionEnabled: false,
      hideEdgesOnViewport: false,
      textureOnViewport: false,
      motionBlur: false
    });
    if (!cy || typeof cy.on !== "function" || typeof cy.destroy !== "function") {
      throw new TypeError("Cytoscape.js did not return a graph instance.");
    }
    const onNodeTap = (event) => selectNode(event.target.id());
    cy.on("tap", "node", onNodeTap);
    cytoscapeHandlers.push(() => cy?.off?.("tap", "node", onNodeTap));
    visualAvailable = true;
  } catch (error) {
    cy?.destroy?.();
    cy = null;
    const unavailable = createElement(document, "p", "Interactive graph renderer unavailable. Use the traversal controls and text version below.");
    unavailable.setAttribute("role", "status");
    graphHost.replaceChildren(unavailable);
    graphHost.setAttribute("role", "group");
    graphHost.setAttribute("aria-label", `Graph renderer unavailable. ${altText}`);
    fallbackText = makeFallbackText(graph, altText, fallbackText);
    status.textContent = error?.message ? `${status.textContent} Graph view is unavailable; the text version remains available.` : status.textContent;
  }

  function reset() {
    if (disposed) return;
    stepIndex = -1;
    selectedNode = startNode;
    status.textContent = "Traversal reset. Choose Start traversal to begin.";
    updateControls();
    updateSelectionText();
  }

  setTraversal();

  return {
    fallbackText,
    ready: Promise.resolve({ ok: true, visualization: visualAvailable }),
    step,
    reset,
    selectNode,
    describeState() {
      if (disposed) return null;
      const selection = describeSelection();
      return {
        algorithm,
        start_node: startNode,
        step_index: stepIndex,
        selected_node: selectedNode,
        visited_node_ids: traversal.visits.slice(0, stepIndex + 1).map(({ nodeId }) => nodeId),
        current_visit: traversal.visits[stepIndex] ? { ...traversal.visits[stepIndex] } : null,
        selected_outgoing_node_ids: selection?.outgoing_neighbors.map(({ id }) => id) || [],
        unreachable_node_ids: [...traversal.unreachable_node_ids]
      };
    },
    resize() {
      if (disposed || !cy) return;
      cy.resize();
      cy.fit(undefined, 26);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const removeListener of listeners.splice(0)) removeListener();
      for (const removeHandler of cytoscapeHandlers.splice(0)) removeHandler();
      cy?.destroy?.();
      cy = null;
      container.replaceChildren();
    }
  };
}
