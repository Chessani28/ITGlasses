import {api, jsonOptions, sourcePath, elapsed} from "./core.mjs";
import {VoiceController, spokenAnswer} from "./voice-core.mjs";
import {captureMicrophone} from "./microphone.mjs";

export function initVoice(chat) {
  const $ = id => document.getElementById(id.replace(/^#+/, "")), audio = $("voice-audio");
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
    $("voice-timing").textContent = `SÃ­ntesis y verificaciÃ³n: ${elapsed(performance.now() - started)}`;
    await new Promise((resolve, reject) => {
      const done = () => { audio.onended = null; audio.onerror = null; signal.removeEventListener("abort", cancel); playback = null; resolve(); };
      const cancel = () => { audio.pause(); done(); };
      playback = done; audio.onended = done;
      audio.onerror = () => { signal.removeEventListener("abort", cancel); audio.onended = null; playback = null; reject(new Error("No se pudo reproducir el audio local.")); };
      signal.addEventListener("abort", cancel, {once: true});
      audio.play().catch(() => { $("voice-detail").textContent = "El navegador bloqueÃ³ la reproducciÃ³n automÃ¡tica. Pulsa reproducir en el audio o Detener."; });
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
      $("voice-mic").textContent = active ? "â— MICRÃ“FONO ACTIVO" : "â—‹ MICRÃ“FONO APAGADO";
      $("voice-start").disabled = active; $("voice-stop").disabled = !active;
      $("voice-preferences").disabled = active; $("voice-diagnostic-toggle").disabled = active;
      chat.lock(active);
    },
    transcript(text, ms) { $("voice-transcript").textContent = text || "No se reconociÃ³ voz."; $("voice-timing").textContent = `TranscripciÃ³n: ${elapsed(ms)}`; },
    async transcribe(blob, signal) {
      const settings = {maxDurationMs: 28000, silenceTimeoutMs: 1100};
      const resp = await api("/api/v1/voice/transcribe", {method: "POST", headers: {"Content-Type": "audio/wav"}, body: blob, signal}, 95000);
      const data = resp.data;
      const {recordingDurationMs, stopReason, speechDurationMs, energyRms, confidence, clarificationReasons, raw_text, normalized_text} = data;
      $("#voice-recording-status").textContent = "Grabacion terminada";
      $("#voice-recording-duration").textContent = recordingDurationMs ? `${(recordingDurationMs/1000).toFixed(2)} s` : "â€”";
      $("#voice-record-stop-reason").textContent = stopReason || "(sin resultado)";
      $("#voice-speech-duration").textContent = speechDurationMs ? `${(speechDurationMs/1000).toFixed(2)} s` : "â€”";
      $("#voice-audio-energy").textContent = energyRms != null ? energyRms.toFixed(6) : "â€”";
      $("#voice-transcript-raw").textContent = raw_text || "â€”";
      $("#voice-transcript-normalized").textContent = normalized_text || "â€”";
      $("#voice-confidence").textContent = confidence != null ? `${confidence.toFixed(2)}` : "â€”";
      $("#voice-clarification-reasons").textContent = clarificationReasons && clarificationReasons.length ? clarificationReasons.join(", ") : "â€”";
      $("#voice-playback-btn").disabled = false;
      const url = URL.createObjectURL(blob);
      audio.src = url; audio.load();
      stopAudio(); playback = () => audio.play().catch(() => { $("#voice-detail").textContent = "El navegador bloqueo la reproduccion."; }); audio.onended = playback;
      return data;
    },
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
  $("voice-diagnostic-toggle").addEventListener("change", async (event) => {
    if (event.target.checked) {
      const {data} = await api("/api/v1/voice/diagnostic/settings", {}, 10000);
      $("#voice-diag-threshold").value = data.energy_threshold;
      $("#voice-diag-silent-timeout").value = data.silence_timeout;
      $("#voice-diag-prespeak-buffer").value = data.pre_speech_buffer;
      $("#voice-diag-max-duration").value = data.max_duration_ms / 1000;
      $("#voice-diag-settings-form").hidden = false;
      $("#voice-diag-toggle-row").classList.replace("open", "collapsed");
    } else {
      $("#voice-diag-settings-form").hidden = true;
      $("#voice-diag-toggle-row").classList.replace("collapsed", "open");
    }
  });
  $("#voice-diag-save").addEventListener("click", async () => {
    const payload = {
      energy_threshold: Number($("voice-diag-threshold").value),
      silence_timeout: Number($("voice-diag-silent-timeout").value),
      pre_speech_buffer: Number($("voice-diag-prespeak-buffer").value),
      max_duration_ms: Number($("voice-diag-max-duration").value) * 1000
    };
    const {data} = await api("/api/v1/voice/diagnostic/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}, 10000);
    $("voice-diag-feedback").textContent = "Ajustes guardados Â· reserva de desarrollo";
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) controller.stop(); });
  window.addEventListener("pagehide", () => controller.stop());
  return controller;
}

