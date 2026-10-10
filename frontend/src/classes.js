import { SAMPLE_ALGEBRA_MATERIALS } from "./sample-assessments.js";
import { SAMPLE_VERSION, starterClasses } from "./static-data.js";

const STORE_KEY = "screen-tutor-classes";
const VERSION_KEY = "screen-tutor-sample-version";
const SAMPLE_SEED_KEY = `${STORE_KEY}-algebra-examples-v1`;

function cloneData(value) {
  return JSON.parse(JSON.stringify(value));
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

function addAlgebraExamples(classes) {
  const nextClasses = cloneData(classes);
  let mathClass = nextClasses.find((item) => item.id === "math-151")
    || nextClasses.find((item) => item.name?.trim().toLowerCase() === "math 151");

  if (!mathClass) {
    mathClass = {
      id: "math-151",
      name: "Math 151",
      color: "#2563eb",
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

function saveClasses(classes) {
  localStorage.setItem(STORE_KEY, JSON.stringify(classes));
}

function seedClasses() {
  const seeded = addAlgebraExamples(cloneData(starterClasses));
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
    let classes = parsed.map((klass) => version < SAMPLE_VERSION
      ? migrateSampleClass(klass)
      : normalizeClass(klass));

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
