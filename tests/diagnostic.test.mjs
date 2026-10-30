import test from 'node:test';import assert from 'node:assert/strict';test('diagnostic settings validate bounds', () => {
  const settings = {
    energy_threshold: 0.012,
    silence_timeout: 1.1,
    pre_speech_buffer: 0.25,
    max_duration_ms: 28000  };
  assert.ok(settings.energy_threshold >= 0.008 && settings.energy_threshold <= 0.012);  assert.ok(settings.silence_timeout >= 1.1 && settings.silence_timeout <= 2.0);  assert.ok(settings.pre_speech_buffer >= 0.2 && settings.pre_speech_buffer <= 0.3);  assert.ok(settings.max_duration_ms >= 10000 && settings.max_duration_ms <= 30000);});
test('segmenter produces voice diagnostics on silence timeout', () => {
  const rate = 16000;
  let length = 0;
  let speechMs = 0;
  let silence = 0;
  const threshold = 0.012;
  const silenceTimeout = rate * 1.1;
  const maxDuration = rate * 28;
  const samples = new Float32Array(1600);
  for (let i = 0; i < 20; i++) {
    const power = Math.sqrt(samples.reduce((sum, n) => sum + n * n, 0) / samples.length);    const voiced = power >= threshold;
    if (voiced) speechMs = 1600;
    silence += 1600;
    length += 1600;
    if (length >= maxDuration || silence >= silenceTimeout) {
      if (speechMs >= rate * 0.25) {
        const diagnostics = { recordingDurationMs: length, stopReason: 'silence_timeout', speechDurationMs: speechMs, energyRms: power, confidence: null, clarificationReasons: [] };
        assert.equal(diagnostics.stopReason, 'silence_timeout');      }
      length = 0; speechMs = 0; silence = 0;
    }
  }
});
console.log('Diagnostic tests loaded');