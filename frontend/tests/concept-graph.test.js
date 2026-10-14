import assert from "node:assert/strict";
import test from "node:test";

import {
  addClassConceptAliasApi,
  createClassConceptApi,
  createClassConceptEdgeApi,
  decideClassConceptAliasApi,
  decideClassConceptApi,
  decideClassConceptEdgeApi,
  deleteClassConceptAliasApi,
  deleteClassConceptEdgeApi,
  getClassConceptExtractionApi,
  getClassConceptGraphApi,
  getClassConceptMergeHistoryApi,
  getClassConceptGraphReviewApi,
  mergeClassConceptApi,
  startClassConceptExtractionApi,
  updateClassConceptApi
} from "../src/api.js";
import { conceptGraphPageMarkup } from "../src/concept-graph.js";

test("concept graph API wrappers use same-origin class routes and settled payload fields", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path: String(path), options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await getClassConceptGraphApi("class/1");
  await getClassConceptGraphReviewApi("class/1");
  await createClassConceptApi("class/1", { canonical_name: "Mitosis", description: "Cell division" });
  await updateClassConceptApi("class/1", "concept/2", { canonical_name: "Mitosis" }, 4);
  await addClassConceptAliasApi("class/1", "concept/2", { name: "Karyokinesis", language: "en" });
  await deleteClassConceptAliasApi("class/1", "concept/2", "alias/3", 6);
  await decideClassConceptAliasApi("class/1", "concept/2", "alias/3", "approve", 7);
  await mergeClassConceptApi("class/1", "concept/2", "concept/4", 5);
  await decideClassConceptApi("class/1", "concept/2", "approve", 6);
  await createClassConceptEdgeApi("class/1", {
    from_concept_id: "concept-1", to_concept_id: "concept-2", type: "prerequisite_for",
    necessity: "helpful", provenance: "explicit", explanation: "The source states this order.",
    source_chunk_revision_ids: ["chunk-revision-1"]
  });
  await decideClassConceptEdgeApi("class/1", "edge/7", "reject", 8);
  await deleteClassConceptEdgeApi("class/1", "edge/7", 9);
  await startClassConceptExtractionApi("class/1", "upload/9");
  await getClassConceptExtractionApi("class/1", "operation/10");
  await getClassConceptMergeHistoryApi("class/1");

  assert.deepEqual(requests.map((request) => new URL(request.path, "https://jot.test").pathname), [
    "/api/classes/class%2F1/concept-graph",
    "/api/classes/class%2F1/concept-graph/review",
    "/api/classes/class%2F1/concepts",
    "/api/classes/class%2F1/concepts/concept%2F2",
    "/api/classes/class%2F1/concepts/concept%2F2/aliases",
    "/api/classes/class%2F1/concepts/concept%2F2/aliases/alias%2F3",
    "/api/classes/class%2F1/concepts/concept%2F2/aliases/alias%2F3/decision",
    "/api/classes/class%2F1/concepts/concept%2F2/merge",
    "/api/classes/class%2F1/concepts/concept%2F2/decision",
    "/api/classes/class%2F1/concept-edges",
    "/api/classes/class%2F1/concept-edges/edge%2F7/decision",
    "/api/classes/class%2F1/concept-edges/edge%2F7",
    "/api/classes/class%2F1/concept-graph/extractions",
    "/api/classes/class%2F1/concept-graph/extractions/operation%2F10",
    "/api/classes/class%2F1/concept-graph/merges"
  ]);
  assert.equal(requests[3].options.method, "PATCH");
  assert.equal(requests.at(-1).options.cache, "no-store");
  assert.equal(new URL(requests[5].path, "https://jot.test").searchParams.get("expected_version"), "6");
  assert.equal(new URL(requests[11].path, "https://jot.test").searchParams.get("expected_version"), "9");
  assert.deepEqual(JSON.parse(requests[3].options.body), { canonical_name: "Mitosis", expected_version: 4 });
  assert.deepEqual(JSON.parse(requests[4].options.body), { name: "Karyokinesis", language: "en" });
  assert.deepEqual(JSON.parse(requests[6].options.body), { decision: "approve", expected_version: 7 });
  assert.deepEqual(JSON.parse(requests[7].options.body), { target_concept_id: "concept/4", expected_version: 5 });
  assert.deepEqual(JSON.parse(requests[8].options.body), { decision: "approve", expected_version: 6 });
  assert.deepEqual(JSON.parse(requests[9].options.body), {
    from_concept_id: "concept-1", to_concept_id: "concept-2", type: "prerequisite_for",
    necessity: "helpful", provenance: "explicit", explanation: "The source states this order.",
    source_chunk_revision_ids: ["chunk-revision-1"]
  });
  assert.deepEqual(JSON.parse(requests[10].options.body), { decision: "reject", expected_version: 8 });
  assert.deepEqual(JSON.parse(requests[12].options.body), { upload_id: "upload/9" });
});

