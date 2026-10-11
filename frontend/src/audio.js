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

export class AudioController {
  constructor({ onStatusChange, onAudioChunk } = {}) {
    this.onStatusChange = onStatusChange || (() => {});
    this.onAudioChunk = onAudioChunk || (() => {});
    this.stream = null;
    this.enabled = false;
    this.context = null;
    this.source = null;
    this.processor = null;
    this.trackEndedHandler = null;
    this.workletLoaded = false;
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
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not supported in this browser.");
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    let source;
    let processor;

    try {
      const context = this.ensureContext();
      await this.preparePlayback();
      if (!context.audioWorklet || typeof AudioWorkletNode === "undefined") {
        throw new Error("Microphone audio is not supported in this browser.");
      }
      if (!this.workletLoaded) {
        await context.audioWorklet.addModule(new URL("./pcm-capture-worklet.js", import.meta.url));
        this.workletLoaded = true;
      }

      source = context.createMediaStreamSource(stream);
      processor = new AudioWorkletNode(context, "screen-tutor-pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1]
      });
      processor.port.onmessage = (event) => {
        if (this.enabled) this.onAudioChunk(pcmBufferToBase64(event.data));
      };

      this.stream = stream;
      this.source = source;
      this.processor = processor;
      this.enabled = true;
      source.connect(processor);
      processor.connect(context.destination);

      const track = stream.getAudioTracks()[0];
      this.trackEndedHandler = () => this.disable();
      track?.addEventListener("ended", this.trackEndedHandler, { once: true });
      this.onStatusChange("on");
      return stream;
    } catch (error) {
      source?.disconnect();
      processor?.disconnect();
      if (processor) processor.port.onmessage = null;
      stream.getTracks().forEach((track) => track.stop());
      this.enabled = false;
      this.stream = null;
      this.source = null;
      this.processor = null;
      this.onStatusChange("off");
      throw error;
    }
  }

  disable() {
    const wasEnabled = this.enabled;
    this.enabled = false;

    const track = this.stream?.getAudioTracks()[0];
    if (track && this.trackEndedHandler) {
      track.removeEventListener("ended", this.trackEndedHandler);
    }
    this.trackEndedHandler = null;

    if (this.processor) {
      this.processor.port.onmessage = null;
      this.processor.disconnect();
    }
    this.source?.disconnect();
    this.stream?.getTracks().forEach((mediaTrack) => mediaTrack.stop());
    this.processor = null;
    this.source = null;
    this.stream = null;

    if (wasEnabled) this.onStatusChange("off");
  }

  toggle() {
    if (this.enabled) {
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
