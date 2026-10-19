import test from "node:test";
import assert from "node:assert/strict";

import {
  deleteClassStudyAssistantEventApi,
  getClassStudyAssistantEventsApi,
  sendClassStudyAssistantMessageApi
} from "../src/api.js";

test("class assistant uses private same-origin endpoints with bounded chat payloads", async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (options.method === "DELETE") return new Response(null, { status: 204 });
    return new Response(JSON.stringify({ reply: "I found your class notes.", events: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  await sendClassStudyAssistantMessageApi("class/id", "What should I review?", [
    { role: "user", text: "Osmosis" },
    { role: "assistant", text: "Water movement across a membrane." }
  ]);
  assert.deepEqual(await getClassStudyAssistantEventsApi("class/id"), []);
  await deleteClassStudyAssistantEventApi("class/id", "event/id");

  assert.equal(calls[0].url, "/api/classes/class%2Fid/study-assistant");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    message: "What should I review?",
    history: [
      { role: "user", text: "Osmosis" },
      { role: "assistant", text: "Water movement across a membrane." }
    ]
  });
  assert.equal(calls[1].url, "/api/classes/class%2Fid/study-assistant/events");
  assert.equal(calls[2].url, "/api/classes/class%2Fid/study-assistant/events/event%2Fid");
  assert.equal(calls[2].options.method, "DELETE");
});
