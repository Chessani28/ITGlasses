/** Pure voice orchestration. Wake matching is a prototype, not a wake-word model. */
export function wakeQuestion(text, aliases = ["oye itglasses", "oye it glasses", "oye iti clases", "oye y ti clases", "oye y te clases"]) {
  const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9ñ ]/g, " ").replace(/\s+/g, " ").trim();
  const words = normalize(text);
  for (const alias of aliases.map(normalize).filter(Boolean)) {
    if (words === alias) return "";
    if (words.startsWith(alias + " ")) {
      const tokens = [...text.matchAll(/[\p{L}\p{N}]+/gu)];
      const last = tokens[alias.split(" ").length - 1];
      return text.slice(last.index + last[0].length).replace(/^[\s,;:.!?¡¿]+/, "");
    }
  }
  return null;
}
export function normalizeProcedureNumbers(text) {
  const normalize = word => word.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const numbers = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco", "veintiseis", "veintisiete", "veintiocho", "veintinueve"];
  const table = new Map(numbers.map((word, index) => [word, index]));
  for (const [ten, base] of [["treinta",30],["cuarenta",40],["cincuenta",50],["sesenta",60],["setenta",70],["ochenta",80],["noventa",90]]) {
    table.set(ten,base);
    for(let i=1;i<10;i++) table.set(`${ten} y ${numbers[i]}`,base+i);
  }
  return text.replace(/(\b(?:procedimiento|id)\s+(?:número\s+|numero\s+)?)([a-záéíóúñ]+(?: y [a-záéíóúñ]+)?)(?=$|[.,!?;:]|\s+(?:de|del|para|que|sobre|en)\b)/gi,
    (whole, prefix, value) => table.has(normalize(value)) ? prefix + table.get(normalize(value)) : whole);
}
export function spokenAnswer(data) {
  const body = data.answer.replace(/\[[a-f0-9-]{36}\]/gi, "");
  const prefix = data.knowledge_type === "documented_evidence" ? "Respuesta de documentación. " : "Conocimiento general, no una política de la empresa. ";
  const sources = data.sources?.length ? ` Consulta las ${data.sources.length} fuentes en pantalla.` : "";
  const text = prefix + body + sources;
  if (text.length > 6000) throw new Error("La respuesta completa supera el límite de voz. Puedes leerla en pantalla o pedir una respuesta más breve.");
  return text;
}
export function microphoneError(error) {
  return ({NotAllowedError: "Permiso de micrófono denegado. Autorízalo en el navegador y en la privacidad de Windows.",
    NotFoundError: "No hay un micrófono disponible.", NotReadableError: "El micrófono está ocupado o Windows impide acceder a él.",
    SecurityError: "El micrófono requiere localhost o un contexto seguro."})[error.name] || error.message || "No se pudo iniciar el micrófono.";
}
export class VoiceController {
  constructor(io, clock = globalThis) { this.io = io; this.clock = clock; this.epoch = 0; this.active = false; this.phase = "idle"; }
  state(phase, detail = "") { this.phase = phase; this.io.state(phase, detail, this.active); }
  stop() {
    this.epoch++; this.active = false; this.clock.clearTimeout(this.timer);
    this.abort?.abort(); this.mic?.stop(); this.mic = null; this.io.stopAudio(); this.state("idle", "Micrófono apagado");
  }
  async start(preferences) {
    this.stop(); const epoch = this.epoch; this.preferences = preferences; this.active = true;
    this.state("listening", "Solicitando micrófono…");
    try {
      const mic = await this.io.capture(audio => this.utterance(audio), () => this.active && ["idle", "listening"].includes(this.phase),
        () => this.activity(), message => { if (epoch === this.epoch) this.fail(message); });
      if (epoch !== this.epoch) { mic.stop(); return; }
      this.mic = mic; this.arm(preferences.wake);
    } catch (error) { if (epoch === this.epoch) this.fail(microphoneError(error)); }
  }
  fail(message) { this.stop(); this.state("error", message); }
  activity() {
    if (!this.active || this.phase !== "listening") return;
    this.clock.clearTimeout(this.timer);
    this.timer = this.clock.setTimeout(() => {
      if (this.preferences.wake) this.arm(true); else this.stop();
    }, this.preferences.windowSeconds * 1000);
  }
  arm(wake = false) {
    this.clock.clearTimeout(this.timer);
    if (!this.active) return;
    this.waitingWake = wake;
    this.state(wake ? "idle" : "listening", wake ? "Micrófono activo · esperando frase experimental" : "Te escucho · pregunta o aclaración");
    if (!wake) this.activity();
  }
  async utterance(audio) {
    if (!this.active || !["idle", "listening"].includes(this.phase)) return;
    const epoch = this.epoch, waitingWake = this.waitingWake;
    this.clock.clearTimeout(this.timer); this.abort = new AbortController();
    this.state("transcribing", "Reconocimiento local en CPU…");
    try {
      const transcript = await this.io.transcribe(audio, this.abort.signal);
      if (epoch !== this.epoch) return;
      this.io.transcript(transcript.text, transcript.elapsed_ms);
      let question = transcript.text.trim();
      if (waitingWake) question = wakeQuestion(question, this.preferences.aliases);
      if (question === null) { this.arm(true); return; }
      if (!question) { this.arm(waitingWake ? false : this.preferences.wake); return; }
      this.state("thinking", "Consultando el modo de chat seleccionado…");
      const answer = await this.io.chat(normalizeProcedureNumbers(question), this.abort.signal);
      if (epoch !== this.epoch) return;
      if (this.preferences.speak) {
        this.state("speaking", "Preparando respuesta de voz local…");
        await this.io.speak(answer, this.preferences, this.abort.signal);
      }
      if (epoch === this.epoch) this.arm(false);
    } catch (error) { if (epoch === this.epoch) this.fail(error.message); }
  }
}
