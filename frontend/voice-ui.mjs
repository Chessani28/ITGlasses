import {api, jsonOptions, sourcePath, elapsed} from "./core.mjs";
import {VoiceController, spokenAnswer} from "./voice-core.mjs";
import {captureMicrophone} from "./microphone.mjs";

export function initVoice(chat) {
  const $ = id => document.getElementById(id.replace(/^#+/, "")), audio = $("voice-audio");
  const recordedAudio = $("voice-playback-audio");
  let playback, audioUrl, recordingUrl, captureSettings = {};
  let transcriptConfirmation = null;
  const engineName = engine => engine === "whisper-small" ? "Whisper Small · experimental" : "Vosk";
  function updateEngineAvailability(data) {
    const engines = data.stt_engines || {};
    $("#voice-engine-vosk").disabled = !engines.vosk?.available;
    $("#voice-engine-whisper").disabled = !engines["whisper-small"]?.available;
    const reason = engines["whisper-small"]?.reason;
    $("#voice-engine-status").textContent = engines["whisper-small"]?.available
      ? "Whisper Small disponible · uso experimental local."
      : reason || "Whisper Small no está configurado; Vosk permanece disponible.";
  }
  function resolveTranscriptConfirmation(value) {
    if (!transcriptConfirmation) return;
    const {resolve, signal, abort} = transcriptConfirmation;
    transcriptConfirmation = null;
    $("#voice-transcript-confirmation").hidden = true;
    $("#voice-transcript-confirm").removeEventListener("click", confirm);
    $("#voice-transcript-discard").removeEventListener("click", discard);
    signal.removeEventListener("abort", abort);
    resolve(value);
  }
  function confirm() { resolveTranscriptConfirmation($("#voice-transcript-editor").value); }
  function discard() { resolveTranscriptConfirmation(null); }
  function confirmTranscript(transcript, signal) {
    $("#voice-transcript-editor").value = transcript.text;
    $("#voice-transcript-confirmation").hidden = false;
    $("#voice-transcript-editor").focus();
    return new Promise(resolve => {
      const abort = () => resolveTranscriptConfirmation(null);
      transcriptConfirmation = {resolve, signal, abort};
      $("#voice-transcript-confirm").addEventListener("click", confirm, {once: true});
      $("#voice-transcript-discard").addEventListener("click", discard, {once: true});
      signal.addEventListener("abort", abort, {once: true});
    });
  }
  function clearRecording() {
    recordedAudio.pause(); recordedAudio.removeAttribute("src"); recordedAudio.load();
    if (recordingUrl) URL.revokeObjectURL(recordingUrl);
    recordingUrl = null;
  }
  function stopAudio() {
    recordedAudio.pause();
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
    capture: (emit, enabled, activity, failed) => captureMicrophone(emit, enabled, captureSettings.threshold ?? Number($("voice-threshold").value), activity, failed, $("voice-diagnostic-toggle").checked, captureSettings),
    stopAudio,
    state(phase, detail, active) {
      const labels = {idle: "En espera", listening: "Escuchando", transcribing: "Transcribiendo", thinking: "Pensando", speaking: "Hablando", error: "Error"};
      $("voice-state").textContent = labels[phase]; $("voice-state").dataset.state = phase;
      $("voice-detail").textContent = detail;
      $("voice-mic").textContent = active ? "â— MICRÃ“FONO ACTIVO" : "â—‹ MICRÃ“FONO APAGADO";
      $("voice-playback-btn").disabled = active || !recordingUrl;
      $("voice-start").disabled = active; $("voice-stop").disabled = !active;
      $("voice-preferences").disabled = active; $("voice-diagnostic-toggle").disabled = active;
      $("voice-engine").disabled = active;
      chat.lock(active);
    },
    transcript(text, ms, engine) {
      $("voice-transcript").textContent = text || "No se reconoció voz.";
      $("#voice-engine-active").textContent = engineName(engine);
      $("voice-timing").textContent = `Transcripción: ${elapsed(ms)}`;
    },
    async transcribe(blob, signal, engine) {
      if (!["vosk", "whisper-small"].includes(engine)) {
        throw new Error("Selecciona un motor de reconocimiento de voz válido.");
      }
      const resp = await api("/api/v1/voice/transcribe", {method: "POST",
        headers: {"Content-Type": "audio/wav", "X-ITGlasses-STT-Engine": engine},
        body: blob, signal}, 95000);
      const data = resp.data;
      if (data.engine !== engine) {
        throw new Error(`El backend devolvió ${engineName(data.engine)} en lugar de ${engineName(engine)}. No se usará esa transcripción.`);
      }
      const {recordingDurationMs, stopReason, speechDurationMs, energyRms} = blob.voiceDiagnostics || {};
      const {raw_text, normalized_text, quality_reasons: clarificationReasons} = data;
      const confidence = null; // No invented aggregate confidence score.
      $("#voice-recording-status").textContent = "Grabacion terminada";
      $("#voice-recording-duration").textContent = recordingDurationMs ? `${(recordingDurationMs/1000).toFixed(2)} s` : "â€”";
      $("#voice-record-stop-reason").textContent = stopReason || "(sin resultado)";
      $("#voice-speech-duration").textContent = speechDurationMs ? `${(speechDurationMs/1000).toFixed(2)} s` : "â€”";
      $("#voice-audio-energy").textContent = energyRms != null ? energyRms.toFixed(6) : "â€”";
      $("#voice-transcript-raw").textContent = raw_text || "â€”";
      $("#voice-transcript-normalized").textContent = normalized_text || "â€”";
      $("#voice-engine-active").textContent = engineName(data.engine);
      $("#voice-confidence").textContent = confidence != null ? `${confidence.toFixed(2)}` : "â€”";
      $("#voice-clarification-reasons").textContent = clarificationReasons && clarificationReasons.length ? clarificationReasons.join(", ") : "â€”";
      $("voice-timing").textContent = `Transcripción · ${engineName(data.engine)}: ${elapsed(data.elapsed_ms)}`;
      clearRecording();
      recordingUrl = URL.createObjectURL(blob); recordedAudio.src = recordingUrl; recordedAudio.load();
      $("voice-playback-btn").disabled = controller.active;
      return data;
    },
    confirmTranscript,
    chat: (text, signal) => chat.send(text, signal), speak
  });
  $("voice-playback-btn").addEventListener("click", () => { if (!controller.active && recordingUrl) { recordedAudio.currentTime = 0; return recordedAudio.play().catch(() => {}); } });
  $("voice-start").addEventListener("click", async () => {
    $("voice-start").disabled = true;
    try {
      const {data} = await api("/api/v1/voice/status", {}, 10000);
      updateEngineAvailability(data);
      if (!data.stt_available || ($("voice-speak").checked && !data.tts_available)) throw new Error("Faltan dependencias o modelos locales de voz. Consulta docs/voice-assistant.md.");
      const engine = $("voice-engine").value;
      if (!data.stt_engines?.[engine]?.available) {
        throw new Error(data.stt_engines?.[engine]?.reason || "El motor de voz seleccionado no está disponible.");
      }
      await controller.start({wake: $("voice-wake").checked, speak: $("voice-speak").checked,
        engine,
        aliases: $("voice-aliases").value.split(","), windowSeconds: Number($("voice-window").value),
        speaker: Number($("voice-speaker").value), speed: Number($("voice-speed").value)});
    } catch (error) { controller.fail(error.message); }
    finally { $("voice-start").disabled = controller.active; }
  });
  $("voice-stop").addEventListener("click", () => controller.stop());
  api("/api/v1/voice/status", {}, 10000).then(({data}) => updateEngineAvailability(data)).catch(() => {
    $("#voice-engine-status").textContent = "No se pudo consultar la disponibilidad de los motores locales.";
  });
  $("voice-diagnostic-toggle").addEventListener("change", async (event) => {
    $("voice-diagnostic-section").hidden = !event.target.checked;
    if (event.target.checked) {
      const {data} = await api("/api/v1/voice/diagnostic/settings", {}, 10000);
      $("#voice-diag-threshold").value = data.energy_threshold;
      $("#voice-diag-silent-timeout").value = data.silence_timeout;
      $("#voice-diag-prespeak-buffer").value = data.pre_speech_buffer;
      $("#voice-diag-max-duration").value = data.max_duration_ms / 1000;
      $("#voice-diag-settings-form").hidden = false;
      $("#voice-diag-toggle-row").classList.replace("open", "collapsed");
    } else {
      captureSettings = {};
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
    captureSettings = {threshold: data.energy_threshold, maxDurationMs: data.max_duration_ms,
      silenceTimeoutMs: data.silence_timeout * 1000, preSpeechMs: data.pre_speech_buffer * 1000};
    $("voice-diag-feedback").textContent = "Ajustes guardados Â· reserva de desarrollo";
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) controller.stop(); });
  window.addEventListener("pagehide", () => { controller.stop(); clearRecording(); });
  return controller;
}
