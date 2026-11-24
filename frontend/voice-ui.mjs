import {api, jsonOptions, sourcePath, elapsed} from "./core.mjs";
import {VoiceController, spokenAnswer} from "./voice-core.mjs";
import {sessionHeaders} from "./session.mjs";
import {captureMicrophone} from "./microphone.mjs";

export const FALLBACK_NOTICE = "Whisper no está disponible; usando Vosk, la transcripción puede ser menos precisa.";
/** "Whisper · large-v3-turbo · GPU", "Vosk · CPU": what transcribed (the response says model and device). */
export function engineName(engine, info = {}) {
  if (engine === "whisper-small") {
    const device = info.device === "cuda" ? "GPU" : info.device === "cpu" ? "CPU" : "";
    return ["Whisper", info.whisper_model || info.model, device].filter(Boolean).join(" · ");
  }
  return engine === "vosk" ? "Vosk · CPU" : String(engine || "—");
}
/** The badge of the voice panel: the engine that the next turn will use. */
export function badgeText(selected, engines = {}) {
  const whisper = engines["whisper-small"];
  if (selected === "whisper-small" && whisper?.available) return `LOCAL · WHISPER ${whisper.device === "cuda" ? "GPU" : "CPU"}`;
  if (engines.vosk?.available || selected === "vosk") return "LOCAL · VOSK CPU";
  return "LOCAL";
}
/** Under the engine choice: what is configured, and what happens when Whisper cannot run. */
export function engineStatus(engines = {}) {
  const whisper = engines["whisper-small"];
  if (!whisper?.available) {
    return `Whisper no disponible${whisper?.reason ? `: ${whisper.reason}` : ""}. Se usa Vosk (básico).`;
  }
  const where = whisper.device === "cuda" ? "en la GPU" : "en la CPU";
  const backup = whisper.fallback_available ? `, con respaldo ${whisper.fallback_model || "Small"} en la CPU` : "";
  return `Whisper ${whisper.model || ""} ${where}${backup}. Si no puede usarse en un turno, se usa Vosk y se avisa.`
    .replace(/\s+/g, " ");
}

