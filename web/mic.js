// Turns microphone samples into 16-bit PCM chunks. The AudioContext runs at 24 kHz.
const CHUNK_SAMPLES = 2400;  // 100 ms

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(CHUNK_SAMPLES);
    this.filled = 0;
  }

  process(inputs) {
    const samples = inputs[0][0];
    if (!samples) return true;
    for (const sample of samples) {
      this.buffer[this.filled++] = Math.max(-1, Math.min(1, sample)) * 32767;
      if (this.filled === CHUNK_SAMPLES) {
        this.port.postMessage(this.buffer.slice().buffer);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic", MicProcessor);