test("member graph markup is read-only and excludes proposals and untrusted hidden fields", () => {
  const markup = conceptGraphPageMarkup({
    graph: {
      concepts: [{
        id: "c1", canonical_name: "<Mitosis>", description: "Cell division", status: "validated",
        aliases: [{ id: "a1", name: "Karyokinesis", language: "en" }],
        sources: [{ material_id: "m1", chunk_revision_id: "cr1", title: "Week 4", locator: { page: 12 } }],
        answer_key: "secret"
      }],
      edges: [{ id: "e1", from_concept_id: "c1", to_concept_id: "c1", type: "related_to", status: "validated", sources: [] }]
    },
    review: { concept_proposals: [{ id: "hidden-proposal", canonical_name: "Do not show" }], answer_key: "private" },
    canManage: false
  });
  assert.match(markup, /&lt;Mitosis&gt;/);
  assert.match(markup, /validated/);
  assert.match(markup, /Karyokinesis/);
  assert.match(markup, /data-graph-source="cr1"/);
  assert.match(markup, /page 12/);
  assert.match(markup, /Related to/);
  assert.doesNotMatch(markup, /hidden-proposal|Do not show|secret|private|Manager tools|data-concept-decision|data-edge-decision|Deprecate concept|Create a concept/);
});

test("prerequisite direction review flags are visible on proposed and validated edges", () => {
  const concepts = [
    { id: "c1", canonical_name: "Fractions", status: "validated" },
    { id: "c2", canonical_name: "Ratios", status: "validated" }
  ];
  const markup = conceptGraphPageMarkup({
    graph: {
      concepts,
      edges: [{ id: "e1", from_concept_id: "c1", to_concept_id: "c2", type: "prerequisite_for", review_flags: ["opposite_prerequisite"], sources: [] }]
    },
    review: { edge_proposals: [{ id: "e2", from_concept_id: "c2", to_concept_id: "c1", type: "prerequisite_for", review_flags: ["opposite_prerequisite"], sources: [] }] },
    canManage: true
  });
  assert.equal((markup.match(/Check relationship direction\./g) || []).length, 2);
  assert.match(markup, /Source evidence also suggests the opposite prerequisite direction/);
});

