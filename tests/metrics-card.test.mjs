import test from "node:test";
import assert from "node:assert/strict";
import {metricsLines} from "../frontend/core.mjs";

const summary = {
  stages: {
    stt: {count: 12, errors: 3, median_ms: 640, p90_ms: 1200},
    chat: {count: 10, errors: 0, median_ms: 1800, p90_ms: 4100},
    tts: {count: 0, errors: 0, median_ms: null, p90_ms: null},
  },
  stt_engines: {"whisper-small": 9, vosk: 3},
  whisper_failures: {voice_resource_limit: 3},
  recent_errors: [{stage: "stt", outcome: "voice_resource_limit", detail: "whisper-small", seconds_ago: 75}],
  ram_available_mb: 4200,
};

test("each stage reads as count, median and p90 in Spanish", () => {
  const lines = metricsLines(summary);
  assert.ok(lines.includes("Voz a texto: 12 · mediana 0.6 s · p90 1.2 s · 3 errores"));
  assert.ok(lines.includes("Asistente: 10 · mediana 1.8 s · p90 4.1 s"));
  assert.ok(lines.includes("Texto a voz: sin uso en la última hora"));
});

test("the Vosk fallback and its reason are visible", () => {
  const lines = metricsLines(summary);
  assert.ok(lines.includes("Motor de voz: Whisper 9 · Vosk 3 (25%)"));
  assert.ok(lines.includes("Whisper no disponible: 3 × falta de RAM"));
});

test("free RAM and the latest errors are shown", () => {
  const lines = metricsLines(summary);
  assert.ok(lines.includes("RAM libre: 4.1 GB"));
  assert.ok(lines.includes("Último error: voz a texto · voice_resource_limit · hace 1 min"));
});

test("a quiet hour says so", () => {
  const quiet = {stages: {stt: {count: 0}, chat: {count: 0}, tts: {count: 0}}, stt_engines: {},
    whisper_failures: {}, recent_errors: [], ram_available_mb: null};
  assert.deepEqual(metricsLines(quiet), ["Voz a texto: sin uso en la última hora", "Asistente: sin uso en la última hora",
    "Texto a voz: sin uso en la última hora"]);
});
