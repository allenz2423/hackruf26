import test from "node:test";
import assert from "node:assert/strict";
import {
  buildGraphTraversal,
  normalizeGraphTraversalParameters,
  selectGraphNode
} from "../src/study-guide/libraries/cytoscape-traversal.js";

function sampleGraph(overrides = {}) {
  return {
    algorithm: "bfs",
    start_node: "A",
    nodes: [
      { id: "A", label: "Alpha", description: "Starting concept." },
      { id: "B", label: "Beta" },
      { id: "C", label: "Gamma" },
      { id: "D", label: "Delta" },
      { id: "E", label: "Unreachable" }
    ],
    edges: [
      { source: "A", target: "C" },
      { source: "A", target: "B" },
      { source: "B", target: "D" },
      { source: "C", target: "D" },
      { source: "D", target: "A" }
    ],
    ...overrides
  };
}

test("graph input accepts exact node and edge bounds and strips unsupported options", () => {
  const nodes = Array.from({ length: 24 }, (_, index) => ({ id: `n${index}`, label: `Node ${index}` }));
  const edges = [];
  for (let index = 0; index < 24; index += 1) {
    edges.push({ source: `n${index}`, target: `n${(index + 1) % 24}` });
    edges.push({ source: `n${index}`, target: `n${(index + 2) % 24}` });
  }
  const graph = normalizeGraphTraversalParameters({
    algorithm: "dfs",
    start_node: "n0",
    nodes,
    edges,
    cytoscape_options: { style: [{ selector: "*", style: { display: "none" } }], layout: { name: "preset" } },
    arbitrary_code: "ignored"
  });

  assert.equal(graph.nodes.length, 24);
  assert.equal(graph.edges.length, 48);
  assert.equal("cytoscape_options" in graph, false);
  assert.equal("arbitrary_code" in graph, false);
  assert.deepEqual(normalizeGraphTraversalParameters({
    algorithm: "bfs",
    start_node: "a",
    nodes: [{ id: "a", label: "A" }, { id: "b", label: "B" }],
    edges: [{ source: "a", target: "b" }]
  }), {
    algorithm: "bfs",
    start_node: "a",
    nodes: [
      { id: "a", label: "A", description: "" },
      { id: "b", label: "B", description: "" }
    ],
    edges: [{ source: "a", target: "b" }]
  });
});

test("graph input rejects undersized, oversized, malformed, and dangling graph data", () => {
  const graph = sampleGraph();
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, nodes: graph.nodes.slice(0, 1) }), RangeError);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, nodes: [...graph.nodes, ...Array.from({ length: 20 }, (_, i) => ({ id: `extra${i}`, label: `Extra ${i}` }))] }), RangeError);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, edges: [] }), RangeError);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, edges: [...graph.edges, ...Array.from({ length: 44 }, (_, i) => ({ source: "A", target: `not-a-node-${i}` }))] }), RangeError);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, nodes: [{ id: "not valid", label: "A" }, ...graph.nodes.slice(1)] }), /unique ID/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, nodes: [{ id: "A", label: "First" }, { id: "A", label: "Second" }, ...graph.nodes.slice(2)] }), /unique ID/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, edges: [{ source: "A", target: "missing" }] }), /declared node IDs/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, edges: [...graph.edges, { source: "A", target: "B" }] }), /duplicates/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, start_node: "missing" }), /start node/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, algorithm: "random" }), /must be "bfs" or "dfs"/);
  assert.throws(() => normalizeGraphTraversalParameters({ ...graph, nodes: [{ id: "A", label: "x".repeat(81) }, ...graph.nodes.slice(1)] }), /label/);
});

test("BFS order, parents, depths, and unreachable nodes are stable regardless of edge order", () => {
  const first = buildGraphTraversal(sampleGraph());
  const reordered = buildGraphTraversal(sampleGraph({ edges: [...sampleGraph().edges].reverse() }));

  assert.deepEqual(first, reordered);
  assert.deepEqual(first.visits, [
    { nodeId: "A", parentId: null, depth: 0 },
    { nodeId: "B", parentId: "A", depth: 1 },
    { nodeId: "C", parentId: "A", depth: 1 },
    { nodeId: "D", parentId: "B", depth: 2 }
  ]);
  assert.deepEqual(first.unreachable_node_ids, ["E"]);
});

test("DFS performs stable recursive preorder and handles cycles", () => {
  const traversal = buildGraphTraversal(sampleGraph({ algorithm: "dfs" }));
  assert.deepEqual(traversal.visits, [
    { nodeId: "A", parentId: null, depth: 0 },
    { nodeId: "B", parentId: "A", depth: 1 },
    { nodeId: "D", parentId: "B", depth: 2 },
    { nodeId: "C", parentId: "A", depth: 1 }
  ]);
  assert.deepEqual(traversal.unreachable_node_ids, ["E"]);
});

test("node selection reports stable directed outgoing neighbors without mutating input", () => {
  const graph = sampleGraph();
  const original = structuredClone(graph);
  assert.deepEqual(selectGraphNode(graph, "A"), {
    node: { id: "A", label: "Alpha", description: "Starting concept." },
    outgoing_neighbors: [
      { id: "B", label: "Beta", description: "" },
      { id: "C", label: "Gamma", description: "" }
    ]
  });
  assert.deepEqual(selectGraphNode(graph, "E").outgoing_neighbors, []);
  assert.equal(selectGraphNode(graph, "not-declared"), null);
  assert.deepEqual(graph, original);
});
