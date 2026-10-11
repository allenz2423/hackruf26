import pcmCaptureWorkletUrl from "./pcm-capture-worklet.js?url&no-inline";

function pcmBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function pcmBase64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function microphoneError(error) {
  const messages = {
    NotAllowedError: "Microphone access was blocked. Allow it in your browser's site permissions and try again.",
    SecurityError: "Microphone access is disabled in this browser. Check your site permissions.",
    NotFoundError: "No microphone was found. Connect a microphone and try again.",
    NotReadableError: "The microphone could not be opened. Check your system permissions and whether another app is using it.",
    AbortError: "The microphone could not start. Try turning it on again.",
    OverconstrainedError: "This microphone does not support the requested audio settings. Try another microphone."
  };
  return new Error(messages[error.name] || error.message || "The microphone could not start.");
}

export class AudioController {
  constructor({ onStatusChange, onAudioChunk, onAudioLevel, onError } = {}) {
    this.onStatusChange = onStatusChange || (() => {});
    this.onAudioChunk = onAudioChunk || (() => {});
    this.onAudioLevel = onAudioLevel || (() => {});
    this.onError = onError || (() => {});
    this.stream = null;
    this.enabled = false;
    this.requesting = false;
    this.captureRequestId = 0;
    this.context = null;
    this.source = null;
    this.processor = null;
    this.trackEndedHandler = null;
    this.trackMuteHandler = null;
    this.trackUnmuteHandler = null;
    this.workletLoaded = false;
    this.lastLevelAt = 0;
    this.playbackSources = new Set();
    this.nextPlaybackTime = 0;
  }

  ensureContext() {
    if (this.context) return this.context;

    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor) {
      throw new Error("Audio is not supported in this browser.");
    }

