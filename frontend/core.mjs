/** Shared request contracts. No browser state or model behavior is simulated. */
import {AUTH_CODES, emit, sessionHeaders} from "./session.mjs";
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const limits = {assistant: 4000, general: 4000, knowledge: 16000};
export function validateFile(file) {
  if (!file) throw new Error("Selecciona un archivo.");
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) throw new Error("Formato no admitido. Usa PDF, DOCX, TXT o Markdown.");
  if (!file.size || file.size > MAX_FILE_BYTES) throw new Error("El archivo debe contener entre 1 byte y 10 MiB.");
}
export function chatPayload(mode, message, conversationId, choiceId) {
  if (!(mode in limits)) throw new Error("Modo de chat desconocido.");
  const trimmed = message.trim();
  if (!trimmed || trimmed.length > limits[mode]) throw new Error(`Escribe entre 1 y ${limits[mode]} caracteres.`);
  const payload = {message: trimmed};
  if (mode === "assistant") {
    if (conversationId) payload.session_id = conversationId;
    return payload;
  }
  if (mode === "knowledge") {
    if (conversationId) payload.conversation_id = conversationId;
    if (choiceId) payload.choice_document_id = choiceId;
  }
  return payload;
}
/** The top-bar connection badge. A backend without the embedding model still answers, but manual search silently
 * degrades to words only (BM25): that must be visible, not hidden in the status panel. */
export function connectionBadge(online, knowledge) {
  if (!online) return {text: "Backend no disponible", style: "bad", title: ""};
  if (knowledge && knowledge.embedding_available === false && knowledge.eligible_documents > 0) {
    return {
      text: "⚠ Manuales: solo búsqueda por palabras",
      style: "warning",
      title: "El modelo de embeddings (qwen3-embedding:0.6b) no está disponible en Ollama, así que los manuales se " +
        "buscan solo por palabras y pueden no encontrarse (sobre todo en inglés). Detén otros Ollama y arráncalo con " +
        "scripts\\start-ollama.ps1.",
    };
  }
  return {text: "● Backend conectado", style: "good", title: ""};
}
export function sourcePath(id) {
  if (typeof id !== "string" || !/^[a-f0-9-]{36}$/i.test(id)) throw new Error("Identificador de fuente inválido.");
  return `/api/v1/knowledge/sources/${encodeURIComponent(id)}`;
}
export function documentState(doc) {
  if (doc.withdrawn) return {label: "Retirado", style: "neutral"};
  if (!doc.authorized) return {label: "Indexado · no autorizado", style: "warning"};
  return {label: "Indexado · autorizado", style: "good"};
}
export async function api(path, options = {}, timeout = 150000) {
  const start = performance.now();
  const controller = new AbortController();
  const cancel = () => controller.abort();
  options.signal?.addEventListener("abort", cancel, {once: true});
  if (options.signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    // Same-origin cookie (the login session) and, for a change, the CSRF token of that session.
    const headers = {...(options.headers || {}), ...sessionHeaders(options.method)};
    const response = await fetch(path, {...options, headers, signal: controller.signal, cache: "no-store",
      credentials: "same-origin"});
    let data;
    try { data = await response.json(); }
    catch { throw new Error("El servidor devolvió una respuesta no válida."); }
    if (!response.ok) {
      const error = new Error(data?.error?.message || `La petición falló (HTTP ${response.status}).`);
      error.code = data?.error?.code;
      error.status = response.status;
      if (AUTH_CODES.has(error.code)) emit("required", error.code);
      throw error;
    }
    return {data, ms: performance.now() - start};
  } catch (error) {
    if (error.name === "AbortError") {
      error = new Error("Se agotó el tiempo de espera. El servidor podría seguir procesando la petición; comprueba el estado antes de repetirla.");
    } else if (error instanceof TypeError) {
      error = new Error("No se pudo conectar con el backend local. Comprueba que siga iniciado.");
    }
    error.ms = performance.now() - start;
    throw error;
  } finally { clearTimeout(timer); options.signal?.removeEventListener("abort", cancel); }
}
export function jsonOptions(body, method = "POST") {
  return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
}
export function elapsed(ms) { return `${(ms / 1000).toFixed(2)} s`; }
