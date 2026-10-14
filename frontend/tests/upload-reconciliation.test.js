import assert from "node:assert/strict";
import test from "node:test";

import { reconcileRemoteUploads } from "../src/library.js";
import { describeUploadProcessing, uploadDocumentErrorMessage } from "../src/file-preview.js";

test("remote upload reconciliation removes deleted mappings and keeps local-only uploads", () => {
  const result = reconcileRemoteUploads([
    { id: "local-kept", backendId: "remote-kept", name: "Old title" },
    { id: "local-stale", backendId: "remote-deleted", name: "Deleted file" },
    { id: "local-draft", name: "Not uploaded yet" }
  ], [
    { id: "remote-kept", title: "Current title", kind: "slides", status: "ready" }
  ]);

  assert.deepEqual(result.map(({ id }) => id), ["local-kept", "local-draft"]);
  assert.equal(result[0].backendId, "remote-kept");
  assert.equal(result[0].name, "Current title");
  assert.equal(result[1].name, "Not uploaded yet");
});

test("missing upload documents get an actionable message without offering a retry", () => {
  assert.match(uploadDocumentErrorMessage({ status: 404 }), /no longer available/);
  assert.match(uploadDocumentErrorMessage({ status: 503, message: "Try again later" }), /Try again later/);
});

test("failed source processing is distinct from a study-document job and exposes no fake progress", () => {
  const state = describeUploadProcessing({ status: "failed", processing_progress: 10 });

  assert.equal(state.state, "failed");
  assert.equal(state.progress, null);
  assert.match(state.message, /original upload failed to process/i);
  assert.doesNotMatch(state.message, /still processing/i);
});

test("source processing progress is reported and clamped to the backend progress range", () => {
  assert.deepEqual(describeUploadProcessing({ status: "processing", processing_progress: 55 }), {
    state: "processing",
    progress: 55,
    message: "Processing original upload… 55%"
  });
  assert.equal(describeUploadProcessing({ status: "processing", processing_progress: 120 }).progress, 100);
  assert.equal(describeUploadProcessing({ status: "ready", processing_progress: 55 }).progress, 100);
});
