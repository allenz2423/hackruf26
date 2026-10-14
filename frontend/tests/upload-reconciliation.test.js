import assert from "node:assert/strict";
import test from "node:test";

import { reconcileRemoteUploads } from "../src/library.js";
import { uploadDocumentErrorMessage } from "../src/file-preview.js";

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
