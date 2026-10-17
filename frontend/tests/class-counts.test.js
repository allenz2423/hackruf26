import test from "node:test";
import assert from "node:assert/strict";
import { getClassDisplayCounts } from "../src/class-counts.js";

test("backend class cards use server counts and include the owner's personal study sets", () => {
  const personalMaterialCounts = new Map([["bio170", 3]]);
  assert.deepEqual(
    getClassDisplayCounts({
      uploads: [],
      materials: [],
      uploadCount: 2,
      materialCount: 4
    }, "bio170", personalMaterialCounts),
    { uploads: 2, materials: 7 }
  );
});

test("browser-only classes continue to use their locally stored arrays", () => {
  assert.deepEqual(
    getClassDisplayCounts({
      uploads: [{ id: "upload" }],
      materials: [{ id: "guide" }],
      uploadCount: 99,
      materialCount: 99
    }, null, new Map()),
    { uploads: 1, materials: 1 }
  );
});

test("missing or invalid server counts fall back to cached class arrays", () => {
  assert.deepEqual(
    getClassDisplayCounts({
      uploads: [{ id: "upload" }],
      materials: [{ id: "guide" }],
      uploadCount: Number.NaN,
      materialCount: null
    }, "bio170", new Map()),
    { uploads: 1, materials: 1 }
  );
});
