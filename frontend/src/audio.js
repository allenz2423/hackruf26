export class AudioController {
  constructor({ onStatusChange } = {}) {
    this.onStatusChange = onStatusChange || (() => {});
    this.stream = null;
    this.enabled = false;
  }

  async enable() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not supported in this browser.");
    }

    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    this.enabled = true;
    this.onStatusChange("on");
    return this.stream;
  }

  disable() {
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
    }

    this.stream = null;
    this.enabled = false;
    this.onStatusChange("off");
  }

  toggle() {
    if (this.enabled) {
      this.disable();
      return Promise.resolve(null);
    }

    return this.enable();
  }
}