export function initVoice(chat) {
  const $ = id => document.getElementById(id.replace(/^#+/, "")), audio = $("voice-audio");
  const recordedAudio = $("voice-playback-audio");
  let playback, audioUrl, recordingUrl, captureSettings = {};
  let transcriptConfirmation = null;
  let engines = {}, picked = false, lastInfo = {};
  function showNotice(text) {
    $("voice-engine-notice").textContent = text || "";
    $("voice-engine-notice").hidden = !text;
  }
  function updateBadge() {
    $("voice-badge").textContent = badgeText($("voice-engine").value, engines);
  }
  function updateEngineAvailability(data) {
    engines = data.stt_engines || {};
    const whisper = engines["whisper-small"];
    $("#voice-engine-vosk").disabled = !engines.vosk?.available;
    $("#voice-engine-whisper").disabled = !whisper?.available;
    // Whisper is the recommended engine when it is there; the person's own choice is kept once made.
    if (!picked) {
      if (whisper?.available) $("voice-engine").value = "whisper-small";
      else if (engines.vosk?.available) $("voice-engine").value = "vosk";
    }
    $("#voice-engine-status").textContent = engineStatus(engines);
    updateBadge();
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
    const request = jsonOptions({text: spokenAnswer(answer), speaker: preferences.speaker, speed: preferences.speed,
      citation_ids: citationIds});
    const response = await fetch("/api/v1/voice/synthesize", {...request, headers: {...request.headers,
      ...sessionHeaders("POST")}, signal, cache: "no-store", credentials: "same-origin"});
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
    capture: (emit, enabled, activity, failed) => captureMicrophone(emit, enabled, captureSettings.threshold ?? Number($("voice-threshold").value), activity, failed, $("voice-diagnostic-toggle").checked, captureSettings),
    stopAudio,
    state(phase, detail, active) {
      const labels = {idle: "En espera", listening: "Escuchando", transcribing: "Transcribiendo", thinking: "Pensando", speaking: "Hablando", error: "Error"};
      $("voice-state").textContent = labels[phase]; $("voice-state").dataset.state = phase;
      $("voice-detail").textContent = detail;
      $("voice-mic").textContent = active ? "● MICRÓFONO ACTIVO" : "○ MICRÓFONO APAGADO";
      $("voice-playback-btn").disabled = active || !recordingUrl;
      $("voice-start").disabled = active; $("voice-stop").disabled = !active;
      $("voice-preferences").disabled = active; $("voice-diagnostic-toggle").disabled = active;
      $("voice-engine").disabled = active;
      chat.lock(active);
    },
    transcript(text, ms, engine) {
      $("voice-transcript").textContent = text || "No se reconoció voz.";
      $("#voice-engine-active").textContent = engineName(engine, lastInfo);
      $("voice-timing").textContent = `Transcripción: ${elapsed(ms)}`;
    },
    async transcribe(blob, signal, engine) {
      if (!["vosk", "whisper-small"].includes(engine)) {
        throw new Error("Selecciona un motor de reconocimiento de voz válido.");
      }
      const ask = async name => (await api("/api/v1/voice/transcribe", {method: "POST",
        headers: {"Content-Type": "audio/wav", "X-ITGlasses-STT-Engine": name},
        body: blob, signal}, 95000)).data;
      let requested = engine, data;
      try { data = await ask(engine); }
      catch (error) {
        // Like the app: Whisper not available right now (503: GPU memory, GPU, not configured) -> one retry with
        // Vosk, and the backend says why in "notice". Any other failure is reported as it is.
        if (engine !== "whisper-small" || error.status !== 503 || signal.aborted) throw error;
        requested = "vosk";
        data = await ask("vosk");
      }
      if (data.engine !== requested) {
        throw new Error(`El backend devolvió ${engineName(data.engine)} en lugar de ${engineName(requested)}. No se usará esa transcripción.`);
      }
      lastInfo = data;
      showNotice(requested !== engine ? (data.notice || FALLBACK_NOTICE) : (data.notice || ""));
      const {recordingDurationMs, stopReason, speechDurationMs, energyRms} = blob.voiceDiagnostics || {};
      const {raw_text, normalized_text, quality_reasons: clarificationReasons} = data;
      const confidence = null; // No invented aggregate confidence score.
      $("#voice-recording-status").textContent = "Grabacion terminada";
      $("#voice-recording-duration").textContent = recordingDurationMs ? `${(recordingDurationMs/1000).toFixed(2)} s` : "—";
      $("#voice-record-stop-reason").textContent = stopReason || "(sin resultado)";
      $("#voice-speech-duration").textContent = speechDurationMs ? `${(speechDurationMs/1000).toFixed(2)} s` : "—";
      $("#voice-audio-energy").textContent = energyRms != null ? energyRms.toFixed(6) : "—";
      $("#voice-transcript-raw").textContent = raw_text || "—";
      $("#voice-transcript-normalized").textContent = normalized_text || "—";
      $("#voice-engine-active").textContent = engineName(data.engine, data);
      $("#voice-confidence").textContent = confidence != null ? `${confidence.toFixed(2)}` : "—";
      $("#voice-clarification-reasons").textContent = clarificationReasons && clarificationReasons.length ? clarificationReasons.join(", ") : "—";
      $("voice-timing").textContent = `Transcripción · ${engineName(data.engine, data)}: ${elapsed(data.elapsed_ms)}`;
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
      let engine = $("voice-engine").value;
      showNotice("");
      if (!data.stt_engines?.[engine]?.available) {
        if (engine !== "whisper-small" || !data.stt_engines?.vosk?.available) {
          throw new Error(data.stt_engines?.[engine]?.reason || "El motor de voz seleccionado no está disponible.");
        }
        engine = "vosk";
        showNotice(FALLBACK_NOTICE);
      }
      await controller.start({wake: $("voice-wake").checked, speak: $("voice-speak").checked,
        engine,
        aliases: $("voice-aliases").value.split(","), windowSeconds: Number($("voice-window").value),
        speaker: Number($("voice-speaker").value), speed: Number($("voice-speed").value)});
    } catch (error) { controller.fail(error.message); }
    finally { $("voice-start").disabled = controller.active; }
  });
  $("voice-stop").addEventListener("click", () => controller.stop());
  $("voice-engine").addEventListener("change", () => { picked = true; showNotice(""); updateBadge(); });
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
    $("voice-diag-feedback").textContent = "Ajustes guardados · reserva de desarrollo";
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) controller.stop(); });
  window.addEventListener("pagehide", () => { controller.stop(); clearRecording(); });
  return controller;
}
