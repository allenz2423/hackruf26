const authListeners = new Set();
let authOptions = { guestAccessEnabled: false, signupMode: "closed" };

function syncAuthOptions(payload) {
  authOptions = {
    guestAccessEnabled: payload.guest_access_enabled === true,
    signupMode: ["approval", "open", "closed"].includes(payload.signup_mode)
      ? payload.signup_mode
      : "closed"
  };
}

// The login session lives in an HttpOnly cookie, so scripts never see or store a token.
export async function getAccessToken() {
  return "";
}

// Who is logged in? Resolves to the account, or null for a guest.
export async function getCurrentUser() {
  try {
    const payload = await apiFetch("/api/auth/me");
    syncAuthOptions(payload);
    return payload.user || null;
  } catch {
    return null;
  }
}

export function getAuthOptions() {
  return { ...authOptions };
}

export async function signIn(identifier, password) {
  const payload = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier, password })
  });
  syncAuthOptions(payload);
  return payload.user;
}

export async function signUp({ username, password, email = "", displayName = "" }) {
  return apiFetch("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({
      username,
      password,
      email: email || null,
      display_name: displayName || null
    })
  });
}

export function getSignupRequestsApi() {
  return apiFetch("/api/admin/signup-requests");
}

export function approveSignupRequestApi(requestId) {
  return apiFetch(`/api/admin/signup-requests/${encodeURIComponent(requestId)}/approve`, {
    method: "POST",
    body: "{}"
  });
}

export function rejectSignupRequestApi(requestId) {
  return apiFetch(`/api/admin/signup-requests/${encodeURIComponent(requestId)}/reject`, {
    method: "POST",
    body: "{}"
  });
}

export async function updateProfile(changes) {
  const payload = await apiFetch("/api/auth/me", {
    method: "PATCH",
    body: JSON.stringify(changes)
  });
  return payload.user;
}

export async function changePassword(currentPassword, newPassword) {
  await apiFetch("/api/auth/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  });
}

export async function signOut() {
  await apiFetch("/api/auth/logout", { method: "POST" });
}

// Called when the server says the session ended (for example it expired) mid-use.
export function onAuthStateChange(callback) {
  authListeners.add(callback);
  return () => authListeners.delete(callback);
}

// Internal HTTP client
async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("The server returned an unreadable response.");
  }
}

function unreachableError(status) {
  const error = new Error("Can't reach the Jot server right now. Make sure the backend is running, then try again.");
  error.code = "server_unreachable";
  error.status = status;
  return error;
}

export async function apiFetch(path, options = {}) {
  const token = await getAccessToken();
  const headers = {
    ...(options.headers || {})
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers
    });
  } catch {
    throw unreachableError();
  }

  const payload = await readJson(response);

  if (!response.ok) {
    // The dev proxy answers 5xx with no body when the backend is not running.
    if (response.status >= 500 && !payload?.code) throw unreachableError(response.status);
    const message = payload?.message || payload?.detail || payload?.error || `Request failed with status ${response.status}`;
    if (response.status === 401 && payload?.code === "authentication_required") {
      for (const listener of authListeners) listener("SESSION_ENDED");
    }
    const error = new Error(message);
    error.code = payload?.code || "request_failed";
    error.status = response.status;
    error.requestId = payload?.request_id;
    throw error;
  }

  return payload;
}

// Health check
export async function checkBackendHealth() {
  try {
    return await apiFetch("/healthz");
  } catch {
    return { status: "unreachable" };
  }
}

