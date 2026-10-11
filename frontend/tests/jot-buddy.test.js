import assert from "node:assert/strict";
import test from "node:test";

import { captionFor, expressionFor } from "../src/jot-buddy.js";

test("praise makes the Jot proud", () => {
  for (const text of ["Great job on that step!", "Well done.", "Nice work, that's the idea.", "Excellent reasoning."]) {
    assert.equal(expressionFor(text), "proud", text);
  }
});

test("a right answer makes it celebrate", () => {
  for (const text of ["Exactly!", "That's right, it's a circle.", "Correct. The radius is 3.", "You got it!", "Perfect."]) {
    assert.equal(expressionFor(text), "celebrate", text);
  }
});

test("a mistake makes it concerned, even next to praise", () => {
  for (const text of [
    "Not quite. Check the sign.",
    "Almost! You dropped a negative.",
    "That's incorrect, but great effort.",
    "Careful with the square root here.",
    "There's a small error in line two."
  ]) {
    assert.equal(expressionFor(text), "concerned", text);
  }
});

test("an unclear request makes it confused", () => {
  for (const text of ["I'm not sure what you mean.", "Could you clarify which problem?", "I can't see the screen clearly.", "Can you share the whole page?"]) {
    assert.equal(expressionFor(text), "confused", text);
  }
});

test("ordinary explanations leave the face alone", () => {
  for (const text of ["", null, undefined, "Let's look at part (a).", "The derivative of sin is cos.", "Try converting to polar coordinates first."]) {
    assert.equal(expressionFor(text), null, String(text));
  }
});

test("short praise at the start of a reply counts", () => {
  for (const [text, expected] of [
    ["Yes! It's a circle centered at the origin.", "celebrate"],
    ["Yep, that's the radius.", "celebrate"],
    ["Right! And what is r here?", "celebrate"],
    ["That's it! A circle of radius 3.", "celebrate"],
    ["You've got it. Now part (b).", "celebrate"],
    ["You're right about the radius.", "celebrate"],
    ["Great! Now let's convert it.", "proud"],
    ["Nice, that's a good start.", "proud"],
    ["Awesome. Next, the angle.", "proud"],
    ["Great observation about the radius.", "proud"],
    ["Good catch on the sign.", "proud"]
  ]) {
    assert.equal(expressionFor(text), expected, text);
  }
});

test("openings that are not praise stay neutral", () => {
  for (const text of ["Right, so let's look at part (b).", "Let's start with part (a).", "Okay, think about the radius.", "So the angle changes."]) {
    assert.equal(expressionFor(text), null, text);
  }
});

test("short replies are shown whole in the speech bubble", () => {
  assert.equal(captionFor("Exactly! That's a circle."), "Exactly! That's a circle.");
  assert.equal(captionFor("  spaced \n out  "), "spaced out");
  assert.equal(captionFor(""), "");
});

test("long replies show the latest sentence, trimmed with an ellipsis", () => {
  const long = "Exactly, that is a circle of radius three. " + "Now let's think about what the angle does as it sweeps around the origin. " + "What happens to the area if the radius doubles?";
  const caption = captionFor(long);
  assert.ok(caption.startsWith("…"), caption);
  assert.ok(caption.endsWith("What happens to the area if the radius doubles?"), caption);
  assert.ok(caption.length <= 141, String(caption.length));
});

test("a single very long sentence keeps its end and starts on a whole word", () => {
  const caption = captionFor("word ".repeat(80) + "finally the end");
  assert.ok(caption.startsWith("…word"), caption);
  assert.ok(caption.endsWith("finally the end"));
  assert.ok(caption.length <= 141);
});
