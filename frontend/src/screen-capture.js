const MAX_FRAME_DIMENSION = 1280;

export class ScreenCaptureController {
  constructor({ video, onEnded, onFrame } = {}) {
    this.video = video;
    this.onEnded = onEnded || (() => {});
    this.onFrame = onFrame || (() => {});
    this.stream = null;
    this.frames = new Map();
    this.frameTimer = null;
    this.requestId = 0;
    this.trackEndedHandler = null;
    this.canvas = document.createElement("canvas");
    this.context = this.canvas.getContext("2d");
  }

  async start() {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      throw new Error("Screen sharing is not supported in this browser.");
    }

    const requestId = ++this.requestId;
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    if (requestId !== this.requestId) {
      stream.getTracks().forEach((track) => track.stop());
      return null;
    }
    this.attach(stream);
    return stream;
  }

  attach(stream) {
    this.stop();
    this.stream = stream;
    this.video.srcObject = stream;
    this.video.play().catch(() => {});

    const [track] = stream.getVideoTracks();
    if (track) {
      this.trackEndedHandler = () => {
        this.stop();
        this.onEnded();
      };
      track.addEventListener("ended", this.trackEndedHandler, { once: true });
    }

    this.frameTimer = window.setInterval(() => this.sampleFrame(), 1000);
  }

  sampleFrame() {
    if (!this.stream || !this.video.videoWidth || !this.video.videoHeight || !this.context) return;

    const sourceWidth = this.video.videoWidth;
    const sourceHeight = this.video.videoHeight;
    const scale = Math.min(1, MAX_FRAME_DIMENSION / Math.max(sourceWidth, sourceHeight));
    this.canvas.width = Math.max(1, Math.round(sourceWidth * scale));
    this.canvas.height = Math.max(1, Math.round(sourceHeight * scale));
    this.context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);

    const frame = {
      frame_id: crypto.randomUUID(),
      width: this.canvas.width,
      height: this.canvas.height,
      captured_at: new Date().toISOString(),
      jpeg: this.canvas.toDataURL("image/jpeg", 0.72)
    };

    this.frames.set(frame.frame_id, {
      width: frame.width,
      height: frame.height,
      sourceWidth,
      sourceHeight,
      capturedAt: frame.captured_at
    });

    while (this.frames.size > 8) {
      this.frames.delete(this.frames.keys().next().value);
    }

    this.onFrame(frame);
  }

  getFrames() {
    return this.frames;
  }

  clearFrames() {
    this.frames.clear();
    this.canvas.width = 0;
    this.canvas.height = 0;
  }

  stop() {
    this.requestId += 1;
    if (this.frameTimer) {
      window.clearInterval(this.frameTimer);
      this.frameTimer = null;
    }

    if (this.stream) {
      const track = this.stream.getVideoTracks()[0];
      if (track && this.trackEndedHandler) track.removeEventListener("ended", this.trackEndedHandler);
      this.stream.getTracks().forEach((track) => track.stop());
    }

    this.trackEndedHandler = null;
    this.stream = null;
    // Releasing the canvas removes the final screen image from memory at stop.
    this.clearFrames();
    this.video.srcObject = null;
  }
}
