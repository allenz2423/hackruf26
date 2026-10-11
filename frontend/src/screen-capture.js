const MAX_FRAME_DIMENSION = 1920;
const MAX_FRAME_BYTES = 2_200_000;
const MAX_COMPARISON_DIMENSION = 960;
const JPEG_QUALITIES = [0.85, 0.75, 0.65];

function jpegByteLength(dataUrl) {
  const data = dataUrl.slice(dataUrl.indexOf(",") + 1);
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return data.length / 4 * 3 - padding;
}

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
    this.context = this.canvas.getContext("2d", { alpha: false });
    this.comparisonCanvas = document.createElement("canvas");
    this.comparisonContext = this.comparisonCanvas.getContext("2d", { alpha: false, willReadFrequently: true });
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
    let maximumDimension = MAX_FRAME_DIMENSION;
    let jpeg = null;
    while (!jpeg) {
      const scale = Math.min(1, maximumDimension / Math.max(sourceWidth, sourceHeight));
      this.canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      this.canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      this.context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
      for (const quality of JPEG_QUALITIES) {
        const candidate = this.canvas.toDataURL("image/jpeg", quality);
        if (candidate.startsWith("data:image/jpeg;base64,") && jpegByteLength(candidate) <= MAX_FRAME_BYTES) {
          jpeg = candidate;
          break;
        }
      }
      if (maximumDimension <= 320 && !jpeg) return;
      maximumDimension = Math.max(320, Math.floor(maximumDimension * 0.75));
    }

    // Derive the reference from the exact canvas encoded above, not a newer
    // video frame that may have arrived while JPEG encoding was in progress.
    const content = this.readContent(this.canvas, sourceWidth, sourceHeight);
    if (!content) return;

    const frame = {
      frame_id: crypto.randomUUID(),
      width: this.canvas.width,
      height: this.canvas.height,
      captured_at: new Date().toISOString(),
      jpeg
    };

    this.frames.set(frame.frame_id, {
      width: frame.width,
      height: frame.height,
      sourceWidth,
      sourceHeight,
      capturedAt: frame.captured_at,
      signature: content.signature
    });

    while (this.frames.size > 8) {
      this.frames.delete(this.frames.keys().next().value);
    }

    this.onFrame(frame);
  }

  getFrames() {
    return this.frames;
  }

  getCurrentContent() {
    if (!this.stream || this.video.readyState < 2 || !this.video.videoWidth || !this.video.videoHeight || !this.canvas.width || !this.context) return null;
    // Match the reference's two-stage resampling, including capture downscale.
    // This avoids mistaking different text antialiasing for an edit on 4K sources.
    try {
      this.context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
      return this.readContent(this.canvas, this.video.videoWidth, this.video.videoHeight);
    } catch {
      return null;
    }
  }

  readContent(source, sourceWidth, sourceHeight) {
    if (!this.comparisonContext) return null;
    const scale = Math.min(1, MAX_COMPARISON_DIMENSION / Math.max(sourceWidth, sourceHeight));
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    this.comparisonCanvas.width = width;
    this.comparisonCanvas.height = height;
    try {
      this.comparisonContext.drawImage(source, 0, 0, width, height);
      const { data } = this.comparisonContext.getImageData(0, 0, width, height);
      const pixels = new Uint8Array(width * height);
      for (let i = 0; i < pixels.length; i += 1) {
        const offset = i * 4;
        pixels[i] = (data[offset] * 77 + data[offset + 1] * 150 + data[offset + 2] * 29) >> 8;
      }
      return { sourceWidth, sourceHeight, signature: { width, height, pixels } };
    } catch {
      // A stopped or not-yet-decoded source cannot validate old geometry.
      return null;
    }
  }

  clearFrames() {
    this.frames.clear();
    this.canvas.width = 0;
    this.canvas.height = 0;
    this.comparisonCanvas.width = 0;
    this.comparisonCanvas.height = 0;
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
