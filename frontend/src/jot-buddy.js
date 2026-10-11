import { avatarSvg } from "./avatar.js";
import { renderMathInto } from "./math.js";

// Your Jot, alive in the tutor panel. It watches what the live session is doing and
// shows it: resting, listening to you, thinking, or talking. While the tutor speaks
// its mouth follows the loudness of the real voice as it plays. CSS does the moving
// (see "Jot buddy" in styles.css); this class only decides the state and feeds the
// mouth and bounce values.

const STATUS_TEXT = {
  offline: "Share your screen to start",
  connecting: "Getting ready...",
  ready: "Ready. Ask me anything.",
  listening: "Listening...",
  thinking: "Thinking...",
  speaking: "Talking...",
  error: "Lost the connection"
};

// What the tutor says decides the face. Checked in order, so "Not quite, but great
// effort" reads as concerned rather than proud. English phrases only for now.
const EXPRESSION_PATTERNS = [
  ["concerned", /\b(not quite|not exactly|not correct|not right|incorrect|almost|so close|close,? but|careful|mistake|oops|small error|slight error|double-check|recheck|re-check|that's not|that isn't|isn't quite|try again|take another look|look again)\b/i],
  ["celebrate", /\b(correct|exactly|perfect|you got it|you've got it|you have it|got it right|that's it|that is it|that's right|that is right|you're right|you are right|right on|spot on|nailed it|precisely|absolutely right|bingo|bravo)\b/i],
  ["proud", /\b(great job|good job|well done|nice work|great work|good work|nicely done|excellent|awesome|amazing|fantastic|wonderful|brilliant|impressive|proud of you|great thinking|good thinking|nice thinking|great (observation|insight|catch|question|point|reasoning|start)|good (observation|catch|question|point|reasoning|start)|nice (catch|observation|reasoning)|keep it up|great progress|good progress)\b/i],
  ["confused", /\b(i'?m not sure|not sure what|could you (clarify|explain|tell me more)|what do you mean|can you (show|share|clarify|rephrase)|unclear|i can'?t (see|read|tell))\b/i]
];

// Short praise usually opens the reply: "Yes!", "Great!", "Nice!", "Right!".
const OPENING_PATTERNS = [
  ["celebrate", /^\W*(yes|yep|yeah|yup|exactly|correct|right[!.])/i],
  ["proud", /^\W*(great|nice|good|awesome|excellent|fantastic|wonderful|brilliant|amazing)\b\W/i]
];

export function expressionFor(text) {
  const value = String(text || "");
  for (const [expression, pattern] of EXPRESSION_PATTERNS) {
    if (pattern.test(value)) return expression;
  }
  for (const [expression, pattern] of OPENING_PATTERNS) {
    if (pattern.test(value)) return expression;
  }
  return null;
}

// While the tutor's words are still arriving, look only at how the reply opens:
// that is where praise and "not quite" live, and reacting then puts the face on
// screen as the tutor says it rather than after a long explanation.
const OPENING_CHARS = 160;

const EXPRESSION_MS = 4200;
const WAVE_MS = 2600;
const BUBBLE_TEXT = {
  listening: "Listening",
  thinking: "Thinking",
  error: "Lost the connection"
};

// The last sentence or so of what the tutor is saying, short enough for the bubble.
const CAPTION_CHARS = 140;
export function captionFor(text) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= CAPTION_CHARS) return clean;
  const sentences = clean.split(/(?<=[.!?])\s+/);
  let caption = sentences.pop() || "";
  while (sentences.length && caption.length < 40) caption = `${sentences.pop()} ${caption}`;
  if (caption.length > CAPTION_CHARS) {
    const tail = caption.slice(-CAPTION_CHARS);
    caption = tail.slice(tail.indexOf(" ") + 1);
  }
  return caption === clean ? caption : `…${caption}`;
}

const LISTEN_HOLD_MS = 600;
const LISTEN_THRESHOLD = 0.1;
const THINKING_TIMEOUT_MS = 15_000;
const SILENCE_BEFORE_REST_MS = 250;
const MOUTH_REST = 0.14;

export class JotBuddy {
  constructor({ root, avatar, getSpeechLevel = () => 0, isSpeaking = () => false }) {
    this.root = root;
    this.getSpeechLevel = getSpeechLevel;
    this.isSpeaking = isSpeaking;
    this.connection = "offline";
    this.micOn = false;
    this.thinking = false;
    this.speaking = false;
    this.listeningUntil = 0;
    this.mouth = MOUTH_REST;
    this.frame = 0;
    this.silentSince = null;
    this.timers = new Set();
    this.expressionTimer = 0;
    this.reactedThisTurn = false;
    this.disposed = false;

    root.classList.add("jot-buddy");
    // The bubble is decoration for sighted users: what the tutor says also goes to the
    // conversation list, which screen readers follow, and the state is in a hidden status.
    root.innerHTML = `
      <div class="jot-buddy-art">${avatarSvg(avatar, { size: 136, animated: true })}</div>
      <div class="jot-bubble" aria-hidden="true">
        <span class="jot-bubble-text"></span>
        <span class="jot-bubble-dots"><i></i><i></i><i></i></span>
      </div>
      <span class="jot-buddy-status visually-hidden"></span>`;
    this.bubbleText = root.querySelector(".jot-bubble-text");
    this.turnText = "";
    this.shownCaption = "";
    this.statusEl = root.querySelector(".jot-buddy-status");
    this.render();
  }

