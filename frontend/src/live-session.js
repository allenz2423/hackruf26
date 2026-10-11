import { requestLiveSession } from "./api.js";

const SESSION_PROTOCOL = "screen-tutor.v1";

function websocketUrl() {
  const url = new URL("/api/live", window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export class LiveSessionController {
  constructor({
    classId = null,
    onMessage,
    onAnnotationRequest,
    onAudioData,
    onAudioInterrupted,
    onStatusChange
  } = {}) {
    this.classId = classId;
    this.onMessage = onMessage || (() => {});
    this.onAnnotationRequest = onAnnotationRequest || (() => ({ accepted: 0, errors: [] }));
    this.onAudioData = onAudioData || (() => {});
    this.onAudioInterrupted = onAudioInterrupted || (() => {});
    this.onStatusChange = onStatusChange || (() => {});
    this.socket = null;
    this.outputText = "";
    this.failed = false;
    this.intentionalClose = false;
    this.readyTimer = null;
    this.resolveReady = null;
    this.rejectReady = null;
  }

  async connect() {
    this.failed = false;
    this.intentionalClose = false;
    this.onStatusChange("connecting");
    const { ticket } = await requestLiveSession(this.classId);
    const socket = new WebSocket(websocketUrl(), [SESSION_PROTOCOL, `ticket.${ticket}`]);
    this.socket = socket;

    await new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
      this.readyTimer = window.setTimeout(() => {
        this.markFailed();
        reject(new Error("The tutoring service did not become ready. Please try again."));
      }, 30_000);

      socket.onmessage = (event) => this.handleMessage(event.data);
      socket.onerror = () => {
        const error = new Error("The tutoring service could not connect. Please try again.");
        this.markFailed();
        this.rejectPending(error);
      };
      socket.onclose = () => {
        if (this.intentionalClose) {
          this.onStatusChange("closed");
          return;
        }
        const error = new Error("The tutoring session ended unexpectedly. Please try again.");
        this.markFailed();
        this.rejectPending(error);
      };
    });
  }

  rejectPending(error) {
    if (this.readyTimer) window.clearTimeout(this.readyTimer);
    this.readyTimer = null;
    const reject = this.rejectReady;
    this.resolveReady = null;
    this.rejectReady = null;
    reject?.(error);
  }

  markReady() {
    if (this.failed) return;
    if (this.readyTimer) window.clearTimeout(this.readyTimer);
    this.readyTimer = null;
    this.onStatusChange("connected");
    const resolve = this.resolveReady;
    this.resolveReady = null;
    this.rejectReady = null;
    resolve?.();
  }

  markFailed() {
    if (this.failed) return;
    this.failed = true;
    this.outputText = "";
    this.onAudioInterrupted();
    this.onStatusChange("error");
  }

  handleMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch {
      this.markFailed();
      return;
    }

    if (message.type === "ready") {
      this.markReady();
      return;
    }

    if (message.type === "error") {
      this.markFailed();
      this.rejectPending(new Error(message.message || "The tutoring session could not continue."));
      return;
    }

    if (message.type === "assistant") {
      if (message.interrupted) {
        this.outputText = "";
        this.onAudioInterrupted();
      }
      if (typeof message.text === "string") this.outputText += message.text;
      for (const chunk of message.audio || []) {
        this.onAudioData(chunk.data, chunk.mime_type);
      }
      if (message.turn_complete) {
        const text = this.outputText.trim();
        if (text) this.onMessage({ role: "tutor", text });
        this.outputText = "";
      }
      return;
    }

    if (message.type === "annotation_request") {
      let result;
      try {
        result = this.onAnnotationRequest(message.payload) || { accepted: 0, errors: [] };
      } catch {
        result = { accepted: 0, errors: ["The requested marks could not be displayed."] };
      }
      const acceptedValue = Array.isArray(result.accepted)
        ? result.accepted.length
        : Number(result.accepted);
      const accepted = Number.isFinite(acceptedValue)
        ? Math.max(0, Math.min(5, Math.trunc(acceptedValue)))
        : 0;
      const errors = (result.errors || []).slice(0, 5).map((error) => String(error).slice(0, 200));
      this.send({
        type: "annotation_result",
        call_id: message.call_id,
        status: accepted > 0 ? "rendered" : "rejected",
        accepted,
        errors
      });
    }
  }

  send(payload) {
    if (this.failed || this.socket?.readyState !== WebSocket.OPEN) return false;
    try {
      this.socket.send(JSON.stringify(payload));
      return true;
    } catch {
      this.markFailed();
      return false;
    }
  }

  sendText(text) {
    if (!text.trim()) return false;
    this.onAudioInterrupted();
    return this.send({ type: "text", text });
  }

  sendAudio(data) {
    if (data) this.send({ type: "audio", data });
  }

  endAudioInput() {
    this.send({ type: "audio_end" });
  }

  sendFrame(frame) {
    if (!frame?.jpeg || !frame?.frame_id) return;
    this.send({
      type: "frame",
      frame_id: frame.frame_id,
      data: frame.jpeg.split(",")[1]
    });
  }

  close() {
    this.intentionalClose = true;
    this.failed = true;
    if (this.readyTimer) window.clearTimeout(this.readyTimer);
    this.readyTimer = null;
    this.rejectPending(new Error("The tutoring session was ended."));
    this.onAudioInterrupted();
    this.socket?.close(1000);
    this.socket = null;
    this.outputText = "";
    this.onStatusChange("closed");
  }
}
