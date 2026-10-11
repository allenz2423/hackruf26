import assert from "node:assert/strict";
import { test } from "node:test";
import { AnnotationController } from "../src/annotations.js";
import { ScreenCaptureController } from "../src/screen-capture.js";

class Element {
  constructor() {
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.clientHeight = this.scrollHeight = 100;
    this.clientWidth = this.scrollWidth = 100;
    this.events = new Map();
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, callback) { this.events.set(type, callback); }
  removeEventListener(type) { this.events.delete(type); }
  getBoundingClientRect() { return { width: 800, height: 400 }; }
}

function preview(t, width = 1920, height = 1080) {
  let observer;
  const canvas = {
    width: 0, height: 0,
    context: { draws: [], drawImage(...args) { this.draws.push(args); } },
    getContext() { return this.context; },
    toDataURL() { return "data:image/jpeg;base64,/9j/"; }
  };
  canvas.context.getImageData = () => ({ data: new Uint8ClampedArray(canvas.width * canvas.height * 4) });
  let canvasCount = 0;
  const createCanvas = () => {
    if (canvasCount++ === 0) return canvas;
    const comparison = { width: 0, height: 0 };
    comparison.getContext = () => ({
      drawImage() {},
      getImageData: () => ({ data: new Uint8ClampedArray(comparison.width * comparison.height * 4) })
    });
    return comparison;
  };
  const globals = {
    document: { createElement: (tag) => tag === "canvas" ? createCanvas() : new Element(), createElementNS: () => new Element() },
    window: { setTimeout: () => 1, clearTimeout() {}, setInterval: () => 2, clearInterval() {} },
    ResizeObserver: class {
      constructor(callback) { observer = this; this.callback = callback; }
      observe() {}
      disconnect() { this.disconnected = true; }
    }
  };
  for (const [key, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
  const video = new Element();
  Object.assign(video, { videoWidth: width, videoHeight: height, play: async () => {} });
  const layer = new Element(), list = new Element();
  const frames = new Map();
  const id = crypto.randomUUID();
  frames.set(id, { width, height, capturedAt: new Date().toISOString() });
  const annotations = new AnnotationController({ video, layer, list, getFrames: () => frames });
  return { annotations, frames, id, video, layer, list, canvas, observer };
}

function mark(frame_id, overrides = {}) {
  return { frame_id, shape: "rectangle", rect: { x: 0.1, y: 0.2, width: 0.4, height: 0.3 }, label: "Review note", ...overrides };
}

test("downscaled frame annotations use original source dimensions", (t) => {
  const f = preview(t, 3840, 2160);
  f.frames.set(f.id, { width: 1280, height: 720, sourceWidth: 3840, sourceHeight: 2160, capturedAt: new Date().toISOString() });
  const result = f.annotations.addMany([mark(f.id)]);
  assert.equal(result.accepted.length, 1);
  assert.deepEqual(result.errors, []);
  assert.equal(f.layer.attributes.viewBox, "0 0 3840 2160");
  const rect = f.layer.children[0].children[0];
  assert.equal(rect.attributes.x, "384");
  assert.equal(rect.attributes.y, "432");
  assert.equal(rect.attributes.width, "1536");
});

test("old, invalid, and future frame timestamps reject annotations", (t) => {
  const f = preview(t);
  for (const capturedAt of [new Date(Date.now() - 60_000).toISOString(), "bad date", new Date(Date.now() + 60_000).toISOString()]) {
    f.frames.get(f.id).capturedAt = capturedAt;
    const result = f.annotations.addMany([mark(f.id)]);
    assert.equal(result.accepted.length, 0);
    assert.match(result.errors[0], /too old/);
  }
});

test("repeated batches cannot exceed eight active marks", (t) => {
  const f = preview(t);
  assert.equal(f.annotations.addMany(Array.from({ length: 5 }, () => mark(f.id))).accepted.length, 5);
  const result = f.annotations.addMany(Array.from({ length: 5 }, () => mark(f.id)));
  assert.equal(result.accepted.length, 3);
  assert.equal(result.errors.length, 2);
  assert.equal(f.annotations.marks.length, 8);
  assert.equal(f.list.children.length, 8);
  f.annotations.marks.forEach((item) => { item.createdAt = Date.now() - 13_000; });
  assert.equal(f.annotations.addMany([mark(f.id)]).accepted.length, 1);
  assert.equal(f.annotations.marks.length, 1);
});

test("resizing the shared source discards marks and rejects its earlier frames", (t) => {
  const f = preview(t);
  f.annotations.addMany([mark(f.id)]);
  f.video.videoWidth = 1280;
  f.video.videoHeight = 720;
  f.observer.callback();
  assert.equal(f.annotations.marks.length, 0);
  assert.equal(f.list.children.length, 0);
  assert.equal(f.annotations.addMany([mark(f.id)]).accepted.length, 0);
});

test("unknown frames, invalid geometry, and oversized batches are rejected", (t) => {
  const f = preview(t);
  const invalid = [mark("unknown"), mark(f.id, { shape: "click" }), mark(f.id, { label: "x".repeat(81) }), mark(f.id, { rect: { x: 0.8, y: 0, width: 0.5, height: 0.5 } })];
  assert.equal(f.annotations.addMany(invalid).accepted.length, 0);
  assert.equal(f.annotations.addMany(Array.from({ length: 9 }, () => mark(f.id))).accepted.length, 0);
  assert.equal(f.annotations.marks.length, 0);
});

test("destroy clears marks, timers, observers, and preview listeners", (t) => {
  const f = preview(t);
  f.annotations.addMany([mark(f.id)]);
  f.annotations.destroy();
  assert.equal(f.annotations.marks.length, 0);
  assert.equal(f.annotations.expiryTimer, null);
  assert.equal(f.layer.children.length, 0);
  assert.equal(f.list.children.length, 0);
  assert.equal(f.observer.disconnected, true);
  assert.equal(f.video.events.size, 0);
});

test("sampling bounds 4K JPEG size and retains source dimensions for marks", (t) => {
  const f = preview(t, 3840, 2160);
  let frame;
  const capture = new ScreenCaptureController({ video: f.video, onFrame: (value) => { frame = value; } });
  capture.stream = {};
  capture.sampleFrame();
  assert.equal(frame.width, 1920);
  assert.equal(frame.height, 1080);
  assert.deepEqual(f.canvas.context.draws[0].slice(1), [0, 0, 1920, 1080]);
  const metadata = capture.getFrames().get(frame.frame_id);
  assert.equal(metadata.sourceWidth, 3840);
  assert.equal(metadata.sourceHeight, 2160);
  assert.equal(metadata.width, 1920);
  f.frames.set(frame.frame_id, metadata);
  assert.equal(f.annotations.addMany([mark(frame.frame_id)]).accepted.length, 1);
});

test("portrait sources are bounded and small sources are never enlarged", (t) => {
  const f = preview(t, 2160, 3840);
  const sizes = [];
  const capture = new ScreenCaptureController({ video: f.video, onFrame: ({ width, height }) => sizes.push([width, height]) });
  capture.stream = {};
  capture.sampleFrame();
  f.video.videoWidth = 320;
  f.video.videoHeight = 240;
  capture.sampleFrame();
  assert.deepEqual(sizes, [[1080, 1920], [320, 240]]);
});

test("stopping capture releases its canvas and prevents late samples", (t) => {
  const f = preview(t);
  let samples = 0, stopped = 0;
  const capture = new ScreenCaptureController({ video: f.video, onFrame: () => { samples += 1; } });
  const track = { stop() { stopped += 1; } };
  capture.stream = { getVideoTracks: () => [track], getTracks: () => [track] };
  capture.sampleFrame();
  capture.stop();
  capture.sampleFrame();
  assert.equal(samples, 1);
  assert.equal(stopped, 1);
  assert.equal(capture.getFrames().size, 0);
  assert.equal(f.canvas.width, 0);
  assert.equal(f.canvas.height, 0);
  assert.equal(f.video.srcObject, null);
});
