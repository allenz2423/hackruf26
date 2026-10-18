import {
  createClassApi,
  getUploadsApi,
  getUploadDetailApi,
  getStudyMaterialDetailApi,
  uploadFileApi,
  uploadTextApi
} from "./api.js";
import { getBackendClassId, getClassById, updateClass } from "./classes.js";
import { colorId } from "./class-style.js";
import { getUploadedFile } from "./file-store.js";

const POLL_INTERVAL_MS = 3000;
const PROCESSING_WAIT_MS = 10 * 60 * 1000;

export { getBackendClassId };

export function getBackendUploadId(upload) {
  return upload.backendId || (upload.isRemote ? upload.id : null);
}

export function mergeAcceptedUploadState(upload, detail) {
  const next = {
    ...upload,
    backendId: detail.id,
    status: detail.status,
    progress: detail.processing_progress ?? 0,
    isRemote: true,
    ...(detail.summary ? { summary: detail.summary } : {})
  };
  delete next.localOnly;
  return next;
}

function throwIfAborted(signal) {
  signal?.throwIfAborted();
}

function delay(signal) {
  throwIfAborted(signal);
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, POLL_INTERVAL_MS);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function waitForJob(read, { signal, onStatus, timeoutMs = PROCESSING_WAIT_MS } = {}) {
  const deadline = Number.isFinite(timeoutMs) ? Date.now() + timeoutMs : Number.POSITIVE_INFINITY;
  while (true) {
    throwIfAborted(signal);
    const detail = await read();
    throwIfAborted(signal);
    await onStatus?.(detail);
    if (["ready", "failed"].includes(detail.status) || Date.now() >= deadline) {
      return detail;
    }
    await delay(signal);
  }
}

async function saveUploadState(klass, upload, detail) {
  // The server has accepted the source. Keep its local recovery copy, but stop
  // presenting the item as browser-only or unsynced.
  Object.assign(upload, mergeAcceptedUploadState(upload, detail));
  delete upload.localOnly;
  const current = getClassById(klass.id) || klass;
  const uploads = current.uploads.map((item) => item.id === upload.id
    ? mergeAcceptedUploadState(item, detail)
    : item);
  await updateClass(klass.id, { uploads });
  klass.uploads = uploads;
}

function fileKind(upload, file) {
  const kind = String(upload.kind || "").toLowerCase();
  if (["notes", "document", "syllabus", "slides", "lecture_audio", "lecture_video"].includes(kind)) {
    return kind;
  }
  if (kind === "lecture") {
    return /\.(mp3|m4a|wav)$/i.test(file.name) ? "lecture_audio" : "lecture_video";
  }
  return null; // Let the backend infer an unknown file kind from its filename.
}

export function selectStudyUploads(klass, { scope = "all", selectedUploadIds } = {}) {
  if (selectedUploadIds !== undefined) {
    if (!Array.isArray(selectedUploadIds) || !selectedUploadIds.length) {
      throw new Error("Select at least one uploaded material.");
    }
    const ids = new Set(selectedUploadIds);
    const uploads = klass.uploads.filter((upload) => ids.has(upload.id));
    if (uploads.length !== ids.size) {
      throw new Error("A selected material is no longer in this class. Choose your sources again.");
    }
    return uploads;
  }
  if (scope === "notes") {
    return klass.uploads.filter((upload) => String(upload.kind).toLowerCase() === "notes");
  }
  if (scope === "syllabus") {
    return klass.uploads.filter((upload) => String(upload.kind).toLowerCase() === "syllabus");
  }
  return [...klass.uploads];
}

async function prepareBackendClass(klass, { signal } = {}) {
  throwIfAborted(signal);
  let classId = getBackendClassId(klass);
  let remoteUploads = [];
  if (classId) {
    try {
      remoteUploads = await getUploadsApi(classId);
    } catch (error) {
      if (error.status !== 404) throw error;
      classId = null; // The local mapping can outlive a reset backend database.
    }
  }
  if (!classId) {
    throwIfAborted(signal);
    const remoteClass = await createClassApi(klass.name, { color: colorId(klass.color), icon: klass.glyph || null });
    classId = remoteClass.id;
    klass.backendId = classId;
    await updateClass(klass.id, { backendId: classId });
  }
  throwIfAborted(signal);
  return { classId, remoteUploads };
}

