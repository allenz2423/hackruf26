import assert from "node:assert/strict";
import test from "node:test";

import {
  getClassSourceChunkApi,
  searchClassSourcesApi
} from "../src/api.js";
import {
  sourceChunkMarkup,
  sourceCitationFromChunk,
  sourceHierarchyText,
  sourceLocatorText,
  sourceSearchHitMarkup
} from "../src/source-search.js";
import { formatSourceCitation } from "../src/source-citations.js";

test("source search API uses bounded same-origin query and exact chunk routes", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path: String(path), options });
    const payload = requests.length === 1
      ? { results: [{ chunk_revision_id: "chunk-rev-1", snippet: "bounded excerpt" }] }
      : { chunk_revision_id: "chunk-rev/2", text: "exact source text" };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };
  const controller = new AbortController();

  const results = await searchClassSourcesApi("class/1", {
    query: "  DNA replication  ",
    uploadId: "upload/2",
    limit: 200,
    signal: controller.signal
  });
  const chunk = await getClassSourceChunkApi("class/1", "chunk-rev/2", { signal: controller.signal });

  const searchUrl = new URL(requests[0].path, "https://jot.test");
  assert.equal(searchUrl.pathname, "/api/classes/class%2F1/sources/search");
  assert.equal(searchUrl.searchParams.get("query"), "DNA replication");
  assert.equal(searchUrl.searchParams.get("upload_id"), "upload/2");
  assert.equal(searchUrl.searchParams.get("limit"), "50");
  assert.equal(requests[0].options.signal, controller.signal);
  assert.equal(requests[1].path, "/api/classes/class%2F1/sources/chunks/chunk-rev%2F2");
  assert.equal(requests[1].options.signal, controller.signal);
  assert.equal(results[0].chunk_revision_id, "chunk-rev-1");
  assert.equal(chunk.text, "exact source text");
});

test("source result display is bounded, escaped, and excludes private question fields", () => {
  const markup = sourceSearchHitMarkup({
    title: "<Lecture>",
    chunk_revision_id: "chunk-r1",
    section_id: "section-3",
    chunk_id: "chunk-7",
    revision_id: "revision-9",
    hierarchy_path: ["Unit 2", { heading: "Replication" }],
    locator: { page: 8, slide: 4, timestamp_seconds: 125 },
    snippet: "<img src=x>".repeat(100),
    answer_key: "secret",
    correct_choice: "private",
    rubric: "internal"
  });

  assert.match(markup, /&lt;Lecture&gt;/);
  assert.doesNotMatch(markup, /<img src=x>/);
  assert.match(markup, /Unit 2 › Replication/);
  assert.match(markup, /Page 8 · Slide 4 · 2:05/);
  assert.ok((markup.match(/&lt;img src=x&gt;/g) || []).length <= 55);
  assert.doesNotMatch(markup, /secret|private|internal/);
  assert.equal(sourceHierarchyText({ hierarchy: { material: "Bio", section: { heading: "Unit 1" } } }), "Bio › Unit 1");
});

test("exact chunk display shows immutable provenance and safely renders text", () => {
  const chunk = {
    material_id: "material-1",
    revision_id: "revision-2",
    chunk_revision_id: "chunk-revision-3",
    title: "Cell biology",
    hierarchy: ["Unit 4", "DNA replication"],
    locator: { slide: 11 },
    extraction_method: "pptx-text",
    content_sha256: "sha256:abc123",
    text: "<script>alert('no')</script> polymerase adds nucleotides",
    answer_key: "must not render"
  };
  const markup = sourceChunkMarkup(chunk);

  assert.match(markup, /Unit 4 › DNA replication/);
  assert.match(markup, /Slide 11/);
  assert.match(markup, /Open original source/);
  assert.match(markup, /aria-label="Open original source at Slide 11"/);
  assert.match(markup, /chunk-revision-3/);
  assert.match(markup, /sha256:abc123/);
  assert.match(markup, /&lt;script&gt;/);
  assert.doesNotMatch(markup, /<script>|must not render/);
  assert.equal(sourceHierarchyText(chunk), "Unit 4 › DNA replication");
  assert.equal(sourceLocatorText(chunk), "Slide 11");
});

test("original-source citation preserves the exact authorized material locator", () => {
  const chunk = {
    material_id: "material-9",
    title: "Lecture 9",
    heading_path: ["Unit 3", "Cell division"],
    locator: { timestamp_start_seconds: 81.8, timestamp_end_seconds: 102.4 }
  };
  const citation = sourceCitationFromChunk(chunk);
  assert.deepEqual(citation, {
    upload_id: "material-9",
    material_id: "material-9",
    name: "Lecture 9",
    timestamp_seconds: 81.8,
    timestamp_end_seconds: 102.4
  });
  assert.equal(sourceLocatorText(chunk), "1:21–1:42");
  assert.equal(formatSourceCitation(citation), "Lecture 9 · 1:21–1:42");
  assert.equal(sourceHierarchyText(chunk), "Unit 3 › Cell division");

  const pageCitation = sourceCitationFromChunk({ material_id: "pdf-1", locator: { page: 7 } });
  assert.equal(pageCitation.page, 7);
  const slideCitation = sourceCitationFromChunk({ material_id: "slides-1", locator: { slide: 12 } });
  assert.equal(slideCitation.slide, 12);
});