// One-use same-origin session ticket
export async function requestLiveSession(classId = null, mode = "conversation", language = "auto", uploadIds = null, useSavedMemory = true) {
  const body = {
    mode,
    ...(useSavedMemory === false ? { use_saved_memory: false } : {}),
    ...(classId ? { class_id: classId } : {}),
    ...(classId && Array.isArray(uploadIds) ? { upload_ids: uploadIds } : {}),
    ...(language && language !== "auto" ? { language } : {})
  };
  return await apiFetch("/api/live-sessions", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

function tutorMemoryPath(classId = null) {
  return `/api/tutor-memory${classId ? `?class_id=${encodeURIComponent(classId)}` : ""}`;
}

async function tutorMemoryRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    return await apiFetch(path, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

export function getTutorMemoryApi(classId = null) {
  return tutorMemoryRequest(tutorMemoryPath(classId));
}

export function setTutorMemoryApi(classId, enabled) {
  return tutorMemoryRequest("/api/tutor-memory", {
    method: "PATCH", body: JSON.stringify({ class_id: classId || null, enabled })
  });
}

export function clearTutorMemoryApi(classId = null) {
  return tutorMemoryRequest(tutorMemoryPath(classId), { method: "DELETE" });
}

// The backend derives learner identity from the authenticated session. Never
// accept an owner/user ID from a dashboard caller.
export async function getLearningProgressApi(windowDays = 30) {
  if (!Number.isInteger(windowDays) || windowDays < 7 || windowDays > 90) {
    throw new RangeError("Learning progress window must be between 7 and 90 days.");
  }
  return await apiFetch(`/api/learning/progress?window_days=${windowDays}`);
}

export function getLearningConceptApi(conceptId, { signal } = {}) {
  return apiFetch(`/api/learning/concepts/${encodeURIComponent(conceptId)}`, { signal });
}

export function getLearningPreferencesApi({ signal } = {}) {
  return apiFetch("/api/learning/preferences", { signal });
}

export function updateLearningPreferencesApi(chosenGoal) {
  if (!["balanced", "build_foundation", "exam_prep", "review"].includes(chosenGoal)) {
    throw new Error("Choose a supported learning goal.");
  }
  return apiFetch("/api/learning/preferences", {
    method: "PUT", body: JSON.stringify({ chosen_goal: chosenGoal })
  });
}

export function getLearningProgressHistoryApi({ days = 90, conceptId = null, signal } = {}) {
  if (!Number.isInteger(days) || days < 7 || days > 365) {
    throw new RangeError("Progress history window must be between 7 and 365 days.");
  }
  const params = new URLSearchParams({ days: String(days) });
  if (conceptId) params.set("concept_id", conceptId);
  return apiFetch(`/api/learning/progress/history?${params}`, { signal });
}

export function getLearningRecommendationsApi({ signal } = {}) {
  return apiFetch("/api/learning/recommendations", { signal });
}

export function updateLearningRecommendationStateApi(recommendationId, state) {
  if (!["active", "completed", "dismissed"].includes(state)) {
    throw new Error("Recommendation state must be active, completed, or dismissed.");
  }
  return apiFetch(`/api/learning/recommendations/${encodeURIComponent(recommendationId)}/state`, {
    method: "PATCH", body: JSON.stringify({ state })
  });
}

export function createMasteryLadderApi(payload) {
  return apiFetch("/api/mastery-ladders", { method: "POST", body: JSON.stringify(payload) });
}

export function getMasteryLadderApi(ladderId, { signal } = {}) {
  return apiFetch(`/api/mastery-ladders/${encodeURIComponent(ladderId)}`, { signal });
}

export function submitMasteryLadderResponseApi(ladderId, conceptId, payload) {
  return apiFetch(`/api/mastery-ladders/${encodeURIComponent(ladderId)}/concepts/${encodeURIComponent(conceptId)}/response`, {
    method: "POST", body: JSON.stringify(payload)
  });
}

export function skipMasteryLadderConceptApi(ladderId, conceptId, payload) {
  return apiFetch(`/api/mastery-ladders/${encodeURIComponent(ladderId)}/concepts/${encodeURIComponent(conceptId)}/skip`, {
    method: "POST", body: JSON.stringify(payload)
  });
}

export function submitMasteryLadderReasoningApi(ladderId, conceptId, payload) {
  return apiFetch(`/api/mastery-ladders/${encodeURIComponent(ladderId)}/concepts/${encodeURIComponent(conceptId)}/reasoning`, {
    method: "POST", body: JSON.stringify(payload)
  });
}

export function retryMasteryLadderReasoningApi(ladderId, conceptId, payload) {
  return apiFetch(`/api/mastery-ladders/${encodeURIComponent(ladderId)}/concepts/${encodeURIComponent(conceptId)}/reasoning/retry`, {
    method: "POST", body: JSON.stringify(payload)
  });
}

export async function askStudyTutorApi(payload, { signal } = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 55_000);
  try {
    return await apiFetch("/api/study-tutor", {
      method: "POST", body: JSON.stringify(payload), signal: controller.signal
    });
  } catch (error) {
    if (timedOut) throw new Error("The tutor took too long. Try your question again.");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

// Calculator tool execution
export async function calculateExpression(expression) {
  return await apiFetch("/api/tools/calculate", {
    method: "POST",
    body: JSON.stringify({ expression })
  });
}

// Class Library endpoints
export async function getClassesApi() {
  const res = await apiFetch("/api/classes");
  return res.items || [];
}

export async function createClassApi(name, { color = null, icon = null } = {}) {
  return await apiFetch("/api/classes", {
    method: "POST",
    body: JSON.stringify({ name: name.trim(), color, icon })
  });
}

/** Change any of `name`, `color`, `icon`; fields left out stay as they are, null resets the look. */
export async function updateClassApi(classId, fields) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}`, {
    method: "PATCH",
    body: JSON.stringify(fields)
  });
}

export async function deleteClassApi(classId) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}`, {
    method: "DELETE"
  });
}

export async function createClassInvitationApi(classId, { expiresInDays = 7, maxUses = 1, capabilities = ["read"] } = {}) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/invitations`, {
    method: "POST",
    body: JSON.stringify({ expires_in_days: expiresInDays, max_uses: maxUses, capabilities })
  });
}

export async function getClassInvitationsApi(classId) {
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/invitations`);
  return res.items || [];
}