  // The state that wins when several are true: talking, then thinking, then listening.
  state() {
    if (this.connection === "error") return "error";
    if (this.speaking) return "speaking";
    if (this.thinking) return "thinking";
    if (this.micOn && performance.now() < this.listeningUntil) return "listening";
    if (this.connection === "connected") return "ready";
    return this.connection;
  }

  render() {
    if (this.disposed) return;
    const state = this.state();
    if (this.root.dataset.state !== state) {
      this.root.dataset.state = state;
      this.statusEl.textContent = STATUS_TEXT[state] || STATUS_TEXT.offline;
      if (state !== "speaking" && BUBBLE_TEXT[state]) this.setBubble(BUBBLE_TEXT[state]);
    }
    if (state === "speaking") this.setBubble(captionFor(this.turnText));
  }

  setBubble(text) {
    if (text === this.shownCaption) return;
    this.shownCaption = text;
    renderMathInto(this.bubbleText, text);
  }

  later(callback, delay) {
    const id = setTimeout(() => {
      this.timers.delete(id);
      callback();
    }, delay);
    this.timers.add(id);
    return id;
  }

  // "connecting", "connected", "closed", "error", or "offline" from the live session.
  setConnection(status) {
    const wasConnected = this.connection === "connected";
    this.connection = ["connecting", "connected", "error"].includes(status) ? status : "offline";
    if (this.connection === "connected" && !wasConnected) this.showExpression("wave", WAVE_MS);
    if (this.connection !== "connected") this.clearExpression();
    if (this.connection !== "connected") {
      this.thinking = false;
      this.listeningUntil = 0;
    }
    this.render();
  }

  setMicOn(on) {
    this.micOn = Boolean(on);
    if (!on) this.listeningUntil = 0;
    this.render();
  }

  // The microphone's input level, 0 to 1. Voice above the threshold means "listening".
  micLevel(level) {
    if (!this.micOn || level < LISTEN_THRESHOLD) return;
    const wasListening = performance.now() < this.listeningUntil;
    this.listeningUntil = performance.now() + LISTEN_HOLD_MS;
    if (!wasListening) {
      this.render();
      this.later(() => this.render(), LISTEN_HOLD_MS + 20);
    }
  }

  questionSent() {
    this.thinking = true;
    this.render();
    this.later(() => {
      if (this.thinking) {
        this.thinking = false;
        this.render();
      }
    }, THINKING_TIMEOUT_MS);
  }

  // Voice from the tutor has started arriving.
  audioArrived() {
    this.thinking = false;
    if (!this.frame) {
      this.silentSince = null;
      this.frame = requestAnimationFrame((now) => this.tick(now));
    }
  }

  // The student talked over the tutor, or the session reset.
  interrupted() {
    this.thinking = false;
    this.turnText = "";
    this.reactedThisTurn = false;
    this.stopSpeaking();
  }

  // React to a finished tutor message: proud, celebrating, confused, or concerned.
  react(text) {
    if (!this.reactedThisTurn) {
      const expression = expressionFor(text);
      if (expression) this.showExpression(expression, EXPRESSION_MS);
    }
    this.reactedThisTurn = false;
  }

  // The tutor's words so far in the current turn, as they stream in.
  hear(textSoFar) {
    this.turnText = String(textSoFar || "");
    if (this.speaking) this.render();
    if (this.reactedThisTurn) return;
    const expression = expressionFor(String(textSoFar || "").slice(0, OPENING_CHARS));
    // "Can you clarify?" can come at the end, so confusion waits for the whole turn.
    if (!expression || expression === "confused") return;
    this.reactedThisTurn = true;
    this.showExpression(expression, EXPRESSION_MS);
  }

  showExpression(expression, duration) {
    if (this.disposed) return;
    if (this.expressionTimer) clearTimeout(this.expressionTimer);
    this.timers.delete(this.expressionTimer);
    this.root.dataset.expression = expression;
    this.expressionTimer = this.later(() => this.clearExpression(), duration);
  }

  clearExpression() {
    if (this.expressionTimer) clearTimeout(this.expressionTimer);
    this.timers.delete(this.expressionTimer);
    this.expressionTimer = 0;
    delete this.root.dataset.expression;
  }

  // The tutor finished a turn without (more) voice, so there is nothing left to wait for.
  turnComplete() {
    this.thinking = false;
    this.render();
  }

  tick(now) {
    this.frame = 0;
    if (this.disposed) return;

    if (this.isSpeaking()) {
      this.silentSince = null;
      // Ease toward the voice: open quickly, close a little slower, like a real mouth.
      const level = Math.min(1, Math.max(0, this.getSpeechLevel()));
      const target = MOUTH_REST + level * (1 - MOUTH_REST);
      this.mouth += (target - this.mouth) * (target > this.mouth ? 0.6 : 0.35);
      this.root.style.setProperty("--mouth", this.mouth.toFixed(3));
      this.root.style.setProperty("--bounce", Math.min(1, level * 1.3).toFixed(3));
      if (!this.speaking) {
        this.speaking = true;
        this.render();
      }
    } else {
      this.silentSince ??= now;
      if (now - this.silentSince > SILENCE_BEFORE_REST_MS) {
        this.stopSpeaking();
        return;
      }
    }
    this.frame = requestAnimationFrame((time) => this.tick(time));
  }

  stopSpeaking() {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.mouth = MOUTH_REST;
    this.root.style.setProperty("--mouth", String(MOUTH_REST));
    this.root.style.setProperty("--bounce", "0");
    this.speaking = false;
    this.render();
  }

  dispose() {
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    for (const id of this.timers) clearTimeout(id);
    this.timers.clear();
  }
}
