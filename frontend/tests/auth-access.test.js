import test from "node:test";
import assert from "node:assert/strict";

import { getAuthOptions, getCurrentUser, signIn, signUp } from "../src/api.js";

test("current-user response controls guest links and signup copy", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });

  globalThis.fetch = async () => new Response(JSON.stringify({
    user: null,
    guest_access_enabled: false,
    signup_mode: "closed"
  }), { status: 200, headers: { "Content-Type": "application/json" } });

  assert.equal(await getCurrentUser(), null);
  assert.deepEqual(getAuthOptions(), { guestAccessEnabled: false, signupMode: "closed" });
});

test("an auth-check outage is not treated as a confirmed guest", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("", { status: 503 });
  await assert.rejects(getCurrentUser(), (error) => error.code === "server_unreachable" && error.status === 503);
});

test("signup preserves the pending response without treating it as a login", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });

  globalThis.fetch = async () => new Response(JSON.stringify({
    status: "pending",
    user: null,
    message: "Waiting for review"
  }), { status: 202, headers: { "Content-Type": "application/json" } });

  const result = await signUp({ username: "Student_1", password: "secret-pass" });
  assert.equal(result.status, "pending");
  assert.equal(result.user, null);
});

test("login refreshes access settings from the server", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });

  globalThis.fetch = async () => new Response(JSON.stringify({
    user: { id: "user-1", username: "Student_1" },
    guest_access_enabled: true,
    signup_mode: "open"
  }), { status: 200, headers: { "Content-Type": "application/json" } });

  assert.equal((await signIn("Student_1", "secret-pass")).username, "Student_1");
  assert.deepEqual(getAuthOptions(), { guestAccessEnabled: true, signupMode: "open" });
});