    this.context = new AudioContextConstructor({ latencyHint: "interactive" });
    return this.context;
  }

  async preparePlayback() {
    const context = this.ensureContext();
    if (context.state === "suspended") await context.resume();
    if (context.state !== "running") {
      throw new Error("Audio playback could not be started in this browser.");
    }
  }

  async enable() {
    if (this.enabled || this.requesting) return this.stream;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not supported in this browser.");
    }

    const requestId = ++this.captureRequestId;
    this.requesting = true;
    this.onStatusChange("requesting");
    let stream;
    let source;
    let processor;

    try {
      const context = this.ensureContext();
      await this.preparePlayback();
      if (requestId !== this.captureRequestId) return null;
      if (!context.audioWorklet || typeof AudioWorkletNode === "undefined") {
        throw new Error("Microphone audio is not supported in this browser.");
      }

      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true }
        },
        video: false
      });
      if (requestId !== this.captureRequestId) {
        stream.getTracks().forEach((track) => track.stop());
        return null;
      }
      this.stream = stream;
      if (!this.workletLoaded) {
        await context.audioWorklet.addModule(pcmCaptureWorkletUrl);
        if (requestId !== this.captureRequestId) {
          stream.getTracks().forEach((track) => track.stop());
          return null;
        }
        this.workletLoaded = true;
      }

      const track = stream.getAudioTracks()[0];
      if (!track || track.readyState === "ended") {
        throw new Error("The microphone disconnected. Connect it and try again.");
      }
      source = context.createMediaStreamSource(stream);
      processor = new AudioWorkletNode(context, "screen-tutor-pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
        channelCountMode: "explicit"
      });
      processor.port.onmessage = (event) => {
        if (!this.enabled || this.processor !== processor || track.muted) return;
        this.onAudioChunk(pcmBufferToBase64(event.data.pcm));
        if (!this.enabled || this.processor !== processor) return;
        const now = performance.now();
        if (now - this.lastLevelAt >= 100) {
          this.lastLevelAt = now;
          this.onAudioLevel(Math.min(1, Math.sqrt(event.data.level) * 2));
        }
      };
      processor.onprocessorerror = () => {
        if (this.processor !== processor) return;
        this.disable();
        this.onError(new Error("Microphone audio stopped. Turn the microphone on again to retry."));
      };

      source.connect(processor);
      processor.connect(context.destination);

      this.stream = stream;
      this.source = source;
      this.processor = processor;
      this.enabled = true;
      this.requesting = false;
      this.lastLevelAt = 0;
      this.trackEndedHandler = () => {
        this.disable();
        this.onError(new Error("The microphone disconnected or access was revoked. Check your device and permissions, then try again."));
      };
      this.trackMuteHandler = () => {
        this.onAudioLevel(0);
        this.onStatusChange("muted", track.label);
      };
      this.trackUnmuteHandler = () => this.onStatusChange("on", track.label);
      track.addEventListener("ended", this.trackEndedHandler, { once: true });
      track.addEventListener("mute", this.trackMuteHandler);
      track.addEventListener("unmute", this.trackUnmuteHandler);
      this.onStatusChange(track.muted ? "muted" : "on", track.label);
      return stream;
    } catch (error) {
      source?.disconnect();
      processor?.disconnect();
      if (processor) {
        processor.port.onmessage = null;
        processor.port.close();
        processor.onprocessorerror = null;
      }
      stream?.getTracks().forEach((track) => track.stop());
      if (requestId !== this.captureRequestId) return null;
      this.enabled = false;
      this.requesting = false;
      this.stream = null;
      this.source = null;
      this.processor = null;
      this.onAudioLevel(0);
      this.onStatusChange("off");
      throw microphoneError(error);
    }
  }

  disable() {
    const wasActive = this.enabled || this.requesting;
    this.captureRequestId += 1;
    this.enabled = false;
    this.requesting = false;

    const track = this.stream?.getAudioTracks()[0];
    if (track && this.trackEndedHandler) {
      track.removeEventListener("ended", this.trackEndedHandler);
      track.removeEventListener("mute", this.trackMuteHandler);
      track.removeEventListener("unmute", this.trackUnmuteHandler);
    }
    this.trackEndedHandler = null;
    this.trackMuteHandler = null;
    this.trackUnmuteHandler = null;

    if (this.processor) {
      this.processor.port.onmessage = null;
      this.processor.port.close();
      this.processor.onprocessorerror = null;
      this.processor.disconnect();
    }
    this.source?.disconnect();
    this.stream?.getTracks().forEach((mediaTrack) => mediaTrack.stop());
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.onAudioLevel(0);

    if (wasActive) this.onStatusChange("off");
  }

  toggle() {
    if (this.enabled || this.requesting) {
      this.disable();
      return Promise.resolve(null);
    }
    return this.enable();
  }

  playPcm(base64, mimeType = "audio/pcm;rate=24000") {
    if (!base64 || !this.context) return;

    const context = this.context;
    if (context.state === "suspended") void context.resume().catch(() => {});

    const bytes = pcmBase64ToBytes(base64);
    const sampleCount = Math.floor(bytes.byteLength / 2);
    if (!sampleCount) return;

    const rate = Number(mimeType.match(/rate=(\d+)/i)?.[1]) || 24_000;
    const buffer = context.createBuffer(1, sampleCount, rate);
    const samples = buffer.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let index = 0; index < sampleCount; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 0x8000;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.onended = () => {
      source.disconnect();
      this.playbackSources.delete(source);
    };

    const startAt = Math.max(context.currentTime, this.nextPlaybackTime);
    source.start(startAt);
    this.nextPlaybackTime = startAt + buffer.duration;
    this.playbackSources.add(source);
  }

  stopPlayback() {
    for (const source of this.playbackSources) {
      try {
        source.stop();
      } catch {
        source.disconnect();
      }
    }
    this.playbackSources.clear();
    this.nextPlaybackTime = this.context?.currentTime || 0;
  }

  async close() {
    this.disable();
    this.stopPlayback();
    const context = this.context;
    this.context = null;
    this.workletLoaded = false;
    if (context && context.state !== "closed") await context.close();
  }
}
