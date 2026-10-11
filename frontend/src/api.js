const authListeners = new Set();

// The login session lives in an HttpOnly cookie, so scripts never see or store a token.
export async function getAccessToken() {
  return "";
}

// Who is logged in? Resolves to the account, or null for a guest.
export async function getCurrentUser() {
  try {
    const payload = await apiFetch("/api/auth/me");
    return payload.user || null;
  } catch {
    return null;
  }
}

export async function signIn(identifier, password) {
  const payload = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier, password })
  });
  return payload.user;
}

export async function signUp({ username, password, email = "", displayName = "" }) {
  const payload = await apiFetch("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({
      username,
      password,
      email: email || null,
      display_name: displayName || null
    })
  });
  return payload.user;
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

// Uploads endpoints
export async function getUploadsApi(classId) {
  const res = await apiFetch(`/api/classes/${encodeURIComponent(classId)}/uploads`);
  return res.items || [];
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

export async function createStudyMaterialApi(classId, { kind, scope = { kind: "all" }, options = { count: 10, difficulty: "mixed" } }) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}/study-materials`, {
    method: "POST",
    body: JSON.stringify({ kind, scope, options })
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
export async function submitAttemptApi(materialId, answers) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/attempts`, {
    method: "POST",
    body: JSON.stringify({ answers })
  });
}

// Flag endpoint
export async function flagMaterialItemApi(materialId, questionId, reason = null) {
  return await apiFetch(`/api/study-materials/${encodeURIComponent(materialId)}/flags`, {
    method: "POST",
    body: JSON.stringify({ question_id: questionId, reason })
  });
}
