// Jot demo film. Every frame is a pure function of time `t` (seconds), so the
// renderer can step through it frame by frame and the preview can play it live.
import { avatarSvg } from "../frontend/src/avatar.js";
import { icon } from "../frontend/src/icons.js";

const W = 1920;
const H = 1080;
const MASCOT = "../frontend/src/assets/jot-mascot.png";

// ---------------------------------------------------------------- helpers
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const lerp = (a, b, p) => a + (b - a) * p;
const ease = {
  out: (p) => 1 - Math.pow(1 - p, 3),
  inOut: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
  back: (p) => {
    const c1 = 1.5;
    const c3 = c1 + 1;
    return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
  },
  expo: (p) => (p >= 1 ? 1 : 1 - Math.pow(2, -10 * p))
};

function h(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

function place(el, x, y, w, hgt) {
  Object.assign(el.style, { left: `${x}px`, top: `${y}px` });
  if (w) el.style.width = `${w}px`;
  if (hgt) el.style.height = `${hgt}px`;
  return el;
}

/** Fade and rise in from `start` over `d` seconds; scale pops with a little overshoot. */
function appear(el, t, start, { d = 0.55, dy = 28, pop = false, out = Infinity, base = "" } = {}) {
  const p = seg(t, start, start + d);
  // No fade-out by default (Infinity - Infinity would give NaN, an invalid opacity).
  const q = Number.isFinite(out) ? 1 - seg(t, out, out + 0.4) : 1;
  el.style.opacity = String(ease.out(p) * q);
  el.style.transform = pop
    ? `${base} scale(${lerp(0.55, 1, ease.back(p))})`
    : `${base} translateY(${(1 - ease.out(p)) * dy}px)`;
}

function highlight(el, t, start, d = 0.7) {
  el.style.backgroundSize = `${ease.expo(seg(t, start, start + d)) * 100}% 100%`;
}

function typed(text, t, start, cps = 22) {
  return text.slice(0, Math.max(0, Math.floor((t - start) * cps)));
}

const ico = (name, size = 28) => icon(name, { size });

function headline(text) {
  return h(`<h2 class="headline"><span class="hl-text">${text}</span></h2>`);
}

function worksheet(lines, title) {
  return h(`<div class="card sheet">
    <div class="q">${title}</div>
    ${lines.map((line, i) => `<div class="line" data-line="${i}"><span class="n">${i + 1}.</span><span class="txt">${line}</span></div>`).join("")}
  </div>`);
}

/** An annotation mark drawn like the app's: a rectangle traced in, then its label. */
function markEl(label) {
  return h(`<div class="mark" style="position:absolute">
    <svg class="mark-svg" style="left:-4px;top:-4px">
      <rect class="mark-rect" x="0" y="0" rx="10" pathLength="100" stroke-dasharray="100" stroke-dashoffset="100"/>
    </svg>
    <div class="mark-label" style="left:0">${label}</div>
  </div>`);
}

/** Fit a mark around `target` (an element in the film), padded by `pad` pixels. */
function fitMark(mark, target, pad = 12) {
  const film = document.getElementById("film");
  const frame = film.getBoundingClientRect();
  const scale = frame.width / W;
  const box = target.getBoundingClientRect();
  const x = (box.left - frame.left) / scale - pad;
  const y = (box.top - frame.top) / scale - pad;
  const w = box.width / scale + pad * 2;
  const hgt = box.height / scale + pad * 2;
  Object.assign(mark.style, { left: `${x}px`, top: `${y}px` });
  const svg = mark.querySelector("svg");
  svg.setAttribute("width", String(w + 8));
  svg.setAttribute("height", String(hgt + 8));
  svg.setAttribute("viewBox", `-4 -4 ${w + 8} ${hgt + 8}`);
  const rect = mark.querySelector("rect");
  rect.setAttribute("width", String(w));
  rect.setAttribute("height", String(hgt));
  mark.querySelector(".mark-label").style.top = `${hgt + 14}px`;
}

function drawMark(mark, t, start, { out = Infinity } = {}) {
  const rect = mark.querySelector("rect");
  const label = mark.querySelector(".mark-label");
  const p = ease.inOut(seg(t, start, start + 0.8));
  const fade = Number.isFinite(out) ? 1 - seg(t, out, out + 0.4) : 1;
  rect.setAttribute("stroke-dashoffset", String(100 - p * 100));
  rect.style.fillOpacity = String(seg(t, start + 0.5, start + 0.9));
  mark.style.opacity = String(t >= start ? fade : 0);
  appear(label, t, start + 0.7, { d: 0.4, dy: 10 });
  label.style.opacity = String(Number(label.style.opacity) * fade);
}

const CURSOR = `<svg class="cursor" viewBox="0 0 24 24"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#1c2e37" stroke="#fffdf7" stroke-width="1.6" stroke-linejoin="round"/></svg>`;

function moveAlong(el, t, points) {
  // points: [[time, x, y], ...]
  let [x, y] = [points[0][1], points[0][2]];
  for (let i = 1; i < points.length; i += 1) {
    const [t0, x0, y0] = points[i - 1];
    const [t1, x1, y1] = points[i];
    if (t >= t0) {
      const p = ease.inOut(seg(t, t0, t1));
      x = lerp(x0, x1, p);
      y = lerp(y0, y1, p);
    }
  }
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
}

function windowFrame(x, y, w, hgt, tabs = "") {
  return place(h(`<div class="card window">
    <div class="window-bar"><img src="${MASCOT}" alt="" />Jot<span class="spacer"></span>${tabs}</div>
  </div>`), x, y, w, hgt);
}

// ---------------------------------------------------------------- the actor
const film = document.getElementById("film");
film.append(h(`<div class="paper"></div>`));
const sceneLayer = h(`<div></div>`);
film.append(sceneLayer);
const jot = h(`<div id="jot"><div class="jot-shadow"></div>${avatarSvg({ color: "slate", face: "happy", accessory: "none" }, { size: 340, animated: true })}</div>`);
film.append(jot);
const caption = h(`<div class="caption"><span class="who">Jot</span><span class="words"></span></div>`);
film.append(caption);

const part = (sel) => jot.querySelector(sel);
const J = {
  figure: part(".jot-figure"),
  eyes: part(".jot-eyes"),
  mouth: part(".jot-mouth"),
  talk: part(".jot-talk"),
  armL: part(".jot-arm-left"),
  foreL: part(".jot-arm-left .jot-forearm"),
  armR: part(".jot-arm-right"),
  foreR: part(".jot-arm-right .jot-forearm"),
  legL: part(".jot-leg-left"),
  legR: part(".jot-leg-right"),
  shadow: part(".jot-shadow"),
  exprs: Object.fromEntries(["proud", "celebrate", "confused", "concerned"].map((n) => [n, part(`.jot-expr-${n}`)])),
  props: {
    sparkles: part(".jot-prop-sparkles"),
    question: part(".jot-prop-question"),
    sweat: part(".jot-prop-sweat")
  }
};

// Where Jot stands in each scene: center x, center y, scale.
const POSES = {
  intro: [960, 380, 1.45],
  problem: [330, 640, 1],
  share: [300, 650, 0.95],
  marks: [300, 650, 0.95],
  hints: [330, 650, 1],
  classes: [300, 650, 0.95],
  study: [300, 650, 0.95],
  material: [300, 650, 0.95],
  coach: [300, 650, 0.95],
  privacy: [330, 640, 1],
  tech: [300, 650, 0.9],
  outro: [960, 360, 1.4]
};

// ---------------------------------------------------------------- scenes
// Each scene: build() returns its root; update(lt) positions everything for local time lt.
const SCENES = {
  intro: {
    build() {
      const root = h(`<div></div>`);
      root.append(
        place(h(`<div class="big-title"><span class="hl-text">Jot</span></div>`), 0, 610),
        place(h(`<div class="big-sub">The study buddy that sees your screen</div>`), 0, 850)
      );
      return root;
    },
    update(lt, root) {
      const title = root.querySelector(".big-title");
      appear(title, lt, 0.5, { d: 0.6, dy: 40 });
      highlight(title.querySelector(".hl-text"), lt, 0.9, 0.8);
      appear(root.querySelector(".big-sub"), lt, 1.3, { dy: 20 });
    }
  },

  problem: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Search can't see your screen"));
      const search = place(h(`<div class="card" style="padding:34px 40px">
        <div style="display:flex;align-items:center;gap:18px;padding:18px 24px;border:2px solid var(--line);border-radius:999px;font-size:30px">
          ${ico("quiz", 30)}<span class="q"></span><span style="width:3px;height:34px;background:var(--text)" class="caret"></span>
        </div>
        ${["Derivative rules - a general overview", "Calculus help forum: why is my answer wrong?", "10 tips for getting derivatives right"].map((r) => `
          <div class="result" style="margin-top:26px">
            <div style="font-size:28px;font-weight:700;color:#2b4f8f">${r}</div>
            <div style="height:12px;margin-top:12px;width:88%;border-radius:6px;background:var(--surface-muted)"></div>
          </div>`).join("")}
        <div class="nope" style="position:absolute;inset:0;display:grid;place-items:center;border-radius:16px;background:rgba(255,253,247,.72)">
          <span style="font-family:var(--font-display);font-size:58px;font-weight:800;transform:rotate(-6deg)"><span class="hl-text">Not your problem.</span></span>
        </div>
      </div>`), 700, 250, 820);
      const sheet = place(worksheet(["f(x) = x² − 3x + 5", "f′(x) = 2x + 3"], "Your actual problem"), 1180, 470, 560);
      root.append(search, sheet);
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      const card = root.querySelector(".card");
      appear(card, lt, 0.3);
      root.querySelector(".q").textContent = typed("why is my derivative wrong", lt, 0.8, 20);
      root.querySelector(".caret").style.opacity = Math.floor(lt * 2.4) % 2 ? "0" : "1";
      root.querySelectorAll(".result").forEach((r, i) => appear(r, lt, 2.3 + i * 0.25, { d: 0.4, dy: 14 }));
      const nope = root.querySelector(".nope");
      nope.style.opacity = String(ease.out(seg(lt, 3.6, 4.1)));
      highlight(nope.querySelector(".hl-text"), lt, 3.9);
      appear(root.querySelector(".sheet"), lt, 5.2, { dy: 60, base: "rotate(3deg)" });
    }
  },

  share: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Share, then just ask"));
      const win = windowFrame(640, 240, 1140, 640, `<span class="tab on">Quick tutor</span>`);
      const btn = place(h(`<div class="btn btn-primary share-btn" style="position:absolute">${ico("monitor", 28)} Share screen</div>`), 30, 96);
      const pills = place(h(`<div style="position:absolute;display:flex;gap:14px">
        <span class="pill p-screen"><span class="dot"></span>Screen <strong>not sharing</strong></span>
        <span class="pill p-mic"><span class="dot"></span>Mic <strong>off</strong></span>
      </div>`), 330, 104);
      const stage = place(h(`<div class="stage"></div>`), 30, 180, 1080, 430);
      const sheet = place(worksheet(["f(x) = x² − 3x + 5", "f′(x) = 2x + 3", "f′(2) = 7"], "Problem 3"), 250, 22, 580);
      sheet.style.transformOrigin = "top center";
      stage.append(sheet);
      win.append(btn, pills, stage);
      // The browser's own source picker.
      const picker = place(h(`<div class="card picker" style="padding:30px;display:grid;gap:18px">
        <div style="font-size:28px;font-weight:700">Choose what to share</div>
        <div style="display:flex;gap:18px">
          ${["Entire screen", "Window", "Chrome tab"].map((label, i) => `
            <div class="pick" data-i="${i}" style="width:200px;padding:16px;border:3px solid var(--line);border-radius:14px;text-align:center;font-size:22px;font-weight:600">
              <div style="height:96px;margin-bottom:12px;border-radius:8px;background:${i === 1 ? "#fffdf9" : "var(--surface-muted)"};box-shadow:inset 0 0 0 1px var(--line)"></div>${label}
            </div>`).join("")}
        </div>
      </div>`), 860, 400);
      const cursor = h(CURSOR);
      const ask = place(h(`<div class="bubble student">Why is step 2 wrong?</div>`), 1300, 930 - 180);
      const wave = place(h(`<div class="mic-wave" style="position:absolute;display:flex;gap:7px;align-items:center;height:60px">${Array.from({ length: 9 }, () => `<span style="width:9px;border-radius:5px;background:var(--primary)"></span>`).join("")}</div>`), 1150, 770);
      // Frames travelling from the shared screen to Jot.
      const frames = h(`<div>${Array.from({ length: 5 }, () => `<span class="frame" style="position:absolute;width:46px;height:32px;border:3px solid #fffdf7;border-radius:6px;background:#8ccfe6;box-shadow:0 4px 10px rgba(0,0,0,.2)"></span>`).join("")}</div>`);
      root.append(win, picker, frames, wave, ask, cursor);
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      appear(root.querySelector(".window"), lt, 0.2, { dy: 40 });
      const cursor = root.querySelector(".cursor");
      cursor.style.opacity = String(seg(lt, 0.6, 0.9) * (1 - seg(lt, 3.4, 3.7)));
      moveAlong(cursor, lt, [[0.6, 1500, 900], [1.4, 820, 368], [2.2, 820, 368], [2.7, 1170, 600], [3.4, 1170, 600]]);
      const pressed = lt > 1.4 && lt < 1.6;
      root.querySelector(".share-btn").style.transform = pressed ? "scale(.96)" : "";
      const picker = root.querySelector(".picker");
      appear(picker, lt, 1.55, { d: 0.35, pop: true, out: 3.1 });
      root.querySelectorAll(".pick").forEach((pick) => {
        pick.style.borderColor = pick.dataset.i === "1" && lt > 2.7 ? "var(--primary)" : "var(--line)";
      });
      const live = lt > 3.2;
      const pScreen = root.querySelector(".p-screen");
      pScreen.className = `pill p-screen${live ? " live" : ""}`;
      pScreen.querySelector("strong").textContent = live ? "sharing" : "not sharing";
      const micOn = lt > 4.6;
      const pMic = root.querySelector(".p-mic");
      pMic.className = `pill p-mic${micOn ? " good" : ""}`;
      pMic.querySelector("strong").textContent = micOn ? "on" : "off";
      const sheet = root.querySelector(".stage .sheet");
      sheet.style.opacity = String(ease.out(seg(lt, 3.2, 3.7)));
      sheet.style.transform = `scale(${lerp(0.92, 1, ease.out(seg(lt, 3.2, 3.8)))})`;
      root.querySelectorAll(".frame").forEach((frame, i) => {
        const cycle = ((lt - 3.6 - i * 0.55) % 2.75 + 2.75) % 2.75;
        const p = clamp(cycle / 1.6);
        const on = lt > 3.6 + i * 0.55 && cycle < 1.6;
        const x = lerp(900, 420, ease.inOut(p));
        const y = lerp(620, 600, p) - Math.sin(p * Math.PI) * 170;
        Object.assign(frame.style, { left: `${x}px`, top: `${y}px`, opacity: on ? String(Math.sin(p * Math.PI) * 1.2) : "0", transform: `rotate(${lerp(-8, 8, p)}deg) scale(${lerp(1, 0.6, p)})` });
      });
      const wave = root.querySelector(".mic-wave");
      if (wave) {
        wave.style.opacity = String(seg(lt, 4.6, 5) * (1 - seg(lt, 7.6, 8)));
        [...wave.children].forEach((bar, i) => {
          bar.style.height = `${12 + Math.abs(Math.sin(lt * 9 + i * 1.3)) * 44 * (0.5 + 0.5 * Math.sin(lt * 3 + i))}px`;
        });
      }
      appear(root.querySelector(".bubble"), lt, 5.6, { pop: true });
    }
  },

  marks: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("It points at the exact spot"));
      const stage = place(h(`<div class="stage"></div>`), 640, 250, 1140, 560);
      const sheet = place(worksheet(["f(x) = x² − 3x + 5", "f′(x) = 2x <span class='tgt'>+ 3</span>", "f′(2) = 7"], "Problem 3"), 260, 40, 620);
      stage.append(sheet);
      const mark = markEl("Check this sign");
      const pointer = h(`<div class="pointer" style="position:absolute;width:30px;height:30px;border-radius:50%;background:var(--mark-stroke);box-shadow:0 0 0 10px rgba(255,154,102,.25)"></div>`);
      root.append(stage, mark, pointer);
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      appear(root.querySelector(".stage"), lt, 0.2, { dy: 40 });
      const mark = root.querySelector(".mark");
      fitMark(mark, root.querySelector(".tgt"), 10);
      drawMark(mark, lt, 2.2);
      const pointer = root.querySelector(".pointer");
      const p = ease.inOut(seg(lt, 1.1, 2.1));
      pointer.style.left = `${lerp(560, 1260, p)}px`;
      pointer.style.top = `${lerp(700, 470, p) - Math.sin(p * Math.PI) * 120}px`;
      pointer.style.opacity = String(seg(lt, 1.1, 1.3) * (1 - seg(lt, 2.1, 2.4)));
    }
  },

  hints: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Hints before answers"));
      root.append(
        place(h(`<div class="bubble student b1">Can you just give me the answer?</div>`), 1060, 270),
        place(h(`<div class="bubble tutor typing b2"><span></span><span></span><span></span></div>`), 640, 400),
        place(h(`<div class="bubble tutor b3">Here's a hint: what's the derivative of −3x?</div>`), 640, 400),
        place(h(`<div class="bubble student b4">Oh! It's −3, so f′(x) = 2x − 3.</div>`), 1080, 560),
        place(h(`<div class="bubble tutor b5">Exactly. Nice catch!</div>`), 640, 700)
      );
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      appear(root.querySelector(".b1"), lt, 0.4, { pop: true });
      const typing = root.querySelector(".b2");
      appear(typing, lt, 1.0, { pop: true, out: 1.7 });
      [...typing.children].forEach((dot, i) => { dot.style.transform = `translateY(${Math.sin(lt * 9 - i) * -5}px)`; });
      appear(root.querySelector(".b3"), lt, 1.9, { pop: true });
      appear(root.querySelector(".b4"), lt, 3.2, { pop: true });
      appear(root.querySelector(".b5"), lt, 4.0, { pop: true });
    }
  },

  classes: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("A notebook for every class"));
      const classes = [
        { name: "Calculus I", glyph: "sigma", c: "#3f6577", tilt: -2 },
        { name: "Biology", glyph: "dna", c: "#4f7340", tilt: 1.5 },
        { name: "World History", glyph: "landmark", c: "#a33a55", tilt: -1 }
      ];
      classes.forEach((klass, i) => {
        const card = place(h(`<div class="card class-card" style="--c:${klass.c}" data-tilt="${klass.tilt}">
          <span class="tabmark"></span><div class="cover"></div>
          <span class="badge">${ico(klass.glyph, 36)}</span>
          <div class="name">${klass.name}</div>
          <div class="meta">${i === 0 ? '<span class="count">0</span> uploads' : `${[6, 9][i - 1]} uploads`}</div>
        </div>`), 640 + i * 400, 300);
        root.append(card);
      });
      const files = [
        ["notes", "Lecture notes.pdf"],
        ["syllabus", "Syllabus.docx"],
        ["slides", "Week 4 slides.pptx"],
        ["exam", "Midterm 1.pdf"],
        ["video", "Lecture 6.mp4"]
      ];
      files.forEach(([glyph, label]) => root.append(h(`<div class="chip file">${ico(glyph, 26)}${label}</div>`)));
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      root.querySelectorAll(".class-card").forEach((card, i) => {
        const tilt = Number(card.dataset.tilt);
        const p = seg(lt, 0.4 + i * 0.18, 1.1 + i * 0.18);
        card.style.opacity = String(ease.out(p));
        card.style.transform = `translateY(${(1 - ease.out(p)) * 60}px) rotate(${lerp(tilt * 6, tilt, ease.back(p))}deg)`;
      });
      let landed = 0;
      root.querySelectorAll(".file").forEach((chip, i) => {
        const start = 2.2 + i * 0.75;
        const p = seg(lt, start, start + 0.9);
        const e = ease.inOut(p);
        const x = lerp(1500 - (i % 2) * 120, 700, e);
        const y = lerp(720 + (i % 3) * 70, 390, e) - Math.sin(p * Math.PI) * 140;
        chip.style.left = `${x}px`;
        chip.style.top = `${y}px`;
        chip.style.opacity = String(seg(lt, start, start + 0.15) * (1 - seg(lt, start + 0.75, start + 0.9)));
        chip.style.transform = `scale(${lerp(1, 0.55, ease.out(seg(lt, start + 0.5, start + 0.9)))})`;
        if (p >= 1) landed += 1;
      });
      root.querySelector(".count").textContent = String(landed);
      const first = root.querySelector(".class-card");
      const bump = landed && lt - (2.2 + (landed - 1) * 0.75 + 0.9) < 0.25 ? 1.03 : 1;
      first.style.transform += ` scale(${bump})`;
    }
  },

  study: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Study sets from your own notes"));
      root.append(place(h(`<div class="card tile t0">
        <h3>${ico("quiz", 34)}Quiz</h3><p>10 questions</p>
        <div class="opt"><i></i>f′(x) = 2x + 3</div>
        <div class="opt ok"><i></i>f′(x) = 2x − 3</div>
        <div class="opt"><i></i>f′(x) = x − 3</div>
      </div>`), 660, 250));
      root.append(place(h(`<div class="card tile flash t1">
        <div class="face front"><div><small>Flashcard</small>Chain rule</div></div>
        <div class="face back">d/dx f(g(x)) = f′(g(x)) · g′(x)</div>
      </div>`), 1100, 250));
      root.append(place(h(`<div class="card tile t2">
        <h3>${ico("exam", 34)}Practice exam</h3><p>45 minutes · 12 questions</p>
        <div class="rule" style="width:92%"></div><div class="rule" style="width:76%"></div><div class="rule" style="width:84%"></div>
      </div>`), 660, 540));
      root.append(place(h(`<div class="card tile t3">
        <h3>${ico("guide", 34)}Study guide</h3><p>Key ideas, formulas, examples</p>
        <div class="rule" style="width:88%"></div><div class="rule" style="width:94%"></div><div class="rule" style="width:70%"></div>
      </div>`), 1100, 540));
      root.append(place(h(`<div class="chip source">${ico("video", 24)}From Lecture 6 · 12:31</div>`), 1540, 470));
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      ["t0", "t1", "t2", "t3"].forEach((name, i) => appear(root.querySelector(`.${name}`), lt, 0.6 + i * 0.55, { pop: true }));
      const flip = ease.inOut(seg(lt, 3.4, 4.1));
      root.querySelector(".t1 .front").style.transform = `rotateY(${flip * 180}deg)`;
      root.querySelector(".t1 .back").style.transform = `rotateY(${180 + flip * 180}deg)`;
      const source = root.querySelector(".source");
      appear(source, lt, 4.6, { pop: true, base: "rotate(-3deg)" });
    }
  },

  material: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Work right on your PDFs"));
      const win = windowFrame(640, 220, 1140, 680, `<span class="tab">Library</span><span class="tab">Study</span><span class="tab on">Tutor</span>`);
      win.append(place(h(`<div style="position:absolute;display:flex;gap:14px;align-items:center">
        <span class="pill">${ico("book", 20)} Using <strong>World History</strong> · All materials</span>
        <span class="btn btn-secondary" style="padding:10px 18px;font-size:22px">${ico("file", 22)} Open material</span>
      </div>`), 30, 92));
      const stage = place(h(`<div class="stage"></div>`), 30, 170, 1080, 410);
      const page = (n) => place(h(`<div class="pdf-page p${n}">
        ${n === 1
          ? `<div class="chap">CHAPTER 9</div><div class="title">Early Japan and Korea</div>`
          : `<div style="height:40px"></div>`}
        <div class="cols">
          <div>${n === 2 ? '<div class="sub">Japan</div>' : ""}${Array.from({ length: n === 1 ? 9 : 12 }, (_, i) => `<div class="tl" style="width:${86 + ((i * 37) % 14)}%"></div>`).join("")}</div>
          <div>${n === 2 ? '<div class="sub">Korea</div>' : ""}${Array.from({ length: n === 1 ? 9 : 12 }, (_, i) => `<div class="tl" style="width:${84 + ((i * 53) % 16)}%"></div>`).join("")}</div>
        </div>
      </div>`), 290, 18, 500, 640);
      stage.append(page(1), page(2));
      win.append(stage);
      win.append(place(h(`<div class="material-bar">${ico("file", 22)} Asuka &amp; Nara Japan.pdf
        <span style="display:inline-flex;align-items:center;gap:12px;color:var(--muted)">${ico("arrowLeft", 22)}<span class="pg">Page 1 of 10</span>${ico("arrowRight", 22)}</span>
      </div>`), 30, 596));
      root.append(win, markEl("Start here"));
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      appear(root.querySelector(".window"), lt, 0.2, { dy: 40 });
      const p1 = root.querySelector(".p1");
      const p2 = root.querySelector(".p2");
      p1.style.opacity = String(ease.out(seg(lt, 1.6, 2.1)) * (1 - ease.out(seg(lt, 4.6, 5.0))));
      p1.style.transform = `translateY(${(1 - ease.out(seg(lt, 1.6, 2.2))) * 40}px)`;
      const flip = ease.inOut(seg(lt, 4.4, 5.0));
      p1.style.transform += ` translateX(${-flip * 1100}px) rotate(${-flip * 6}deg)`;
      p2.style.opacity = String(flip);
      p2.style.transform = `translateX(${(1 - flip) * 620}px)`;
      root.querySelector(".pg").textContent = lt > 4.7 ? "Page 2 of 10" : "Page 1 of 10";
      const mark = root.querySelector(".mark");
      if (lt > 5.4) fitMark(mark, root.querySelector(".p2 .sub"), 8);
      drawMark(mark, lt, 5.6);
    }
  },

  coach: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("Check my steps"));
      root.append(place(h(`<div class="toggle" style="position:absolute"><span class="track"><span class="knob"></span></span>Check my steps</div>`), 660, 250));
      const sheet = place(worksheet(["3(x + 4) = 18", "<span class='wrong'>3x + 4 = 18</span><span class='fixed'>3x + 12 = 18</span>", "3x = 6"], "Solve for x"), 660, 340, 720);
      root.append(sheet);
      root.append(place(h(`<div class="bubble tutor hint">Careful: the 3 multiplies the 4 too.</div>`), 1240, 470));
      root.append(place(h(`<div class="pill lang" style="position:absolute;font-size:26px">${ico("lock", 24)} Language lock: <strong class="lang-name">English</strong></div>`), 1250, 740));
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      appear(root.querySelector(".toggle"), lt, 0.3);
      const on = ease.out(seg(lt, 0.9, 1.2));
      root.querySelector(".knob").style.transform = `translateX(${on * 38}px)`;
      root.querySelector(".track").style.background = on > 0.5 ? "var(--primary)" : "#c9c0ad";
      appear(root.querySelector(".sheet"), lt, 0.5);
      const lines = root.querySelectorAll(".sheet .line");
      lines.forEach((line, i) => { line.style.clipPath = `inset(0 ${100 - seg(lt, 1.4 + i * 1.0, 2.2 + i * 1.0) * 100}% 0 0)`; });
      lines[2].style.clipPath = `inset(0 ${100 - seg(lt, 4.8, 5.4) * 100}% 0 0)`;
      const fixed = lt > 4.2;
      root.querySelector(".wrong").style.display = fixed ? "none" : "";
      root.querySelector(".fixed").style.display = fixed ? "" : "none";
      root.querySelector(".wrong").style.textDecoration = lt > 2.8 ? "underline wavy #e0614f 3px" : "none";
      appear(root.querySelector(".hint"), lt, 2.8, { pop: true, out: 4.6 });
      appear(root.querySelector(".lang"), lt, 5.8, { pop: true });
      const names = ["English", "Español", "Français", "日本語"];
      root.querySelector(".lang-name").textContent = names[Math.min(names.length - 1, Math.max(0, Math.floor((lt - 6.2) / 0.55)))];
    }
  },

  privacy: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("You're in control"));
      const rows = [
        ["monitor", "Only what you share", "you pick the window"],
        ["micOff", "Never recorded", "screen and voice stay in memory"],
        ["lock", "Never clicks or types", "it can only point"]
      ];
      rows.forEach(([glyph, label, sub], i) => {
        root.append(place(h(`<div class="check-row r${i}">
          <span class="box"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="#3f7a52" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" pathLength="100" stroke-dasharray="100" stroke-dashoffset="100"/></svg></span>
          <span class="ico">${ico(glyph, 44)}</span><span class="label">${label}</span><span class="sub">${sub}</span>
        </div>`), 680, 300 + i * 160));
      });
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      [0.8, 2.6, 5.0].forEach((start, i) => {
        const row = root.querySelector(`.r${i}`);
        appear(row, lt, start - 0.3, { dy: 30 });
        row.querySelector("path").setAttribute("stroke-dashoffset", String(100 - ease.out(seg(lt, start, start + 0.45)) * 100));
        highlight(row.querySelector(".label"), lt, start + 0.2, 0.6);
      });
    }
  },

  tech: {
    build() {
      const root = h(`<div></div>`);
      root.append(headline("How it works"));
      const nodes = [
        ["monitor", "Your browser", "screen preview, mic, marks"],
        ["sparkles", "Jot server", "Python · FastAPI · Pydantic"],
        ["globe", "Gemini Live", "sees frames, speaks, asks for marks"]
      ];
      nodes.forEach(([glyph, title, sub], i) => {
        root.append(place(h(`<div class="card node n${i}"><span class="ico">${ico(glyph, 56)}</span><h3>${title}</h3><p>${sub}</p></div>`), 620 + i * 420, 330));
      });
      root.append(h(`<svg class="wires" style="position:absolute;left:0;top:0" width="${W}" height="${H}">
        <path class="w0" d="M1000 440 H1040" />
        <path class="w1" d="M1420 440 H1460" />
      </svg>`));
      root.append(place(h(`<div class="flow-label l0">frames + voice →<br>← speech + marks</div>`), 935, 560, 170));
      root.append(place(h(`<div class="flow-label l1">context + tools →<br>← audio + tool calls</div>`), 1350, 560, 180));
      root.append(h(`<div class="packets">${Array.from({ length: 6 }, () => `<span style="position:absolute;width:20px;height:20px;border-radius:6px;background:var(--hl);box-shadow:0 0 0 3px #1c2e37"></span>`).join("")}</div>`));
      const chips = ["google-genai SDK", "Vanilla JS + Vite", "SQLite", "pdf.js"];
      chips.forEach((chip, i) => root.append(place(h(`<div class="chip stack s${i}">${chip}</div>`), 700 + i * 280, 700)));
      return root;
    },
    update(lt, root) {
      appear(root.querySelector(".headline"), lt, 0.1);
      highlight(root.querySelector(".headline .hl-text"), lt, 0.5);
      [0, 1, 2].forEach((i) => appear(root.querySelector(`.n${i}`), lt, 0.4 + i * 0.6, { pop: true }));
      appear(root.querySelector(".l0"), lt, 1.6);
      appear(root.querySelector(".l1"), lt, 2.2);
      root.querySelectorAll(".packets span").forEach((packet, i) => {
        const lane = i % 2;
        const back = i >= 3;
        const cycle = ((lt - 2.4 - i * 0.37) % 1.6 + 1.6) % 1.6;
        const p = ease.inOut(clamp(cycle / 1.2));
        const x0 = lane ? 1395 : 975;
        const x1 = lane ? 1475 : 1055;
        const x = back ? lerp(x1, x0, p) : lerp(x0, x1, p);
        Object.assign(packet.style, {
          left: `${x}px`,
          top: `${back ? 470 : 410}px`,
          opacity: lt > 2.4 + i * 0.37 && cycle < 1.2 ? "1" : "0",
          transform: `rotate(${p * 90}deg)`
        });
      });
      [0, 1, 2, 3].forEach((i) => appear(root.querySelector(`.s${i}`), lt, 3.4 + i * 0.3, { pop: true }));
    }
  },

  outro: {
    build() {
      const root = h(`<div></div>`);
      root.append(
        place(h(`<div class="big-title" style="font-size:200px"><span class="hl-text">Jot</span></div>`), 0, 600),
        place(h(`<div class="big-sub" style="color:var(--text);font-family:var(--font-display);font-size:64px;font-weight:800;letter-spacing:-0.02em">Share. Ask. Understand.</div>`), 0, 820),
        place(h(`<div class="big-sub" style="font-size:30px">HackRU Fall 2026</div>`), 0, 920)
      );
      return root;
    },
    update(lt, root) {
      const [title, tag, event] = root.querySelectorAll(".big-title, .big-sub");
      appear(title, lt, 0.3, { dy: 40 });
      highlight(title.querySelector(".hl-text"), lt, 0.7, 0.8);
      appear(tag, lt, 1.2);
      appear(event, lt, 1.8);
    }
  }
};