export async function revokeClassInvitationApi(classId, invitationId) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/invitations/${encodeURIComponent(invitationId)}`, { method: "DELETE" });
}

export async function redeemClassInvitationApi(code) {
  return apiFetch("/api/class-invitations/redeem", {
    method: "POST",
    body: JSON.stringify({ code: String(code || "").trim() })
  });
}

export async function getClassMembersApi(classId) {
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/members`);
  return res.items || [];
}

export async function updateClassMemberCapabilitiesApi(classId, memberId, capabilities) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/members/${encodeURIComponent(memberId)}/capabilities`, {
    method: "PATCH",
    body: JSON.stringify({ capabilities })
  });
}

export async function removeClassMemberApi(classId, memberId) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/members/${encodeURIComponent(memberId)}`, { method: "DELETE" });
}

export async function leaveClassApi(classId) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/leave`, { method: "POST" });
}

function classConceptGraphPath(classId, suffix = "") {
  return `/api/classes/${encodeURIComponent(classId)}/concept-graph${suffix}`;
}

function classConceptPath(classId, suffix = "") {
  return `/api/classes/${encodeURIComponent(classId)}/concepts${suffix}`;
}

export function getClassConceptGraphApi(classId, { signal } = {}) {
  return apiFetch(classConceptGraphPath(classId), { signal });
}

export function getClassConceptGraphReviewApi(classId, { signal } = {}) {
  return apiFetch(classConceptGraphPath(classId, "/review"), { signal });
}

export function getClassConceptMergeHistoryApi(classId, { signal } = {}) {
  return apiFetch(classConceptGraphPath(classId, "/merges"), { signal, cache: "no-store" });
}

export function createClassConceptApi(classId, concept) {
  return apiFetch(classConceptPath(classId), {
    method: "POST", body: JSON.stringify(concept)
  });
}

export function updateClassConceptApi(classId, conceptId, changes, expectedVersion) {
  return apiFetch(classConceptPath(classId, `/${encodeURIComponent(conceptId)}`), {
    method: "PATCH", body: JSON.stringify({ ...changes, expected_version: expectedVersion })
  });
}

export function addClassConceptAliasApi(classId, conceptId, { name, language = "und" }) {
  return apiFetch(classConceptPath(classId, `/${encodeURIComponent(conceptId)}/aliases`), {
    method: "POST", body: JSON.stringify({ name, language })
  });
}

export function deleteClassConceptAliasApi(classId, conceptId, aliasId, expectedVersion) {
  const version = Number(expectedVersion);
  if (!Number.isSafeInteger(version) || version < 1) throw new Error("Refresh the concept before removing this alias.");
  const query = `?expected_version=${version}`;
  return apiFetch(classConceptPath(classId, `/${encodeURIComponent(conceptId)}/aliases/${encodeURIComponent(aliasId)}${query}`), {
    method: "DELETE"
  });
}

export function decideClassConceptAliasApi(classId, conceptId, aliasId, decision, expectedVersion) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/concepts/${encodeURIComponent(conceptId)}/aliases/${encodeURIComponent(aliasId)}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, expected_version: expectedVersion })
  });
}

export function mergeClassConceptApi(classId, conceptId, targetConceptId, expectedVersion) {
  return apiFetch(classConceptPath(classId, `/${encodeURIComponent(conceptId)}/merge`), {
    method: "POST", body: JSON.stringify({ target_concept_id: targetConceptId, expected_version: expectedVersion })
  });
}

export function decideClassConceptApi(classId, conceptId, decision, expectedVersion) {
  return apiFetch(classConceptPath(classId, `/${encodeURIComponent(conceptId)}/decision`), {
    method: "POST", body: JSON.stringify({ decision, expected_version: expectedVersion })
  });
}

export function createClassConceptEdgeApi(classId, edge) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/concept-edges`, {
    method: "POST", body: JSON.stringify(edge)
  });
}

