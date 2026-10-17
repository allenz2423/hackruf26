import assert from "node:assert/strict";
import test from "node:test";

import { focusStudyPlayer } from "../src/ui.js";

function installWindow(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  const scrollCalls = [];
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { scrollTo: (...args) => scrollCalls.push(args) }
  });
  t.after(() => previous
    ? Object.defineProperty(globalThis, "window", previous)
    : delete globalThis.window);
  return scrollCalls;
}

test("opening a study player focuses its heading and returns the viewport to the top", (t) => {
  const scrollCalls = installWindow(t);
  const focusCalls = [];
  const attributes = new Map();
  const target = {
    tabIndex: -1,
    setAttribute: (name, value) => attributes.set(name, value),
    focus: (options) => focusCalls.push(options)
  };

  focusStudyPlayer({ querySelector: () => target });

  assert.equal(attributes.get("tabindex"), "-1");
  assert.deepEqual(focusCalls, [{ preventScroll: true }]);
  assert.deepEqual(scrollCalls, [[0, 0]]);
});

test("the initial flashcard receives focus without changing its keyboard tab stop", (t) => {
  const scrollCalls = installWindow(t);
  const focusCalls = [];
  const target = {
    tabIndex: 0,
    setAttribute() {
      assert.fail("the focusable flashcard should keep its existing tab stop");
    },
    focus: (options) => focusCalls.push(options)
  };

  focusStudyPlayer({ querySelector: () => target });

  assert.deepEqual(focusCalls, [{ preventScroll: true }]);
  assert.deepEqual(scrollCalls, [[0, 0]]);
});
