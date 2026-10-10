import { createClient } from "@supabase/supabase-js";

// Supabase configuration
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || "https://rmwpbaxufddzerxfbtwa.supabase.co";
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || "sb_publishable_z43N15w5KEE0byz46MMlFg_g-G6uRjE";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true
  }
});

// Authentication helpers
export async function getAccessToken() {
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token || "";
}

export async function getCurrentUser() {
  const { data: { user } } = await supabase.auth.getUser();
  return user;
}

export async function signInWithEmail(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

export async function signUpWithEmail(email, password, displayName = "") {
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      data: { display_name: displayName }
    }
  });
  if (error) throw error;
  return data;
}

export async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
}

export function onAuthStateChange(callback) {
  return supabase.auth.onAuthStateChange((event, session) => {
    callback(event, session);
  });
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

  const response = await fetch(path, {
    ...options,
    headers
  });

  const payload = await readJson(response);

  if (!response.ok) {
    const message = payload?.message || payload?.detail || payload?.error || `Request failed with status ${response.status}`;
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

// Gemini Live token request
export async function requestLiveToken(classId = null) {
  const body = classId ? { class_id: classId } : {};
  return await apiFetch("/api/live-token", {
    method: "POST",
    body: JSON.stringify(body)
  });
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

export async function createClassApi(name) {
  return await apiFetch("/api/classes", {
    method: "POST",
    body: JSON.stringify({ name: name.trim() })
  });
}

export async function updateClassApi(classId, name) {
  return await apiFetch(`/api/classes/${encodeURIComponent(classId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name: name.trim() })
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