export function decideClassConceptEdgeApi(classId, edgeId, decision, expectedVersion) {
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/concept-edges/${encodeURIComponent(edgeId)}/decision`, {
    method: "POST", body: JSON.stringify({ decision, expected_version: expectedVersion })
  });
}

export function deleteClassConceptEdgeApi(classId, edgeId, expectedVersion) {
  const version = Number(expectedVersion);
  if (!Number.isSafeInteger(version) || version < 1) throw new Error("Refresh the relationship before removing it.");
  const query = `?expected_version=${version}`;
  return apiFetch(`/api/classes/${encodeURIComponent(classId)}/concept-edges/${encodeURIComponent(edgeId)}${query}`, {
    method: "DELETE"
  });
}

export function startClassConceptExtractionApi(classId, uploadId) {
  return apiFetch(classConceptGraphPath(classId, "/extractions"), {
    method: "POST", body: JSON.stringify({ upload_id: uploadId })
  });
}

export function getClassConceptExtractionApi(classId, operationId, { signal } = {}) {
  return apiFetch(classConceptGraphPath(classId, `/extractions/${encodeURIComponent(operationId)}`), { signal });
}

export async function updateResourcePublicationApi(resourceType, resourceId, { state, releaseAt = null }) {
  if (!["uploads", "study_materials"].includes(resourceType)) throw new Error("Unsupported resource type.");
  return apiFetch(`/api/resources/${resourceType}/${encodeURIComponent(resourceId)}/publication`, {
    method: "PATCH",
    body: JSON.stringify({ state, ...(state === "scheduled" ? { release_at: releaseAt } : {}) })
  });
}

// Uploads endpoints
export async function getUploadsApi(classId) {
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/uploads`);
  return res.items || [];
}

