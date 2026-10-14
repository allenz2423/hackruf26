import assert from "node:assert/strict";
import test from "node:test";

import { validateUpload } from "../src/library.js";

const MIB = 1024 * 1024;

test("accepts a file at the 100 MiB upload limit", () => {
  assert.equal(validateUpload({ name: "course.pdf", size: 100 * MIB }), null);
});

test("rejects a file larger than the 100 MiB upload limit", () => {
  const error = validateUpload({ name: "course.pdf", size: 100 * MIB + 1 });
  assert.match(error, /100/);
});
