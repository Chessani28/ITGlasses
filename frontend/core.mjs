/** Shared request contracts. No browser state or model behavior is simulated. */
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const limits = {general: 4000, knowledge: 16000};
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
  if (mode === "knowledge") {
    if (conversationId) payload.conversation_id = conversationId;
    if (choiceId) payload.choice_document_id = choiceId;
  }
  return payload;
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
    const response = await fetch(path, {...options, signal: controller.signal, cache: "no-store"});
    let data;
    try { data = await response.json(); }
    catch { throw new Error("El servidor devolvió una respuesta no válida."); }
    if (!response.ok) {
      const error = new Error(data?.error?.message || `La petición falló (HTTP ${response.status}).`);
      error.code = data?.error?.code;
      error.status = response.status;
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
