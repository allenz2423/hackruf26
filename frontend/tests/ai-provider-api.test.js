import assert from "node:assert/strict";
import test from "node:test";

import {
  getAIProviderSettingsApi,
  updateAIProviderSettingsApi,
  resetAIProviderSettingsApi,
  testAIProviderSettingsApi
} from "../src/api.js";

test("AI provider settings use same-origin admin routes and write-only key payloads", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null });
    return new Response(JSON.stringify({ provider_configured: true, api_key_configured: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await getAIProviderSettingsApi();
  await updateAIProviderSettingsApi({
    base_url: "https://api.example.test/v1",
    model: "model-1",
    api_key: "secret-key",
    clear_api_key: false
  });
  await testAIProviderSettingsApi();
  await resetAIProviderSettingsApi();

  assert.deepEqual(requests.map(({ path, method }) => [path, method]), [
    ["/api/admin/ai-provider", "GET"],
    ["/api/admin/ai-provider", "PUT"],
    ["/api/admin/ai-provider/test", "POST"],
    ["/api/admin/ai-provider", "DELETE"]
  ]);
  assert.deepEqual(requests[1].body, {
    base_url: "https://api.example.test/v1",
    model: "model-1",
    api_key: "secret-key",
    clear_api_key: false
  });
});