test("merge history appears only in manager UI and escapes retained provenance", () => {
  const history = { items: [{
    source_concept_id: "source-id", target_concept_id: "target-id",
    source_name: "<Old concept>", source_description: "<img src=x onerror=alert(1)>",
    source_version: 3, merged_by: "owner-id", created_at: "2026-10-14T12:00:00Z", target_name: "Current concept"
  }] };
  const managerMarkup = conceptGraphPageMarkup({ graph: { concepts: [], edges: [] }, canManage: true, mergeHistory: history });
  assert.match(managerMarkup, /Merge history \(1\)/);
  assert.match(managerMarkup, /&lt;Old concept&gt;/);
  assert.match(managerMarkup, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(managerMarkup, /<img src=x/);

  const memberMarkup = conceptGraphPageMarkup({ graph: { concepts: [], edges: [] }, canManage: false, mergeHistory: history });
  assert.doesNotMatch(memberMarkup, /Merge history|Old concept|Current concept|owner-id/);
});

test("manager markup shows review actions, source-backed forms, and extraction states safely", () => {
  const markup = conceptGraphPageMarkup({
    graph: { concepts: [
      { id: "c1", canonical_name: "DNA replication", status: "validated", version: 2 },
      { id: "c2", canonical_name: "DNA transcription", status: "validated", version: 1 }
    ], edges: [] },
    review: {
      concept_proposals: [{
        id: "candidate-1", canonical_name: "<Candidate>", description: "Proposal",
        version: 3, sources: [{ material_id: "m1", chunk_revision_id: "cr2", title: "Lecture", locator: { slide: 8 }, excerpt: "<script> unsafe" }],
        answer_key: "hidden"
      }],
      alias_proposals: [{
        id: "alias-proposal", name: "Karyokinesis", language: "en", state: "proposed",
        concept_id: "c1", concept_name: "DNA replication", concept_version: 2,
        sources: [{ material_id: "m1", chunk_revision_id: "cr4", title: "Lecture", locator: { page: 10 }, excerpt: "A bounded source excerpt" }]
      }],
      edge_proposals: [{
        id: "edge-candidate", from_concept_id: "c1", to_concept_id: "candidate-1", type: "prerequisite_for",
        necessity: "essential", provenance: "explicit", explanation: "The source describes the dependency.", version: 4,
        sources: [{ material_id: "m1", chunk_revision_id: "cr3", title: "Lecture", locator: { page: 9 }, snippet: "Supporting excerpt" }]
      }],
      extractions: [{ operation_id: "op-pending", upload_id: "up1", status: "pending" }]
    },
    uploads: [
      { id: "up1", title: "Ready source", status: "ready", source_index_status: "ready" },
      { id: "up2", title: "Failed source", status: "ready", source_index_status: "ready" }
    ],
    extractionJobs: [
      { operation_id: "op-pending", upload_id: "up1", status: "processing" },
      { operation_id: "op-failed", upload_id: "up2", status: "failed", error_code: "provider_unavailable" }
    ],
    canManage: true
  });
  assert.match(markup, /Manager tools/);
  assert.match(markup, /Approve concept/);
  assert.match(markup, />Reject</);
  assert.match(markup, /Approve edge/);
  assert.match(markup, /Alias proposals/);
  assert.match(markup, /Approve alias/);
  assert.match(markup, /Reject alias/);
  assert.match(markup, /data-alias-id="alias-proposal"/);
  assert.match(markup, /data-version="2" aria-label="Approve alias Karyokinesis">Approve alias/);
  assert.match(markup, /data-edit-concept="c1"/);
  assert.match(markup, /data-concept-decision="deprecate" data-concept-id="c1" data-version="2">Deprecate concept/);
  assert.match(markup, /data-add-alias="c1"/);
  assert.match(markup, /data-merge-concept="c1"/);
  assert.match(markup, /Add a source-backed relationship/);
  assert.match(markup, /Extraction in progress/);
  assert.match(markup, /Retry extraction/);
  assert.match(markup, /&lt;Candidate&gt;/);
  assert.match(markup, /&lt;script&gt; unsafe/);
  assert.doesNotMatch(markup, /<script>|answer_key|>hidden</);
});

test("empty and unavailable graph states remain actionable and do not render provider details", () => {
  const markup = conceptGraphPageMarkup({
    graph: { concepts: [], edges: [] },
    canManage: true,
    error: "The class concept map could not be loaded.",
    reviewError: "Could not load proposals.",
    statusMessage: "Try again."
  });
  assert.match(markup, /Concept map unavailable/);
  assert.match(markup, /Refresh map/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /role="status"/);
  assert.doesNotMatch(markup, /No validated concepts yet|Manager tools|Create a concept|Add a source-backed relationship/);
  assert.doesNotMatch(markup, /provider stack|traceback|API_KEY/);
});

test("manager review failures never masquerade as an empty proposal queue", () => {
  const markup = conceptGraphPageMarkup({
    graph: { concepts: [], edges: [] }, canManage: true,
    reviewError: "Could not load proposed records."
  });
  assert.match(markup, /review queue is unavailable/i);
  assert.doesNotMatch(markup, /No concept proposals need review|No relationship proposals need review/);
});
