import test from "node:test";
import assert from "node:assert/strict";

import { clearGuideProgressApi, getGuideProgressApi, submitGuideMiniQuizApi } from "../src/api.js";

test("guide review APIs use owner-scoped routes and opaque operation IDs", async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ sections: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  await getGuideProgressApi("guide/id");
  await submitGuideMiniQuizApi("guide/id", 4, { operation_id: "op-1", choice_index: 2 });
  await clearGuideProgressApi("guide/id");

  assert.equal(calls[0].url, "/api/study-materials/guide%2Fid/guide-progress");
  assert.equal(calls[1].url, "/api/study-materials/guide%2Fid/guide-sections/4/mini-quiz");
  assert.deepEqual(JSON.parse(calls[1].options.body), { operation_id: "op-1", choice_index: 2 });
  assert.equal(calls[2].url, calls[0].url);
  assert.equal(calls[2].options.method, "DELETE");
});
