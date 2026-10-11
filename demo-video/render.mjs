// Build the timeline from narration/*.wav, mix out/narration.wav, then either
//   node render.mjs                 render out/jot-demo.mp4 (1920x1080, 30 fps)
//   node render.mjs --stills 2,9.5  save PNG stills at those times to out/stills/
//   node render.mjs --preview       serve the film for live playback in a browser
import { spawn } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ffmpegPath from "ffmpeg-static";
import { chromium } from "playwright-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = normalize(join(HERE, ".."));
const OUT = join(HERE, "out");
const FPS = 30;
const LEAD_IN = 0.9; // seconds before the first line
const SPEECH_DELAY = 0.45; // seconds from a scene's start to its line
const GAP = 0.8; // seconds of quiet after each line
const TAIL = 2.2; // hold on the last frame
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : null;
};

// ------------------------------------------------------------- audio
function readWav(path) {
  const buffer = readFileSync(path);
  let offset = 12;
  let format = null;
  let data = null;
  while (offset + 8 <= buffer.length) {
    const id = buffer.toString("ascii", offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === "fmt ") {
      format = {
        channels: buffer.readUInt16LE(body + 2),
        rate: buffer.readUInt32LE(body + 4),
        bits: buffer.readUInt16LE(body + 14)
      };
    } else if (id === "data") {
      data = buffer.subarray(body, body + size);
    }
    offset = body + size + (size % 2);
  }
  if (!format || !data || format.bits !== 16 || format.channels !== 1) {
    throw new Error(`${path}: expected 16-bit mono PCM (run make-narration.ps1).`);
  }
  const samples = new Int16Array(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
  return { rate: format.rate, samples };
}

function writeWav(path, rate, samples) {
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + samples.length * 2, 4);
  header.write("WAVEfmt ", 8, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(samples.length * 2, 40);
  writeFileSync(path, Buffer.concat([header, Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength)]));
}

function buildTimeline() {
  const script = JSON.parse(readFileSync(join(HERE, "script.json"), "utf8"));
  const clips = script.map((scene) => {
    const path = join(HERE, "narration", `${scene.id}.wav`);
    if (!existsSync(path)) throw new Error(`Missing ${path}. Run: npm run narration`);
    return { ...scene, ...readWav(path) };
  });
  const rate = clips[0].rate;
  let cursor = LEAD_IN - SPEECH_DELAY;
  const scenes = clips.map((clip, index) => {
    const start = index === 0 ? 0 : cursor;
    const speechStart = cursor + SPEECH_DELAY;
    const speechEnd = speechStart + clip.samples.length / rate;
    cursor = speechEnd + GAP;
    return { id: clip.id, text: clip.text, start, speechStart, speechEnd };
  });
  scenes.forEach((scene, index) => {
    scene.end = index + 1 < scenes.length ? scenes[index + 1].start : cursor + TAIL;
  });
  const duration = scenes.at(-1).end;

  // One mixed track, and a loudness envelope per video frame for Jot's mouth.
  const mix = new Int16Array(Math.ceil(duration * rate));
  clips.forEach((clip, index) => mix.set(clip.samples, Math.round(scenes[index].speechStart * rate)));
  const perFrame = rate / FPS;
  const raw = [];
  for (let frame = 0; frame < duration * FPS; frame += 1) {
    let sum = 0;
    const from = Math.floor(frame * perFrame);
    const to = Math.min(mix.length, Math.floor((frame + 1) * perFrame));
    for (let i = from; i < to; i += 1) sum += (mix[i] / 32768) ** 2;
    raw.push(Math.sqrt(sum / Math.max(1, to - from)));
  }
  const sorted = [...raw].filter((v) => v > 0.005).sort((a, b) => a - b);
  const loud = sorted[Math.floor(sorted.length * 0.95)] || 1;
  const envelope = [];
  let smooth = 0;
  for (const value of raw) {
    const target = Math.min(1, value / loud);
    smooth += (target - smooth) * (target > smooth ? 0.7 : 0.35);
    envelope.push(Number(smooth.toFixed(3)));
  }

  mkdirSync(OUT, { recursive: true });
  writeWav(join(OUT, "narration.wav"), rate, mix);
  const timeline = { fps: FPS, duration, scenes, envelope };
  writeFileSync(join(OUT, "timeline.json"), JSON.stringify(timeline));
  const srt = scenes.map((scene, i) => `${i + 1}\n${stamp(scene.speechStart)} --> ${stamp(scene.speechEnd)}\n${scene.text}\n`).join("\n");
  writeFileSync(join(OUT, "jot-demo.srt"), srt);
  return timeline;
}

function stamp(seconds) {
  const ms = Math.round(seconds * 1000);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}

// ------------------------------------------------------------- static server
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".wav": "audio/wav" };

function serve() {
  const server = createServer((request, response) => {
    const path = normalize(join(REPO, decodeURIComponent(new URL(request.url, "http://x").pathname)));
    if (!path.startsWith(REPO + sep) || !existsSync(path) || !statSync(path).isFile()) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": TYPES[extname(path)] || "application/octet-stream", "Cache-Control": "no-store" });
    createReadStream(path).pipe(response);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

// ------------------------------------------------------------- main
const timeline = buildTimeline();
console.log(`Timeline: ${timeline.scenes.length} scenes, ${timeline.duration.toFixed(1)} s`);
const server = await serve();
const base = `http://127.0.0.1:${server.address().port}/demo-video/index.html`;

if (flag("--preview")) {
  console.log(`Preview: ${base}  (press Ctrl+C to stop)`);
} else {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on("pageerror", (error) => console.error("page error:", error.message));
  await page.goto(`${base}?render=1`);
  await page.evaluate(async (data) => {
    await window.__ready;
    window.__setup(data);
  }, timeline);

  const stills = option("--stills");
  if (stills) {
    mkdirSync(join(OUT, "stills"), { recursive: true });
    for (const t of stills.split(",").map(Number)) {
      await page.evaluate((time) => window.__seek(time), t);
      const file = join(OUT, "stills", `t${String(t).replace(".", "_")}.png`);
      await page.screenshot({ path: file });
      console.log(`still ${t}s -> ${file}`);
    }
  } else {
    const output = join(OUT, "jot-demo.mp4");
    const ffmpeg = spawn(ffmpegPath, [
      "-y", "-loglevel", "error",
      "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
      "-i", join(OUT, "narration.wav"),
      "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
      output
    ], { stdio: ["pipe", "inherit", "inherit"] });
    const done = new Promise((resolve, reject) => ffmpeg.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}`)))));
    const frames = Math.ceil(timeline.duration * FPS);
    const started = Date.now();
    for (let frame = 0; frame < frames; frame += 1) {
      await page.evaluate((time) => window.__seek(time), frame / FPS);
      const jpeg = await page.screenshot({ type: "jpeg", quality: 92 });
      if (!ffmpeg.stdin.write(jpeg)) await new Promise((resolve) => ffmpeg.stdin.once("drain", resolve));
      if (frame % 150 === 0) console.log(`frame ${frame}/${frames} (${((Date.now() - started) / 1000).toFixed(0)} s)`);
    }
    ffmpeg.stdin.end();
    await done;
    console.log(`Wrote ${output}`);
  }
  await browser.close();
  server.close();
}
