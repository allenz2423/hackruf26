import { SAMPLE_ALGEBRA_MATERIALS } from "./sample-assessments.js";

const STORE_KEY = "screen-tutor-classes";
const SAMPLE_SEED_KEY = `${STORE_KEY}-algebra-examples-v1`;

function cloneData(value) {
  return JSON.parse(JSON.stringify(value));
}

const starterClasses = [
  {
    id: "math-151",
    name: "Math 151",
    color: "#2563eb",
    icon: "M",
    uploads: [],
    materials: cloneData(SAMPLE_ALGEBRA_MATERIALS)
  },
  {
    id: "biology",
    name: "Biology",
    color: "#059669",
    icon: "B",
    uploads: [],
    materials: []
  }
];

function saveClasses(classes) {
  localStorage.setItem(STORE_KEY, JSON.stringify(classes));
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

function readClasses() {
  const saved = localStorage.getItem(STORE_KEY);
  if (!saved) return cloneData(starterClasses);

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed)) return cloneData(starterClasses);
    if (localStorage.getItem(SAMPLE_SEED_KEY) !== "1") {
      const seeded = addAlgebraExamples(parsed);
      saveClasses(seeded);
      localStorage.setItem(SAMPLE_SEED_KEY, "1");
      return seeded;
    }
    return parsed;
  } catch {
    return cloneData(starterClasses);
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
    uploads: [],
    materials: []
  };

  classes.push(nextClass);
  saveClasses(classes);
  return nextClass;
}

export function updateClass(classId, patch) {
  const classes = readClasses();
  const index = classes.findIndex((item) => item.id === classId);
  if (index === -1) return null;

  classes[index] = { ...classes[index], ...patch };
  saveClasses(classes);
  return classes[index];
}

export function deleteClass(classId) {
  saveClasses(readClasses().filter((item) => item.id !== classId));
}
