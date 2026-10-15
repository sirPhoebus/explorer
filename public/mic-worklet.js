class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = new Float32Array(0);
    this.targetRate = 24000;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    const merged = new Float32Array(this.pending.length + channel.length);
    merged.set(this.pending);
    merged.set(channel, this.pending.length);
    const ratio = sampleRate / this.targetRate;
    const block = Math.floor(sampleRate * 0.1);
    if (merged.length < block) {
      this.pending = merged;
      return true;
    }
    const count = Math.floor(merged.length / ratio);
    if (count < 1) {
      this.pending = merged;
      return true;
    }
    const consumed = Math.floor(count * ratio);
    const samples = new Float32Array(count);
    let energy = 0;
    for (let index = 0; index < count; index += 1) {
      const position = index * ratio;
      const left = Math.floor(position);
      const right = Math.min(left + 1, consumed - 1);
      const mix = position - left;
      const sample = merged[left] * (1 - mix) + merged[right] * mix;
      samples[index] = sample;
      energy += sample * sample;
    }
    this.pending = merged.slice(consumed);
    const pcm = new Int16Array(count);
    for (let index = 0; index < count; index += 1) {
      const sample = Math.max(-1, Math.min(1, samples[index]));
      pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    const bytes = new Uint8Array(pcm.byteLength);
    bytes.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength));
    this.port.postMessage({ pcm: bytes.buffer, rms: Math.sqrt(energy / count) }, [bytes.buffer]);
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
