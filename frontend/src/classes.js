import { SAMPLE_ALGEBRA_MATERIALS } from "./sample-assessments.js";
import { SAMPLE_VERSION, starterClasses } from "./static-data.js";
import {
  getClassesApi,
  createClassApi,
  updateClassApi,
  deleteClassApi
} from "./api.js";

const STORE_KEY = "screen-tutor-classes";
const VERSION_KEY = "screen-tutor-sample-version";
const SAMPLE_SEED_KEY = `${STORE_KEY}-algebra-examples-v1`;
const OWNER_KEY = `${STORE_KEY}-owner`;

// Earlier builds saved saturated defaults; map them to the current ink palette.
const LEGACY_CLASS_COLORS = {
  "#2563eb": "#3f6577",
  "#7c3aed": "#7a4a72",
  "#059669": "#4f7340",
  "#d97706": "#8a6414",
  "#dc2626": "#b04a2f",
  "#0891b2": "#26707a"
};
const CLASS_PALETTE = ["#3f6577", "#7a4a72", "#4f7340", "#8a6414", "#b04a2f", "#26707a"];
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function getBackendClassId(klass) {
  if (UUID_PATTERN.test(klass?.backendId || "")) return klass.backendId;
  return UUID_PATTERN.test(klass?.id || "") ? klass.id : null;
}

function cloneData(value) {
  return JSON.parse(JSON.stringify(value));
}

function normalizeClass(klass) {
  return {
    nextExam: "",
    focus: "",
    updatedAt: new Date().toISOString(),
    ...klass,
    color: LEGACY_CLASS_COLORS[klass.color] || klass.color,
    uploads: Array.isArray(klass.uploads) ? klass.uploads : [],
    materials: Array.isArray(klass.materials) ? klass.materials : []
  };
}

function migrateSampleClass(savedClass) {
  const sample = starterClasses.find((item) => item.id === savedClass.id);
  if (!sample) return normalizeClass(savedClass);

  const saved = normalizeClass(savedClass);
  const mergeRecords = (sampleRecords, savedRecords) => {
    const byId = new Map(savedRecords.map((item) => [item.id, item]));
    const merged = sampleRecords.map((item) => ({ ...item, ...byId.get(item.id) }));
    const sampleIds = new Set(sampleRecords.map((item) => item.id));
    return [...merged, ...savedRecords.filter((item) => !sampleIds.has(item.id))];
  };

  return {
    ...sample,
    ...saved,
    uploads: mergeRecords(sample.uploads, saved.uploads),
    materials: mergeRecords(sample.materials, saved.materials)
  };
}

function addAlgebraExamples(classes) {
  const nextClasses = cloneData(classes);
  let mathClass = nextClasses.find((item) => item.id === "math-151")
    || nextClasses.find((item) => item.name?.trim().toLowerCase() === "math 151");

  if (!mathClass) {
    mathClass = {
      id: "math-151",
      name: "Math 151",
      color: "#3f6577",
      icon: "M",
      uploads: [],
      materials: []
    };
    nextClasses.unshift(mathClass);
  }

  mathClass.uploads = Array.isArray(mathClass.uploads) ? mathClass.uploads : [];
  mathClass.materials = Array.isArray(mathClass.materials) ? mathClass.materials : [];
  const existingIds = new Set(mathClass.materials.map((material) => material.id));
  const missingExamples = SAMPLE_ALGEBRA_MATERIALS
    .filter((material) => !existingIds.has(material.id));
  mathClass.materials.unshift(...cloneData(missingExamples));
  return nextClasses;
}

// Only the demo account starts with the built-in sample classes and study sets.
let demoContent = false;

export function setDemoContent(enabled) {
  demoContent = Boolean(enabled);
}

