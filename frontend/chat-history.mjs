// Historial de chats del asistente guardado en este navegador (localStorage).
// No sale del equipo: el servidor solo conserva el contexto unos 30 minutos.

export const HISTORY_KEY = "itglasses.chats.v1";
export const MAX_CHATS = 50;
export const MAX_MESSAGES = 200;
const TITLE_LENGTH = 60;

export function newChatId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Título del chat: la primera pregunta del usuario, recortada. */
export function chatTitle(messages) {
  const first = (messages || []).find(entry => entry.role === "user" && String(entry.text || "").trim());
  if (!first) return "Nueva conversación";
  const text = String(first.text).replace(/\s+/g, " ").trim();
  return text.length > TITLE_LENGTH ? `${text.slice(0, TITLE_LENGTH - 1).trimEnd()}…` : text;
}

/** Solo lo que vale la pena guardar: sin mensajes pendientes ni opciones de un solo uso. */
export function storableMessages(messages) {
  return (messages || [])
    .filter(entry => !entry.pending && (entry.role === "user" || entry.role === "assistant"))
    .slice(-MAX_MESSAGES)
    .map(({role, text, ms, status, sources, error}) => {
      const item = {role, text: String(text ?? "")};
      if (ms !== undefined) item.ms = ms;
      if (status) item.status = status;
      if (Array.isArray(sources) && sources.length) item.sources = sources;
      if (error) item.error = true;
      return item;
    });
}

function validChat(value) {
  return value && typeof value.id === "string" && Array.isArray(value.messages) && typeof value.updated === "number";
}

export function createHistory(storage = globalThis.localStorage, now = () => Date.now()) {
  function read() {
    try {
      const parsed = JSON.parse(storage?.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter(validChat) : [];
    } catch { return []; }
  }
  function write(chats) {
    const kept = [...chats].sort((a, b) => b.updated - a.updated);
    // Si el navegador se queda sin espacio, se descartan los chats más viejos.
    for (let count = Math.min(kept.length, MAX_CHATS); count >= 0; count--) {
      try { storage?.setItem(HISTORY_KEY, JSON.stringify(kept.slice(0, count))); return true; }
      catch { /* sin espacio: intenta con menos */ }
    }
    return false;
  }
  return {
    /** Chats guardados, del más reciente al más viejo. */
    list() { return read().sort((a, b) => b.updated - a.updated); },
    get(id) { return read().find(chat => chat.id === id) || null; },
    /** Guarda o actualiza un chat. Los chats vacíos no se guardan. */
    save({id, messages, conversation}) {
      const stored = storableMessages(messages);
      if (!id || !stored.length) return false;
      const chats = read().filter(chat => chat.id !== id);
      const previous = read().find(chat => chat.id === id);
      chats.push({id, title: chatTitle(stored), messages: stored, conversation: conversation || null,
        created: previous?.created ?? now(), updated: now()});
      return write(chats);
    },
    remove(id) { return write(read().filter(chat => chat.id !== id)); },
    clear() { try { storage?.removeItem(HISTORY_KEY); return true; } catch { return false; } },
  };
}

/** "Hoy 14:05", "Ayer", o la fecha corta, para la lista de chats. */
export function relativeDate(timestamp, now = Date.now()) {
  const date = new Date(timestamp), today = new Date(now);
  const startOfDay = day => new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
  const days = Math.round((startOfDay(today) - startOfDay(date)) / 86400000);
  const time = date.toLocaleTimeString("es-MX", {hour: "2-digit", minute: "2-digit"});
  if (days === 0) return `Hoy ${time}`;
  if (days === 1) return `Ayer ${time}`;
  return date.toLocaleDateString("es-MX", {day: "numeric", month: "short"});
}
