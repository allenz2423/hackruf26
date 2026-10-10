import { SAMPLE_VERSION, starterClasses } from "./static-data.js";

const STORE_KEY = "screen-tutor-classes";
const VERSION_KEY = "screen-tutor-sample-version";

function cloneClasses(classes) {
  return JSON.parse(JSON.stringify(classes));
}

function normalizeClass(klass) {
  return {
    nextExam: "",
    focus: "",
    updatedAt: new Date().toISOString(),
    ...klass,
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

function seedClasses() {
  const seeded = cloneClasses(starterClasses);
  saveClasses(seeded);
  localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
  return seeded;
}

function readClasses() {
  const saved = localStorage.getItem(STORE_KEY);
  if (!saved) return seedClasses();

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed)) return seedClasses();

    const version = Number(localStorage.getItem(VERSION_KEY) || 0);
    const hasOnlyEmptyStarter = parsed.length <= 2 && parsed.every((klass) => !klass.uploads?.length && !klass.materials?.length);
    if (version < SAMPLE_VERSION && hasOnlyEmptyStarter) return seedClasses();

    if (version < SAMPLE_VERSION) {
      const migrated = parsed.map(migrateSampleClass);
      saveClasses(migrated);
      localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
      return migrated;
    }

    return parsed.map(normalizeClass);
  } catch {
    return seedClasses();
  }
}

function saveClasses(classes) {
  localStorage.setItem(STORE_KEY, JSON.stringify(classes));
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

export function createClass(name) {
  const classes = readClasses();
  const nextClass = {
    id: makeId(name),
    name,
    color: "#7c3aed",
    icon: name.trim().charAt(0).toUpperCase() || "C",
    nextExam: "",
    focus: "New class workspace",
    uploads: [],
    materials: [],
    updatedAt: new Date().toISOString()
  };

  classes.push(nextClass);
  saveClasses(classes);
  return nextClass;
}

export function updateClass(classId, patch) {
  const classes = readClasses();
  const index = classes.findIndex((item) => item.id === classId);
  if (index === -1) return null;

  classes[index] = { ...classes[index], ...patch, updatedAt: new Date().toISOString() };
  saveClasses(classes);
  return classes[index];
}

export function deleteClass(classId) {
  saveClasses(readClasses().filter((item) => item.id !== classId));
}

export function resetSampleData() {
  const current = readClasses();
  const sampleIds = new Set(starterClasses.map((item) => item.id));
  const next = cloneClasses(starterClasses).map((sample) => {
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
  const restored = [...next, ...customClasses];
  saveClasses(restored);
  localStorage.setItem(VERSION_KEY, String(SAMPLE_VERSION));
  return restored;
}