/** Search the current authorized source index. Results contain snippets only. */
export async function searchClassSourcesApi(classId, {
  query,
  uploadId = null,
  limit = 20,
  signal
} = {}) {
  const boundedLimit = Math.max(1, Math.min(50, Math.trunc(Number(limit) || 20)));
  const params = new URLSearchParams({ query: String(query || "").trim(), limit: String(boundedLimit) });
  if (uploadId) params.set("upload_id", uploadId);
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/sources/search?${params}`, { signal });
  return res.items || res.results || [];
}

/** Fetch one immutable indexed chunk after the student explicitly selects it. */
export async function getClassSourceChunkApi(classId, chunkRevisionId, { signal } = {}) {
  return apiFetch(
    `/api/classes/${encodeURIComponent(classId)}/sources/chunks/${encodeURIComponent(chunkRevisionId)}`,
    { signal }
  );
}

export async function uploadFileApi(classId, file, kind = null, title = null) {
  const formData = new FormData();
  formData.append("file", file);
  if (kind) formData.append("kind", kind);
  if (title) formData.append("title", title);

  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/uploads`, {
    method: "POST",
    body: formData
  });
}

export async function uploadTextApi(classId, text, kind = "notes", title = "Pasted text") {
  const formData = new FormData();
  formData.append("text", text);
  formData.append("kind", kind);
  formData.append("title", title);

  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/uploads`, {
    method: "POST",
    body: formData
  });
}

export async function getUploadDetailApi(uploadId) {
  return await apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}`);
}

export async function updateUploadApi(uploadId, data) {
  return await apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}`, {
    method: "PATCH",
    body: JSON.stringify(data)
  });
}

export async function deleteUploadApi(uploadId) {
  return await apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}`, {
    method: "DELETE"
  });
}

export async function retryUploadApi(uploadId) {
  return await apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}/retry`, {
    method: "POST"
  });
}

// Syllabus endpoints
export async function getSyllabusApi(classId) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/syllabus`);
}

export async function updateSyllabusApi(classId, outline) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/syllabus`, {
    method: "PUT",
    body: JSON.stringify(outline)
  });
}

// Study Materials endpoints
export async function getStudyMaterialsApi(classId) {
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/study-materials`);
  return res.items || [];
}

export async function getMyStudyMaterialsApi() {
  const res = await apiFetch("/api/me/study-materials");
  return res.items || [];
}

export async function createStudyMaterialApi(classId, {
  kind,
  scope = { kind: "all" },
  options = { count: 10, difficulty: "mixed" },
  additionalInstructions = "",
  destination = "personal",
  language = null,
  operationId = null
}) {
  const body = { kind, scope, options, destination };
  if (typeof language === "string" && language.trim()) body.language = language.trim();
  if (typeof operationId === "string" && operationId.trim()) body.operation_id = operationId.trim();
  if (typeof additionalInstructions === "string" && additionalInstructions.trim()) {
    body.additional_instructions = additionalInstructions.trim();
  }
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/study-materials`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export async function getStudyMaterialDetailApi(materialId) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}`);
}

async function downloadFile(path, fallbackFilename) {
  const token = await getAccessToken();
  const response = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (!response.ok) {
    const error = await readJson(response);
    throw new Error(error.message || "Could not download the document. Try again.");
  }
  const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1]
    || fallbackFilename;
  return { blob: await response.blob(), filename };
}

export async function downloadStudyFileApi(materialId, format = "pdf") {
  if (!["pdf", "latex"].includes(format)) throw new Error("Unsupported study-guide file format.");
  return downloadFile(`/api/study-materials/${encodeURIComponent(materialId)}/files/${format}`,
    `study-guide.${format === "pdf" ? "pdf" : "tex"}`);
}

