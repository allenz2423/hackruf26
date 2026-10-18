import test from "node:test";
import assert from "node:assert/strict";
import { getStudyMaterialsApi, proposeStudyMaterialOrganizationApi } from "../src/api.js";
import { displayStudyMaterialTitle, formatRemoteMaterial } from "../src/study.js";

test("study material cards omit a repeated course name at the start of the title", () => {
  assert.equal(
    displayStudyMaterialTitle("BIO170 · Chapter 2 — Chemical Basis of Life", "BIO170"),
    "Chapter 2 — Chemical Basis of Life"
  );
  assert.equal(
    displayStudyMaterialTitle("Bio 170: Chapter 3 — Proteins", "Bio 170"),
    "Chapter 3 — Proteins"
  );
  assert.equal(displayStudyMaterialTitle("Chapter 4 — Nucleic Acids", "BIO170"), "Chapter 4 — Nucleic Acids");
  assert.equal(displayStudyMaterialTitle("BIO170", "BIO170"), "BIO170");
});

test("remote material summaries preserve the user's class-folder assignment", () => {
  const folderId = "4caa6c38-17ea-4f99-9aef-78fa3df79a8d";
  const material = formatRemoteMaterial({
    id: "material-1",
    kind: "study_guide",
    title: "Cell Biology",
    status: "ready",
    folder_id: folderId,
    content: { format: "study_guide", sections: [] }
  }, "class-1", "Study guide");

  assert.equal(material.folderId, folderId);
});

test("materials without an assignment remain unfiled", () => {
  const material = formatRemoteMaterial({
    id: "material-2",
    kind: "quiz",
    title: "Cell Review",
    status: "ready"
  }, "class-1", "Quiz");

  assert.equal(material.folderId, null);
});

test("study material API joins private folder assignments without changing shared data", async () => {
  const originalFetch = globalThis.fetch;
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    const payload = String(url).endsWith("/study-materials")
      ? { items: [{ id: "m1", title: "Membranes" }, { id: "m2", title: "Quiz" }] }
      : { assignments: { m1: "folder-a" } };
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const materials = await getStudyMaterialsApi("class-1");
    assert.equal(materials[0].folder_id, "folder-a");
    assert.equal(materials[1].folder_id, null);
    assert.equal(requested.length, 2);
    assert.ok(requested.some((url) => url.endsWith("/study-material-organization/assignments")));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("AI organization is explicitly requested and posts no source content from the browser", async () => {
  const originalFetch = globalThis.fetch;
  let requestBody;
  globalThis.fetch = async (_url, options) => {
    requestBody = options.body;
    return new Response(JSON.stringify({ folders: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    await proposeStudyMaterialOrganizationApi("class-1");
    assert.equal(requestBody, "{}");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