/** Reconcile browser-only sources with SQLite before requesting generation. */
export async function prepareStudySources(klass, { scope = "all", selectedUploadIds, signal, onProgress } = {}) {
  const uploads = selectStudyUploads(klass, { scope, selectedUploadIds });
  if (!uploads.length) {
    throw new Error("Add course material matching the selected scope in the Library tab first.");
  }
  throwIfAborted(signal);
  onProgress?.("Preparing your course materials…");
  const { classId, remoteUploads } = await prepareBackendClass(klass, { signal });
  const remoteById = new Map(remoteUploads.map((upload) => [upload.id, upload]));
  const sources = [];
  for (const upload of uploads) {
    throwIfAborted(signal);
    let remote = remoteById.get(getBackendUploadId(upload));
    if (!remote) {
      onProgress?.(`Uploading ${upload.name || "course material"}…`);
      const file = await getUploadedFile(upload.id);
      throwIfAborted(signal);
      if (file) {
        remote = await uploadFileApi(classId, file, fileKind(upload, file), upload.name || file.name);
      } else if (typeof upload.content === "string" && upload.content.trim()) {
        const kind = String(upload.kind).toLowerCase();
        const textKind = ["notes", "document", "syllabus"].includes(kind) ? kind : "notes";
        remote = await uploadTextApi(classId, upload.content, textKind, upload.name || "Course notes");
      } else {
        throw new Error(`The original file for ${upload.name || "this upload"} is unavailable. Upload it again in the Library tab.`);
      }
      // Save the ID before waiting, so interrupted requests reuse the uploaded file.
      await saveUploadState(klass, upload, remote);
    }
    sources.push({ upload, remote });
  }
  const uploadIds = [];
  for (const { upload, remote } of sources) {
    onProgress?.(`Processing ${upload.name || "course material"}…`);
    const detail = await waitForJob(() => getUploadDetailApi(remote.id), {
      signal,
      onStatus: (current) => saveUploadState(klass, upload, current)
    });
    if (detail.status === "failed") {
      throw new Error(`Processing failed for ${upload.name || "this upload"}. Retry it in the Library tab before generating.`);
    }
    if (detail.status !== "ready") {
      throw new Error(`${upload.name || "This upload"} is still processing. Try generating again when it is ready.`);
    }
    uploadIds.push(detail.id);
  }
  return { classId, uploadIds: [...new Set(uploadIds)] };
}

/** Prepare the class UUID and actual notes before opening its Live connection. */
export async function prepareTutorClass(klass, options = {}) {
  if (klass.uploads?.length) return (await prepareStudySources(klass, options)).classId;
  return (await prepareBackendClass(klass, options)).classId;
}

/**
 * Prepare the class and the uploads the student chose for tutor context.
 * `selectedUploadIds` null means every upload; the returned `uploadIds` is then null too.
 */
export async function prepareTutorSources(klass, { selectedUploadIds = null, signal } = {}) {
  if (selectedUploadIds === null) return { classId: await prepareTutorClass(klass, { signal }), uploadIds: null };
  const existing = new Set((klass.uploads || []).map((upload) => upload.id));
  const chosen = selectedUploadIds.filter((id) => existing.has(id));
  if (!chosen.length) return { classId: (await prepareBackendClass(klass, { signal })).classId, uploadIds: [] };
  return prepareStudySources(klass, { scope: "selected", selectedUploadIds: chosen, signal });
}

export function waitForStudyMaterial(materialId, options = {}) {
  return waitForJob(() => getStudyMaterialDetailApi(materialId), options);
}

export function waitForUpload(uploadId, options = {}) {
  return waitForJob(() => getUploadDetailApi(uploadId), options);
}
