import {
  createClassApi,
  getUploadsApi,
  getUploadDetailApi,
  getStudyMaterialDetailApi,
  uploadFileApi,
  uploadTextApi
} from "./api.js";
import { getClassById, updateClass } from "./classes.js";
import { getUploadedFile } from "./file-store.js";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const POLL_INTERVAL_MS = 3000;
const PROCESSING_WAIT_MS = 10 * 60 * 1000;

export function getBackendClassId(klass) {
  if (UUID_PATTERN.test(klass.backendId || "")) return klass.backendId;
  return UUID_PATTERN.test(klass.id || "") ? klass.id : null;
}

export function getBackendUploadId(upload) {
  return upload.backendId || (upload.isRemote ? upload.id : null);
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

async function waitForJob(read, { signal, onStatus } = {}) {
  const deadline = Date.now() + PROCESSING_WAIT_MS;
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
  const patch = {
    backendId: detail.id,
    status: detail.status,
    progress: detail.processing_progress ?? 0,
    ...(detail.summary ? { summary: detail.summary } : {})
  };
  Object.assign(upload, patch);
  const current = getClassById(klass.id) || klass;
  const uploads = current.uploads.map((item) => item.id === upload.id ? { ...item, ...patch } : item);
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

function selectedUploads(klass, scope) {
  if (scope === "notes") {
    return klass.uploads.filter((upload) => String(upload.kind).toLowerCase() === "notes");
  }
  if (scope === "syllabus") {
    return klass.uploads.filter((upload) => String(upload.kind).toLowerCase() === "syllabus");
  }
  return [...klass.uploads];
}

/** Reconcile browser-only sources with SQLite before requesting generation. */
export async function prepareStudySources(klass, { scope = "all", signal, onProgress } = {}) {
  const uploads = selectedUploads(klass, scope);
  if (!uploads.length) {
    throw new Error("Add course material matching the selected scope in the Library tab first.");
  }
  throwIfAborted(signal);
  onProgress?.("Preparing your course materials…");
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
    const remoteClass = await createClassApi(klass.name);
    classId = remoteClass.id;
    klass.backendId = classId;
    await updateClass(klass.id, { backendId: classId });
  }
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
  return { classId, uploadIds };
}

export function waitForStudyMaterial(materialId, options = {}) {
  return waitForJob(() => getStudyMaterialDetailApi(materialId), options);
}

export function waitForUpload(uploadId, options = {}) {
  return waitForJob(() => getUploadDetailApi(uploadId), options);
}
