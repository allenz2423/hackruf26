import { requestLiveSession } from "./api.js";

const SESSION_PROTOCOL = "screen-tutor.v1";
const MAX_BUFFERED_BYTES = 4_000_000;

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
    onStatusChange,
    onTutorText,
    onMemoryState
  } = {}) {
    this.classId = classId;
    // Backend upload ids for the class context; null uses every ready upload.
    this.uploadIds = null;
    this.onMessage = onMessage || (() => {});
    this.onAnnotationRequest = onAnnotationRequest || (() => ({ accepted: 0, errors: [] }));
    this.onAudioData = onAudioData || (() => {});
    this.onAudioInterrupted = onAudioInterrupted || (() => {});
    this.onStatusChange = onStatusChange || (() => {});
    this.onTutorText = onTutorText || (() => {});
    this.onMemoryState = onMemoryState || (() => {});
    this.memoryEnabled = false;
    this.ending = false;
    this.resolveEnd = null;
    this.endTimer = null;
    this.endPromise = null;
    this.socket = null;
    this.outputText = "";
    this.failed = false;
    this.intentionalClose = false;
    this.readyTimer = null;
    this.resolveReady = null;
    this.rejectReady = null;
    this.connectionId = 0;
  }

  async connect({ mode = "conversation", language = "auto", useSavedMemory = true } = {}) {
    if (this.ending) throw new Error("Wait for the current session to finish saving.");
    this.rejectPending(new Error("A new tutoring session was started."));
    this.detachSocket();
    const connectionId = ++this.connectionId;
    this.failed = false;
    this.intentionalClose = false;
    this.memoryEnabled = false;
    this.onStatusChange("connecting");
    const { ticket } = await requestLiveSession(this.classId, mode, language, this.uploadIds, useSavedMemory);
    if (connectionId !== this.connectionId) throw new Error("The tutoring session was ended.");
    const socket = new WebSocket(websocketUrl(), [SESSION_PROTOCOL, `ticket.${ticket}`]);
    this.socket = socket;

    await new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
      this.readyTimer = window.setTimeout(() => {
        this.markFailed(new Error("The tutoring service did not become ready. Please try again."));
      }, 30_000);

      socket.onmessage = (event) => {
        if (this.socket === socket && !this.failed) this.handleMessage(event.data);
      };
      socket.onerror = () => {
        if (this.socket !== socket) return;
        if (this.ending) { this.completeEnd("unconfirmed"); return; }
        this.markFailed(new Error("The tutoring service could not connect. Please try again."));
      };
      socket.onclose = () => {
        if (this.socket !== socket) return;
        if (this.ending) { this.completeEnd("unconfirmed"); return; }
        if (this.intentionalClose) {
          this.onStatusChange("closed");
          return;
        }
        this.markFailed(new Error("The tutoring session ended unexpectedly. Please try again."));
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

  markFailed(error = new Error("The tutoring session could not continue. Please try again.")) {
    if (this.ending) { this.completeEnd("unconfirmed"); return; }
    if (this.failed) return;
    this.failed = true;
    this.outputText = "";
    this.rejectPending(error);
    this.detachSocket();
    this.onAudioInterrupted();
    this.onStatusChange("error", error.message);
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
      if (this.ending) return;
      this.memoryEnabled = message.memory?.enabled === true;
      this.onMemoryState(message.memory || { enabled: false, loaded_count: 0 });
      this.markReady();
      return;
    }

    if (message.type === "session_ended" && this.ending) {
      const status = ["saved", "disabled", "empty", "failed"].includes(message.memory_status)
        ? message.memory_status : "unconfirmed";
      this.completeEnd(status);
      return;
    }
    if (message.type === "error") {
      if (this.ending) { this.completeEnd("failed"); return; }
      this.markFailed(new Error(message.message || "The tutoring session could not continue."));
      return;
    }

    if (this.ending) return;

    if (message.type === "assistant") {
      if (message.interrupted) {
        this.outputText = "";
        this.onAudioInterrupted();
      }
      if (typeof message.text === "string" && message.text) {
        this.outputText += message.text;
        this.onTutorText(this.outputText);
      }
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
    if (this.ending && payload.type !== "end_session") return false;
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
    if (this.socket?.bufferedAmount > MAX_BUFFERED_BYTES) {
      this.markFailed(new Error("The connection is too slow for microphone audio. Reconnect to try again."));
      return false;
    }
    return data ? this.send({ type: "audio", data }) : false;
  }

  endAudioInput() {
    this.send({ type: "audio_end" });
  }

  pauseScreen() {
    this.outputText = "";
    this.onAudioInterrupted();
    return this.send({ type: "screen_paused" });
  }

  sendFrame(frame) {
    // Drop this sample on congestion; the next sample contains the current screen.
    if (!frame?.jpeg || !frame?.frame_id || this.socket?.bufferedAmount > MAX_BUFFERED_BYTES) return false;
    const data = frame.jpeg.split(",")[1];
    if ((this.socket?.bufferedAmount || 0) + data.length + 128 > MAX_BUFFERED_BYTES) return false;
    return this.send({
      type: "frame",
      frame_id: frame.frame_id,
      data
    });
  }

  detachSocket() {
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    socket.close(1000);
  }

  finish() {
    if (this.ending) return this.endPromise;
    if (this.failed || this.resolveReady || this.socket?.readyState !== WebSocket.OPEN) {
      this.close();
      return Promise.resolve("unconfirmed");
    }
    this.ending = true;
    this.rejectPending(new Error("The tutoring session was ended."));
    this.intentionalClose = true;
    this.outputText = "";
    this.onAudioInterrupted();
    this.endPromise = new Promise((resolve) => { this.resolveEnd = resolve; });
    // Capture the promise before send(), whose failure may close synchronously.
    const promise = this.endPromise;
    this.endTimer = window.setTimeout(() => this.completeEnd("unconfirmed"), 30_000);
    if (!this.send({ type: "end_session" })) this.completeEnd("unconfirmed");
    return promise;
  }

  completeEnd(status) {
    const resolve = this.resolveEnd;
    this.resolveEnd = null;
    if (this.endTimer) window.clearTimeout(this.endTimer);
    this.endTimer = null;
    this.ending = false;
    this.endPromise = null;
    this.close();
    resolve?.(status);
  }

  close() {
    const resolveEnd = this.resolveEnd;
    this.resolveEnd = null;
    if (this.endTimer) window.clearTimeout(this.endTimer);
    this.endTimer = null;
    this.ending = false;
    this.endPromise = null;
    this.connectionId += 1;
    this.intentionalClose = true;
    this.failed = true;
    if (this.readyTimer) window.clearTimeout(this.readyTimer);
    this.readyTimer = null;
    this.rejectPending(new Error("The tutoring session was ended."));
    this.onAudioInterrupted();
    this.detachSocket();
    this.outputText = "";
    this.onStatusChange("closed");
    resolveEnd?.("unconfirmed");
  }
}
