/** Browser PCM capture with energy/silence segmentation. No cloud speech APIs. */
export function pcmWav(chunks, sampleRate) {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const samples = new Float32Array(length); let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
  const count = Math.floor(length * 16000 / sampleRate);
  const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const text = (index, value) => [...value].forEach((char, i) => view.setUint8(index + i, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + count * 2, true); text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    // Area averaging for downsampling; speech-only prototype, not studio resampling.
    const from = Math.floor(i * sampleRate / 16000), to = Math.max(from + 1, Math.floor((i + 1) * sampleRate / 16000));
    let sum = 0; for (let j = from; j < to; j++) sum += samples[j] || 0;
    const value = Math.max(-1, Math.min(1, sum / (to - from)));
    view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  return new Blob([buffer], {type: "audio/wav"});
}
export class Segmenter {
  constructor(rate, emit, threshold = 0.012, activity = () => {}) { this.rate = rate; this.emit = emit; this.threshold = threshold; this.activity = activity; this.reset(); }
  reset() { this.chunks = []; this.pre = []; this.length = 0; this.silence = 0; this.voiced = 0; }
  push(samples) {
    const power = Math.sqrt(samples.reduce((sum, n) => sum + n * n, 0) / samples.length);
    const voiced = power >= this.threshold;
    if (voiced) this.activity();
    if (!this.chunks.length && !voiced) {
      this.pre.push(samples); while (this.pre.reduce((s, x) => s + x.length, 0) > this.rate * 0.2) this.pre.shift(); return;
    }
    if (!this.chunks.length) { this.chunks = this.pre; this.length = this.pre.reduce((s, x) => s + x.length, 0); this.pre = []; }
    this.chunks.push(samples); this.length += samples.length;
    this.voiced += voiced ? samples.length : 0; this.silence = voiced ? 0 : this.silence + samples.length;
    if (this.length >= this.rate * 28 || this.silence >= this.rate * 1.1) {
      if (this.voiced >= this.rate * 0.25) this.emit(pcmWav(this.chunks, this.rate));
      this.reset();
    }
  }
}
export async function captureMicrophone(emit, enabled, threshold, activity, failed) {
  if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioWorkletNode) throw new Error("Este navegador no admite captura local. Usa Chrome o Edge en localhost.");
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
  let context, source, processor;
  const stop = () => { stream.getTracks().forEach(track => track.stop()); source?.disconnect(); processor?.disconnect(); if (context?.state !== "closed") context?.close().catch(() => {}); };
  try {
    context = new AudioContext(); await context.audioWorklet.addModule("/ui/voice-worklet.js");
    source = context.createMediaStreamSource(stream);
    processor = new AudioWorkletNode(context, "voice-capture");
    const segmenter = new Segmenter(context.sampleRate, emit, threshold, activity);
    processor.port.onmessage = event => { if (enabled()) segmenter.push(event.data); else segmenter.reset(); };
    source.connect(processor); processor.connect(context.destination); await context.resume();
    stream.getAudioTracks()[0].addEventListener("ended", () => { stop(); failed?.("El micrófono se desconectó o perdió el permiso."); });
    return {stop};
  } catch (error) { stop(); throw error; }
}
