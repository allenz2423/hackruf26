const STORE_KEY = "screen-tutor-classes";

const starterClasses = [
  {
    id: "math-151",
    name: "Math 151",
    color: "#2563eb",
    icon: "M",
    uploads: [],
    materials: []
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

function readClasses() {
  const saved = localStorage.getItem(STORE_KEY);
  if (!saved) return starterClasses;

  try {
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed : starterClasses;
  } catch {
    return starterClasses;
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
