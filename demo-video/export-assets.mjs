// Export images for the presentation deck from the demo film:
//   out/deck/jot-<pose>.png   Jot in a pose, transparent background
//   out/deck/scene-<id>.png   a feature scene without Jot or captions
// Run `node render.mjs --stills 0` first if out/timeline.json is missing.
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = normalize(join(HERE, ".."));
const OUT = join(HERE, "out", "deck");
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png" };

// Jot poses: a moment in a scene (seconds after the scene starts) that shows it.
const POSES = { wave: ["intro", 1.6], confused: ["problem", 4.4], celebrate: ["hints", 4.6], concerned: ["coach", 3.4], proud: ["privacy", 6.8], happy: ["marks", 6.5] };
// Feature scenes, captured once everything in them has appeared. Clip: x, y, w, h.
const SCENES = {
  share: [6.5, [620, 225, 1180, 670]],
  marks: [5.5, [620, 235, 1180, 590]],
  hints: [5.6, [620, 250, 1180, 540]],
  classes: [7.4, [620, 270, 1240, 360]],
  study: [6.0, [620, 235, 1250, 580]],
  material: [7.2, [620, 205, 1180, 710]],
  coach: [3.6, [620, 225, 1290, 460]],
  privacy: [7.2, [640, 270, 1180, 470]],
  tech: [7.0, [600, 310, 1300, 460]]
};

const server = createServer((request, response) => {
  const path = normalize(join(REPO, decodeURIComponent(new URL(request.url, "http://x").pathname)));
  if (!path.startsWith(REPO + sep) || !existsSync(path) || !statSync(path).isFile()) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { "Content-Type": TYPES[extname(path)] || "application/octet-stream" });
  createReadStream(path).pipe(response);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

const timeline = JSON.parse(readFileSync(join(HERE, "out", "timeline.json"), "utf8"));
// A silent envelope keeps Jot's mouth closed in the pose images.
const quiet = { ...timeline, envelope: timeline.envelope.map(() => 0) };
const start = Object.fromEntries(timeline.scenes.map((scene) => [scene.id, scene.start]));

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
page.on("pageerror", (error) => console.error("page error:", error.message));
await page.goto(`http://127.0.0.1:${server.address().port}/demo-video/index.html?render=1`);
await page.evaluate(async (data) => {
  await window.__ready;
  window.__setup(data);
}, quiet);

// Poses: only Jot, on a transparent page.
await page.addStyleTag({ content: "html, body, #film { background: transparent !important; } .paper, .caption, .scene, .jot-shadow { visibility: hidden !important; }" });
for (const [name, [scene, at]] of Object.entries(POSES)) {
  await page.evaluate((t) => window.__seek(t), start[scene] + at);
  await page.evaluate(() => { document.getElementById("jot").style.transform = "translate(960px, 540px) scale(1.6)"; });
  const file = join(OUT, `jot-${name}.png`);
  await page.locator("#jot").screenshot({ path: file, omitBackground: true });
  console.log(file);
}

// Scenes on a transparent background (the slide supplies the paper), without Jot, captions, or the headline.
await page.addStyleTag({ content: ".scene { visibility: visible !important; } #jot, .caption, .headline { display: none !important; }" });
for (const [id, [at, [x, y, w, h]]] of Object.entries(SCENES)) {
  await page.evaluate((t) => window.__seek(t), start[id] + at);
  const file = join(OUT, `scene-${id}.png`);
  await page.screenshot({ path: file, clip: { x, y, width: w, height: h }, omitBackground: true });
  console.log(file);
}

// A poster frame for the embedded video: the title card.
await page.addStyleTag({ content: "#jot, .headline { display: block !important; } .paper { visibility: visible !important; } #film { background: #f4efe3 !important; }" });
await page.evaluate((t) => window.__seek(t), 3.2);
await page.screenshot({ path: join(OUT, "poster.png"), clip: { x: 0, y: 0, width: 1920, height: 1080 }, scale: "css" });
console.log(join(OUT, "poster.png"));

// The app's own icons (frontend/src/icons.js), in the primary ink, for the deck.
const ICONS = ["sparkles", "quiz", "monitor", "book", "lock", "micOff", "mic", "globe", "exam", "notes", "slides", "video", "syllabus", "file", "calculator", "check", "pencil", "guide", "cards", "languages", "sigma", "code", "user", "refresh"];
await page.evaluate(async (names) => {
  const { icon } = await import("/frontend/src/icons.js");
  const wrap = document.createElement("div");
  wrap.id = "icons";
  wrap.style.cssText = "position:fixed;left:0;top:0;display:flex;flex-wrap:wrap;gap:20px;z-index:99;color:#3f6577";
  wrap.innerHTML = names.map((name) => `<span data-icon="${name}" style="display:inline-block;width:160px;height:160px">${icon(name, { size: 160 })}</span>`).join("");
  document.body.append(wrap);
}, ICONS);
for (const name of ICONS) {
  const file = join(OUT, `icon-${name}.png`);
  await page.locator(`[data-icon="${name}"]`).screenshot({ path: file, omitBackground: true });
}
console.log(`${ICONS.length} icons`);

await browser.close();
server.close();
