import assert from "node:assert/strict";
import test from "node:test";

import {
  createClassInvitationApi,
  getClassInvitationsApi,
  redeemClassInvitationApi,
  getClassMembersApi,
  updateClassMemberCapabilitiesApi,
  removeClassMemberApi,
  leaveClassApi,
  revokeClassInvitationApi,
  updateResourcePublicationApi,
  createStudyMaterialApi,
  getMyStudyMaterialsApi
} from "../src/api.js";

test("shared-class API operations use the expected same-origin contracts", async (t) => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (path, options) => {
    requests.push({ path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null });
    return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  await createClassInvitationApi("class/1", { expiresInDays: 14, maxUses: 3, capabilities: ["read", "create"] });
  await getClassInvitationsApi("class/1");
  await redeemClassInvitationApi("  invitation-code  ");
  await getClassMembersApi("class/1");
  await updateClassMemberCapabilitiesApi("class/1", "member/2", ["read", "create"]);
  await removeClassMemberApi("class/1", "member/2");
  await leaveClassApi("class/1");
  await revokeClassInvitationApi("class/1", "invite/3");
  await updateResourcePublicationApi("uploads", "upload/4", { state: "scheduled", releaseAt: "2030-02-03T04:05:00Z" });
  await createStudyMaterialApi("class/1", { kind: "quiz", destination: "class_contribution" });
  await getMyStudyMaterialsApi();

  assert.deepEqual(requests.map(({ path, method }) => [path, method]), [
    ["/api/classes/class%2F1/invitations", "POST"],
    ["/api/classes/class%2F1/invitations", "GET"],
    ["/api/class-invitations/redeem", "POST"],
    ["/api/classes/class%2F1/members", "GET"],
    ["/api/classes/class%2F1/members/member%2F2/capabilities", "PATCH"],
    ["/api/classes/class%2F1/members/member%2F2", "DELETE"],
    ["/api/classes/class%2F1/leave", "POST"],
    ["/api/classes/class%2F1/invitations/invite%2F3", "DELETE"],
    ["/api/resources/uploads/upload%2F4/publication", "PATCH"],
    ["/api/classes/class%2F1/study-materials", "POST"],
    ["/api/me/study-materials", "GET"]
  ]);
  assert.deepEqual(requests[0].body, { expires_in_days: 14, max_uses: 3, capabilities: ["read", "create"] });
  assert.deepEqual(requests[2].body, { code: "invitation-code" });
  assert.deepEqual(requests[4].body, { capabilities: ["read", "create"] });
  assert.deepEqual(requests[8].body, { state: "scheduled", release_at: "2030-02-03T04:05:00Z" });
  assert.equal(requests[9].body.destination, "class_contribution");
});