export async function getUploadDocumentApi(uploadId, detailLevel = "balanced", signal) {
  return apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}/document?detail_level=${encodeURIComponent(detailLevel)}`, { signal });
}

export async function createUploadDocumentApi(uploadId, detailLevel = "balanced", signal) {
  return apiFetch(`/api/uploads/${encodeURIComponent(uploadId)}/document`, {
    method: "POST", body: JSON.stringify({ detail_level: detailLevel }), signal
  });
}

export async function downloadUploadDocumentApi(uploadId, format, detailLevel = "balanced") {
  if (!["pdf", "latex", "original_text"].includes(format)) throw new Error("Unsupported document file format.");
  const suffix = { pdf: "pdf", latex: "tex", original_text: "txt" }[format];
  return downloadFile(`/api/uploads/${encodeURIComponent(uploadId)}/document/files/${format}?detail_level=${encodeURIComponent(detailLevel)}`, `study-document.${suffix}`);
}

export async function deleteStudyMaterialApi(materialId) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}`, {
    method: "DELETE"
  });
}

// Attempts endpoints
export async function submitAttemptApi(materialId, answers, { runId = null, operationId = null } = {}) {
  if (!runId || !operationId) {
    throw new TypeError("A quiz run and operation ID are required for remote answer submission.");
  }
  if (!answers || typeof answers !== "object" || Array.isArray(answers)
    || Object.entries(answers).some(([questionId, answer]) => !questionId || typeof answer !== "string")) {
    throw new TypeError("Quiz answers must map run question IDs to answer strings.");
  }
  const body = { answers };
  body.run_id = runId;
  body.operation_id = operationId;
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/attempts`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export async function createQuizRunApi(materialId, operationId, mode) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/runs`, {
    method: "POST", body: JSON.stringify({ operation_id: operationId, mode })
  });
}

export async function getQuizRunApi(materialId, runId) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/runs/${encodeURIComponent(runId)}`);
}

export async function listQuizRunsApi(materialId) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/runs`);
}

function reasoningPath(materialId, runId, questionId) {
  return `/api/study-materials/${encodeURIComponent(materialId)}/runs/${encodeURIComponent(runId)}/questions/${encodeURIComponent(questionId)}`;
}

function requiredExpectedVersion(expectedVersion) {
  if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new TypeError("A current question attempt version is required.");
  }
  return expectedVersion;
}

export async function submitReasoningApi(
  materialId,
  runId,
  questionId,
  { operationId, text, source = "text", expectedVersion }
) {
  const body = {
    operation_id: operationId,
    text,
    source,
    expected_version: requiredExpectedVersion(expectedVersion)
  };
  return await apiFetch(`${reasoningPath(materialId, runId, questionId)}/reasoning`, {
    method: "POST", body: JSON.stringify(body)
  });
}

export async function retryReasoningApi(materialId, runId, questionId, operationId, expectedVersion) {
  const body = { operation_id: operationId, expected_version: requiredExpectedVersion(expectedVersion) };
  return await apiFetch(`${reasoningPath(materialId, runId, questionId)}/reasoning/retry`, {
    method: "POST", body: JSON.stringify(body)
  });
}

export async function skipQuizQuestionApi(materialId, runId, questionId, operationId, expectedVersion) {
  const body = { operation_id: operationId, expected_version: requiredExpectedVersion(expectedVersion) };
  return await apiFetch(`${reasoningPath(materialId, runId, questionId)}/skip`, {
    method: "POST", body: JSON.stringify(body)
  });
}

export async function submitReasoningAnswerApi(materialId, runId, questionId, { operationId, answer, expectedVersion }) {
  const body = {
    operation_id: operationId,
    answer,
    expected_version: requiredExpectedVersion(expectedVersion)
  };
  return await apiFetch(`${reasoningPath(materialId, runId, questionId)}/answer`, {
    method: "POST", body: JSON.stringify(body)
  });
}

export async function retryReasoningFeedbackApi(materialId, runId, questionId, operationId, expectedVersion) {
  const body = { operation_id: operationId, expected_version: requiredExpectedVersion(expectedVersion) };
  return await apiFetch(`${reasoningPath(materialId, runId, questionId)}/feedback/retry`, {
    method: "POST", body: JSON.stringify(body)
  });
}

// Flag endpoint
export async function flagMaterialItemApi(materialId, questionId, reason = null) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/flags`, {
    method: "POST",
    body: JSON.stringify({ question_id: questionId, reason })
  });
}
