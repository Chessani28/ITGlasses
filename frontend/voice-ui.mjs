import {api, jsonOptions, sourcePath, elapsed} from "./core.mjs";
import {VoiceController, spokenAnswer} from "./voice-core.mjs";
import {captureMicrophone} from "./microphone.mjs";

export function initVoice(chat) {
  const $ = id => document.getElementById(id), audio = $("voice-audio");
  let playback, audioUrl;
  function stopAudio() {
    audio.pause(); audio.removeAttribute("src"); audio.load();
    if (audioUrl) URL.revokeObjectURL(audioUrl); audioUrl = null;
    playback?.(); playback = null;
  }
  async function speak(answer, preferences, signal) {
    const started = performance.now();
    const citationIds = (answer.sources || []).map(source => source.citation_id);
    const response = await fetch("/api/v1/voice/synthesize", {...jsonOptions({text: spokenAnswer(answer),
      speaker: preferences.speaker, speed: preferences.speed, citation_ids: citationIds}), signal, cache: "no-store"});
    if (!response.ok) { const data = await response.json(); throw new Error(data.error?.message || "No se pudo generar la voz."); }
    const blob = await response.blob();
    // Recheck immediately before playback. Cached audio is never reused for replay.
    for (const id of citationIds) await api(sourcePath(id), {signal}, 15000);
    if (signal.aborted) return;
    stopAudio(); audioUrl = URL.createObjectURL(blob); audio.src = audioUrl;
    $("voice-timing").textContent = `Síntesis y verificación: ${elapsed(performance.now() - started)}`;
    await new Promise((resolve, reject) => {
      const done = () => { audio.onended = null; audio.onerror = null; signal.removeEventListener("abort", cancel); playback = null; resolve(); };
      const cancel = () => { audio.pause(); done(); };
      playback = done; audio.onended = done;
      audio.onerror = () => { signal.removeEventListener("abort", cancel); audio.onended = null; playback = null; reject(new Error("No se pudo reproducir el audio local.")); };
      signal.addEventListener("abort", cancel, {once: true});
      audio.play().catch(() => { $("voice-detail").textContent = "El navegador bloqueó la reproducción automática. Pulsa reproducir en el audio o Detener."; });
    });
    stopAudio();
  }
  const controller = new VoiceController({
    capture: (emit, enabled, activity, failed) => captureMicrophone(emit, enabled, Number($("voice-threshold").value), activity, failed),
    stopAudio,
    state(phase, detail, active) {
      const labels = {idle: "En espera", listening: "Escuchando", transcribing: "Transcribiendo", thinking: "Pensando", speaking: "Hablando", error: "Error"};
      $("voice-state").textContent = labels[phase]; $("voice-state").dataset.state = phase;
      $("voice-detail").textContent = detail;
      $("voice-mic").textContent = active ? "● MICRÓFONO ACTIVO" : "○ MICRÓFONO APAGADO";
      $("voice-start").disabled = active; $("voice-stop").disabled = !active;
      $("voice-preferences").disabled = active;
      chat.lock(active);
    },
    transcript(text, ms) { $("voice-transcript").textContent = text || "No se reconoció voz."; $("voice-timing").textContent = `Transcripción: ${elapsed(ms)}`; },
    transcribe: async (blob, signal) => (await api("/api/v1/voice/transcribe", {method: "POST", headers: {"Content-Type": "audio/wav"}, body: blob, signal}, 95000)).data,
    chat: (text, signal) => chat.send(text, signal), speak
  });
  $("voice-start").addEventListener("click", async () => {
    $("voice-start").disabled = true;
    try {
      const {data} = await api("/api/v1/voice/status", {}, 10000);
      if (!data.stt_available || ($("voice-speak").checked && !data.tts_available)) throw new Error("Faltan dependencias o modelos locales de voz. Consulta docs/voice-assistant.md.");
      await controller.start({wake: $("voice-wake").checked, speak: $("voice-speak").checked,
        aliases: $("voice-aliases").value.split(","), windowSeconds: Number($("voice-window").value),
        speaker: Number($("voice-speaker").value), speed: Number($("voice-speed").value)});
    } catch (error) { controller.fail(error.message); }
    finally { $("voice-start").disabled = controller.active; }
  });
  $("voice-stop").addEventListener("click", () => controller.stop());
  document.addEventListener("visibilitychange", () => { if (document.hidden) controller.stop(); });
  window.addEventListener("pagehide", () => controller.stop());
  return controller;
}