// Everyone else keeps just their own classes. Sample classes that were never
// synced, and sample files and study sets inside the rest, are removed.
function withoutDemoContent(classes) {
  const starterIds = new Set(starterClasses.map((item) => item.id));
  return classes
    .filter((klass) => !(starterIds.has(klass.id) && !klass.backendId))
    .map((klass) => ({
      ...klass,
      uploads: klass.uploads.filter((upload) => !upload.isSample),
      materials: klass.materials.filter((material) => !material.isSample)
    }));
}

function saveClasses(classes) {
  localStorage.setItem(STORE_KEY, JSON.stringify(classes));
}

function seedClasses() {
  const seeded = demoContent ? addAlgebraExamples(cloneData(starterClasses)) : [];
  saveClasses(seeded);
  localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
  localStorage.setItem(SAMPLE_SEED_KEY, "1");
  return seeded;
}

function readClasses() {
  const saved = localStorage.getItem(STORE_KEY);
  if (!saved) return seedClasses();

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed)) return seedClasses();

    const version = Number(localStorage.getItem(VERSION_KEY) || 0);
    let classes = parsed.map((klass) => demoContent && version < SAMPLE_VERSION
      ? migrateSampleClass(klass)
      : normalizeClass(klass));

    if (!demoContent) {
      const own = withoutDemoContent(classes);
      if (JSON.stringify(own) !== JSON.stringify(classes)) saveClasses(own);
      return own;
    }

    if (version < SAMPLE_VERSION || localStorage.getItem(SAMPLE_SEED_KEY) !== "1") {
      classes = addAlgebraExamples(classes);
      saveClasses(classes);
      localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
      localStorage.setItem(SAMPLE_SEED_KEY, "1");
    }
    return classes;
  } catch {
    return seedClasses();
  }
}

function makeId(name) {
  return `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "")}-${Date.now()}`;
}

export function getClasses() {
  return readClasses();
}

export function getClassById(classId) {
  return readClasses().find((item) => item.id === classId) || null;
}

/**
 * Synchronize classes from backend API while strictly preserving
 * all local classes and added materials so no user-created classes are lost.
 */
export async function syncClassesFromApi() {
  try {
    const remoteClasses = await getClassesApi();
    if (!Array.isArray(remoteClasses)) return getClasses();

    const current = readClasses();
    const currentById = new Map(current.map((c) => [c.id, c]));
    const currentByBackendId = new Map(current.filter((c) => c.backendId).map((c) => [c.backendId, c]));
    const currentByName = new Map(current.map((c) => [c.name.trim().toLowerCase(), c]));
    const matchedLocalIds = new Set();

    const mergedRemote = remoteClasses.map((rc, idx) => {
      let local = currentByBackendId.get(rc.id) || currentById.get(rc.id);
      if (!local) {
        const candidate = currentByName.get(rc.name.trim().toLowerCase());
        if (candidate && !getBackendClassId(candidate) && !matchedLocalIds.has(candidate.id)) local = candidate;
      }
      local ||= {};
      if (local.id) matchedLocalIds.add(local.id);
      return {
        ...local,
        id: local.id || rc.id,
        backendId: rc.id,
        name: rc.name,
        color: local.color || CLASS_PALETTE[idx % CLASS_PALETTE.length],
        icon: rc.name.trim().charAt(0).toUpperCase() || "C",
        nextExam: local.nextExam || "",
        focus: local.focus || "Class workspace",
        uploads: local.uploads || [],
        materials: local.materials || [],
        uploadCount: rc.upload_count ?? (local.uploads?.length || 0),
        materialCount: rc.material_count ?? (local.materials?.length || 0),
        updatedAt: rc.updated_at || new Date().toISOString()
      };
    });

    const remoteIds = new Set(mergedRemote.map((rc) => rc.id));

    // Keep all existing classes that aren't on remote so newly added classes always stay!
    const preservedLocal = current.filter((c) => !remoteIds.has(c.id) && !matchedLocalIds.has(c.id));

    const combined = [...mergedRemote, ...preservedLocal];
    saveClasses(combined);
    return combined;
  } catch (err) {
    console.warn("Could not sync classes from API:", err);
    return getClasses();
  }
}