// ---------------------------------------------------------------- timeline
let timeline = null;
const sceneRoots = {};
const captionWords = {};

function setup(data) {
  timeline = data;
  for (const scene of timeline.scenes) {
    const root = h(`<div class="scene" data-scene="${scene.id}"></div>`);
    root.append(SCENES[scene.id].build());
    sceneLayer.append(root);
    sceneRoots[scene.id] = root;
    const words = scene.text.split(/\s+/);
    const total = words.reduce((sum, word) => sum + word.length + 1, 0);
    let acc = 0;
    captionWords[scene.id] = words.map((word) => {
      const at = acc / total;
      acc += word.length + 1;
      return { word, at };
    });
  }
  // The outro and intro hide the caption: the title cards speak for themselves.
}

function envelopeAt(t) {
  const env = timeline.envelope;
  const i = t * timeline.fps;
  const a = env[Math.floor(i)] || 0;
  const b = env[Math.floor(i) + 1] || 0;
  return lerp(a, b, i - Math.floor(i));
}

function sceneAt(t) {
  const scenes = timeline.scenes;
  let index = 0;
  for (let i = 0; i < scenes.length; i += 1) if (t >= scenes[i].start) index = i;
  return index;
}

let lastCaption = "";

function renderCaption(scene, t) {
  const progress = seg(t, scene.speechStart, scene.speechEnd);
  const key = `${scene.id}:${Math.floor(progress * 400)}`;
  if (key === lastCaption) return;
  lastCaption = key;
  caption.querySelector(".words").innerHTML = captionWords[scene.id]
    .map(({ word, at }) => `<span class="w${progress > at ? " on" : ""}">${word}</span>`)
    .join(" ");
}

