const TARGET_SAMPLE_RATE = 16_000;
const CHUNK_SIZE = 320;

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.position = 0;
    this.pending = [];
  }

  process(inputs, outputs) {
    for (const channel of outputs[0] || []) channel.fill(0);

    const samples = inputs[0]?.[0];
    if (!samples?.length) return true;

    const step = sampleRate / TARGET_SAMPLE_RATE;
    while (this.position < samples.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const right = Math.min(left + 1, samples.length - 1);
      const value = samples[left] + (samples[right] - samples[left]) * fraction;
      const clipped = Math.max(-1, Math.min(1, value));
      this.pending.push(clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff);
      this.position += step;
    }
    this.position -= samples.length;

    while (this.pending.length >= CHUNK_SIZE) {
      const chunk = new Int16Array(CHUNK_SIZE);
      for (let index = 0; index < CHUNK_SIZE; index += 1) {
        chunk[index] = this.pending[index];
      }
      this.pending = this.pending.slice(CHUNK_SIZE);
      this.port.postMessage(chunk.buffer, [chunk.buffer]);
    }

    return true;
  }
}

registerProcessor("screen-tutor-pcm-capture", PcmCaptureProcessor);