export async function createClass(name) {
  const trimmed = name.trim();
  const classes = readClasses();
  const existing = classes.find((c) => c.name.trim().toLowerCase() === trimmed.toLowerCase());
  if (existing) return existing;
  let created = null;

  try {
    const remote = await createClassApi(trimmed);
    created = {
      id: remote.id,
      backendId: remote.id,
      name: remote.name,
      color: CLASS_PALETTE[Math.floor(Math.random() * CLASS_PALETTE.length)],
      icon: remote.name.charAt(0).toUpperCase() || "C",
      nextExam: "",
      focus: "New class workspace",
      uploads: [],
      materials: [],
      updatedAt: remote.updated_at || new Date().toISOString()
    };
  } catch (e) {
    console.warn("Failed to create class via API, falling back to local:", e);
  }

  if (!created) {
    created = {
      id: makeId(trimmed),
      name: trimmed,
      color: "#7a4a72",
      icon: trimmed.charAt(0).toUpperCase() || "C",
      nextExam: "",
      focus: "New class workspace",
      uploads: [],
      materials: [],
      updatedAt: new Date().toISOString()
    };
  }

  classes.push(created);
  saveClasses(classes);
  return created;
}

export async function updateClass(classId, patch) {
  const classes = readClasses();
  const index = classes.findIndex((item) => item.id === classId);
  if (index === -1) return null;
  const remoteId = getBackendClassId(classes[index]);
  if (remoteId && patch.name) await updateClassApi(remoteId, patch.name);

  classes[index] = { ...classes[index], ...patch, updatedAt: new Date().toISOString() };
  saveClasses(classes);
  return classes[index];
}

export async function deleteClass(classId) {
  const klass = getClassById(classId);
  const remoteId = getBackendClassId(klass);
  if (remoteId) {
    try {
      await deleteClassApi(remoteId);
    } catch (e) {
      if (e.status !== 404) throw e;
    }
  }

  saveClasses(readClasses().filter((item) => item.id !== classId));
}

export function resetSampleData() {
  if (!demoContent) return readClasses();
  const current = readClasses();
  const samples = addAlgebraExamples(cloneData(starterClasses));
  const sampleIds = new Set(samples.map((item) => item.id));
  const restored = samples.map((sample) => {
    const saved = current.find((item) => item.id === sample.id);
    if (!saved) return sample;

    const sampleUploadIds = new Set(sample.uploads.map((item) => item.id));
    const sampleMaterialIds = new Set(sample.materials.map((item) => item.id));
    return {
      ...sample,
      uploads: [...sample.uploads, ...saved.uploads.filter((item) => !sampleUploadIds.has(item.id))],
      materials: [...sample.materials, ...saved.materials.filter((item) => !sampleMaterialIds.has(item.id))]
    };
  });
  const customClasses = current.filter((item) => !sampleIds.has(item.id));
  const next = [...restored, ...customClasses];
  saveClasses(next);
  localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
  localStorage.setItem(SAMPLE_SEED_KEY, "1");
  return next;
}

export function hasLocalClassData() {
  return localStorage.getItem(STORE_KEY) !== null || localStorage.getItem(OWNER_KEY) !== null;
}

// Remove everything cached for the previous account or session.
export function clearLocalClassData() {
  for (const key of [STORE_KEY, VERSION_KEY, SAMPLE_SEED_KEY, OWNER_KEY]) {
    localStorage.removeItem(key);
  }
  sessionStorage.removeItem("tutor_initial_question");
}

// Record who the cached classes belong to. A cache that belongs to someone else is wiped first.
// A cache with no recorded owner predates accounts, so the first account to log in keeps it.
export function claimLocalClassData(userId) {
  const owner = localStorage.getItem(OWNER_KEY);
  if (owner && owner !== userId) {
    clearLocalClassData();
    return true;
  }
  localStorage.setItem(OWNER_KEY, userId);
  return false;
}