function expressionFor(id, lt) {
  if (id === "hints" && lt > 4.0) return "celebrate";
  if (id === "coach" && lt > 2.8 && lt < 4.2) return "concerned";
  if (id === "coach" && lt >= 4.2 && lt < 5.8) return "proud";
  if (id === "privacy" && lt > 6.2) return "proud";
  if (id === "problem" && lt > 3.6 && lt < 5.2) return "confused";
  if (id === "outro" && lt > 0.8) return "celebrate";
  return null;
}

function renderJot(t, index) {
  const scenes = timeline.scenes;
  const scene = scenes[index];
  const lt = t - scene.start;
  const prev = scenes[Math.max(0, index - 1)];
  const [x1, y1, s1] = POSES[scene.id];
  const [x0, y0, s0] = POSES[prev.id];
  const move = index === 0 ? 1 : ease.inOut(seg(lt, 0, 0.9));
  const hop = index === 0 ? 0 : Math.sin(move * Math.PI) * 60 * (Math.abs(x1 - x0) > 50 ? 1 : 0);
  const enter = index === 0 ? ease.back(seg(t, 0.15, 0.85)) : 1;
  const x = lerp(x0, x1, move);
  const y = lerp(y0, y1, move) - hop;
  const s = lerp(s0, s1, move) * enter;
  jot.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  jot.style.opacity = String(seg(t, 0.1, 0.3));

  const env = envelopeAt(t);
  const speaking = env > 0.04;
  const level = clamp(env);
  const expr = expressionFor(scene.id, lt);

  // Body: a gentle breath, a bounce with the voice, a hop when celebrating.
  const celebrateHop = expr === "celebrate" ? Math.abs(Math.sin(t * 6)) * 6 : 0;
  J.figure.style.transform = `translateY(${Math.sin(t * 1.65) * -1.5 - level * 3 - celebrateHop}px) scale(${1 + Math.sin(t * 1.65) * 0.012 + level * 0.03}, ${1 - Math.sin(t * 1.65) * 0.008})`;
  J.shadow.style.transform = `scaleX(${1 - celebrateHop * 0.03})`;

  // Blink about every 3.6 seconds; look toward the scene while it plays.
  const blink = (t % 3.6) < 0.12 ? 0.1 : 1;
  const look = scene.id === "intro" || scene.id === "outro" ? 0 : 3;
  J.eyes.style.transform = `translateX(${look}px) scaleY(${blink})`;

  // Expressions and props.
  for (const [name, el] of Object.entries(J.exprs)) el.style.opacity = name === expr ? "1" : "0";
  J.props.sparkles.style.opacity = expr === "celebrate" || expr === "proud" ? String(0.6 + Math.sin(t * 8) * 0.4) : "0";
  J.props.sweat.style.opacity = expr === "concerned" ? "1" : "0";
  J.props.question.style.opacity = expr === "confused" ? "1" : "0";
  J.eyes.style.opacity = expr ? "0" : "1";
  J.mouth.style.opacity = expr || speaking ? "0" : "1";
  for (const el of Object.values(J.exprs)) {
    const mouth = el.querySelector(".jot-expr-mouth");
    if (mouth) mouth.style.opacity = speaking ? "0" : "1";
  }
  J.talk.style.opacity = speaking ? "1" : "0";
  J.talk.style.transform = `scaleY(${0.22 + level * 0.95})`;

  // Arms: a wave to say hi and bye, celebrating arms up, gestures while talking.
  const waving = (scene.id === "intro" && lt > 0.6 && lt < 3.4) || (scene.id === "outro" && lt > 3.2);
  let armL = 14 + Math.sin(t * 1.4) * 3;
  let foreL = -10;
  let armR = -14 - Math.sin(t * 1.4) * 3;
  let foreR = 10;
  if (speaking) {
    armL = 30 + Math.sin(t * 3.7) * 12 * (0.4 + level);
    foreL = -50 - Math.sin(t * 3.7 + 0.6) * 18;
    armR = -22 - Math.sin(t * 2.7 + 1) * 10 * (0.4 + level);
  }
  if (expr === "celebrate") {
    armL = 150 + Math.sin(t * 8) * 10;
    foreL = 20;
    armR = -150 - Math.sin(t * 8 + 1) * 10;
    foreR = -20;
  } else if (expr === "proud") {
    armL = 40;
    foreL = -95;
    armR = -40;
    foreR = 95;
  } else if (expr === "confused") {
    armR = -150;
    foreR = -60 + Math.sin(t * 6) * 12;
  }
  if (waving) {
    armR = -150;
    foreR = Math.sin(t * 9) * 26;
  }
  J.armL.style.transform = `rotate(${armL}deg)`;
  J.foreL.style.transform = `rotate(${foreL}deg)`;
  J.armR.style.transform = `rotate(${armR}deg)`;
  J.foreR.style.transform = `rotate(${foreR}deg)`;
  const legKick = expr === "celebrate" ? Math.sin(t * 12) * 10 : 0;
  J.legL.style.transform = `rotate(${4 + legKick}deg)`;
  J.legR.style.transform = `rotate(${-4 - legKick}deg)`;
}

