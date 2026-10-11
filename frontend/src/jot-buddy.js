import { avatarSvg } from "./avatar.js";

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
    this.disposed = false;

    root.classList.add("jot-buddy");
    root.innerHTML = `
      <div class="jot-buddy-art">${avatarSvg(avatar, { size: 72, animated: true })}</div>
      <div class="jot-buddy-text">
        <span class="jot-buddy-name">Your Jot</span>
        <span class="jot-buddy-status"></span>
      </div>`;
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
    }
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
    this.connection = ["connecting", "connected", "error"].includes(status) ? status : "offline";
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
    this.stopSpeaking();
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
