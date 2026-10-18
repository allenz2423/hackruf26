import test from "node:test";
import assert from "node:assert/strict";

import { createClass, setDemoContent } from "../src/classes.js";

class MemoryStorage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test("a failed server class creation does not claim a local class was created", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalLocalStorage = globalThis.localStorage;
  const originalSessionStorage = globalThis.sessionStorage;
  globalThis.fetch = async () => new Response("", { status: 503 });
  globalThis.localStorage = new MemoryStorage();
  globalThis.sessionStorage = new MemoryStorage();
  localStorage.setItem("screen-tutor-classes", "[]");
  setDemoContent(false);
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (originalLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = originalLocalStorage;
    if (originalSessionStorage === undefined) delete globalThis.sessionStorage;
    else globalThis.sessionStorage = originalSessionStorage;
  });

  await assert.rejects(createClass("Biology"), /Jot server|server/i);
  assert.deepEqual(JSON.parse(localStorage.getItem("screen-tutor-classes")), []);
});