/** Draw the film at time t (seconds). */
function seek(t) {
  if (!timeline) return;
  const index = sceneAt(t);
  const scenes = timeline.scenes;
  scenes.forEach((scene, i) => {
    const root = sceneRoots[scene.id];
    const visible = i === index || (i === index - 1 && t < scene.end + 0.45);
    if (!visible) {
      root.style.opacity = "0";
      root.style.visibility = "hidden";
      return;
    }
    root.style.visibility = "visible";
    const fadeIn = i === 0 ? 1 : ease.out(seg(t, scene.start, scene.start + 0.45));
    const fadeOut = 1 - ease.out(seg(t, scene.end, scene.end + 0.45));
    root.style.opacity = String(Math.min(fadeIn, i === index ? 1 : fadeOut));
    SCENES[scene.id].update(t - scene.start, root.firstElementChild);
  });
  const scene = scenes[index];
  const captionVisible = scene.id !== "intro" && scene.id !== "outro";
  caption.style.opacity = String(captionVisible ? seg(t, scene.start + 0.2, scene.start + 0.6) : 1 - seg(t, scene.start, scene.start + 0.3));
  caption.style.transform = `translateY(${captionVisible ? (1 - ease.out(seg(t, scene.start + 0.2, scene.start + 0.6))) * 16 : 0}px)`;
  renderCaption(scene, t);
  renderJot(t, index);
}

window.__setup = setup;
window.__seek = seek;
window.__ready = document.fonts.ready.then(() => Promise.all([...document.images].map((img) => img.decode().catch(() => {}))));

// ---------------------------------------------------------------- live preview
const params = new URLSearchParams(location.search);
if (!params.has("render")) {
  const fit = () => {
    const scale = Math.min(innerWidth / W, innerHeight / H);
    film.style.transform = `translate(${(innerWidth - W * scale) / 2}px, ${(innerHeight - H * scale) / 2}px) scale(${scale})`;
  };
  addEventListener("resize", fit);
  fit();
  fetch("./out/timeline.json").then((r) => r.json()).then((data) => {
    setup(data);
    seek(0);
    const button = document.getElementById("start");
    button.hidden = false;
    button.addEventListener("click", () => {
      button.hidden = true;
      const audio = new Audio("./out/soundtrack.wav");
      audio.play();
      const tick = () => {
        seek(audio.currentTime);
        if (!audio.ended) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  });
}
