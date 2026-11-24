/** Saved conversations in the chat page (stage "historial" 5): list with filters, open and continue, rename,
 * delete with undo, restore, and the notice that nothing is saved without a login.
 * Titles, messages and device names go in with textContent, never as HTML. */
import {api, jsonOptions} from "./core.mjs";
import {on, session} from "./session.mjs";

const PAGE = 30;
const DAY = 86400;

export function when(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString("es-MX", {dateStyle: "short", timeStyle: "short"}) : "—";
}
export function channelLabel(item) {
  return item.channel === "web" ? "Web" : (item.device_label || "Dispositivo");
}
export function describe(item) {
  const count = item.message_count === 1 ? "1 mensaje" : `${item.message_count || 0} mensajes`;
  return `${channelLabel(item)} · ${when(item.last_message_at)} · ${count}`;
}
export function titleOf(item) {
  return item.title || "Conversación sin título";
}
/** /api/v1/conversations with the filters of the panel. */
export function listPath({channel = "", deviceId = "", deleted = false, cursor = null, limit = PAGE} = {}) {
  const query = new URLSearchParams({limit: String(limit)});
  if (channel) query.set("channel", channel);
  if (deviceId) query.set("device_id", deviceId);
  if (deleted) query.set("deleted", "true");
  if (cursor) query.set("cursor", cursor);
  return `/api/v1/conversations?${query}`;
}
/** Saved messages as the chat page shows them (the answer's route as its badge, its sources as citations). */
export function toChatEntries(messages) {
  return (messages || []).map(message => message.role === "user"
    ? {role: "user", text: message.content}
    : {role: "assistant", text: message.content, status: message.route || undefined, sources: message.sources || []});
}
/** The line under the composer: whether this conversation is saved and who can see it. */
export function historyNote({user, saving, lastSaved}) {
  if (!user) return "Sin sesión · esta conversación no se guarda en el historial. Inicia sesión para conservarla.";
  if (saving === false) return "El historial está desactivado en este servidor · esta conversación no se guarda.";
  if (lastSaved === false) return "Esta respuesta no se guardó en el historial (no disponible). Puedes seguir conversando.";
  return "Se guarda en tu historial · solo tú puedes verla.";
}
export function purgeText(purgeAfter) {
  return purgeAfter ? `Se eliminará definitivamente el ${new Date(purgeAfter * 1000).toLocaleDateString("es-MX")}.` : "";
}
export function graceText(days) {
  return days === 0 ? "Se eliminará definitivamente en la siguiente limpieza."
    : `Podrás restaurarla durante ${days === 1 ? "1 día" : `${days} días`}; después se elimina definitivamente.`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function button(text, className, handler, label) {
  const node = el("button", className, text);
  node.type = "button";
  if (label) node.setAttribute("aria-label", label);
  node.addEventListener("click", handler);
  return node;
}

/** chat: {open(conversation), reset(), current(), busy()} from app.js. */
export function initHistory(chat) {
  const $ = id => document.getElementById(id);
  const view = {items: [], cursor: null, deleted: false, saving: null, grace: 7, lastSaved: null, user: null,
    loading: false, dialog: null, refreshTimer: null};

  function feedback(text, error = false, undo = null) {
    const box = $("history-feedback");
    box.replaceChildren();
    if (text) box.append(el("span", "", text));
    if (undo) box.append(button("Deshacer", "text-button", undo));
    box.classList.toggle("error", Boolean(error));
  }
  function note() {
    $("context-note").textContent = historyNote({user: session.user, saving: view.saving, lastSaved: view.lastSaved});
  }
  function render() {
    const list = $("history-list");
    const logged = Boolean(session.user);
    $("history-anonymous").hidden = logged;
    $("history-tools").hidden = !logged;
    list.hidden = !logged;
    $("history-count").textContent = logged ? String(view.items.length) + (view.cursor ? "+" : "") : "—";
    $("history-more").hidden = !logged || !view.cursor;
    $("history-deleted").textContent = view.deleted ? "← Volver a mis conversaciones" : "Ver borradas";
    $("history-title").textContent = view.deleted ? "Borradas" : "Historial";
    note();
    if (!logged) { list.replaceChildren(); return; }
    const current = chat.current();
    list.replaceChildren(...view.items.map(item => {
      const row = el("li", "history-item" + (item.id === current ? " active" : ""));
      const open = button("", "history-open", () => openConversation(item.id), `Abrir: ${titleOf(item)}`);
      if (view.deleted) open.disabled = true;
      open.append(el("strong", "", titleOf(item)), el("small", "", describe(item)));
      if (view.deleted) open.append(el("small", "warning-text", purgeText(item.purge_after)));
      if (item.id === current) open.setAttribute("aria-current", "true");
      const actions = el("div", "history-actions");
      if (view.deleted) {
        actions.append(button("Restaurar", "secondary-button", () => restore(item), `Restaurar ${titleOf(item)}`));
      } else {
        actions.append(button("Renombrar", "text-button", () => askRename(item), `Renombrar ${titleOf(item)}`),
          button("Borrar", "text-button danger-text", () => askDelete(item), `Borrar ${titleOf(item)}`));
      }
      row.append(open, actions);
      return row;
    }));
    if (!view.items.length) {
      list.append(el("li", "history-empty", view.deleted ? "No hay conversaciones borradas que se puedan restaurar."
        : "Todavía no hay conversaciones guardadas. Escribe una pregunta para empezar."));
    }
  }

  async function load(more = false) {
    if (!session.user || view.loading) { render(); return; }
    view.loading = true;
    try {
      const {data} = await api(listPath({channel: $("history-channel").value, deviceId: $("history-device").value,
        deleted: view.deleted, cursor: more ? view.cursor : null}), {}, 20000);
      view.items = more ? [...view.items, ...data.conversations] : data.conversations;
      view.cursor = data.next_cursor;
      view.saving = data.saving;
      view.grace = data.grace_days;
      if (!more) feedback("");
    } catch (error) {
      if (error.status !== 401) feedback(error.message, true);
    } finally { view.loading = false; render(); }
  }
  async function loadDevices() {
    const select = $("history-device");
    const keep = select.value;
    select.replaceChildren(Object.assign(el("option", "", "Todos los dispositivos"), {value: ""}));
    if (!session.user) return;
    try {
      const {data} = await api("/api/v1/me/devices", {}, 20000);
      for (const device of data.devices) {
        const option = el("option", "", device.name || device.model || (device.variant === "meta" ? "Ray-Ban Meta" : "Celular"));
        option.value = device.id;
        select.append(option);
      }
      select.value = [...select.options].some(option => option.value === keep) ? keep : "";
    } catch { /* the list still works without the device filter */ }
  }

  async function openConversation(id) {
    if (chat.busy()) { feedback("Espera a que termine la respuesta en curso.", true); return; }
    try {
      const {data} = await api(`/api/v1/conversations/${encodeURIComponent(id)}`, {}, 20000);
      chat.open(data.conversation);
      view.lastSaved = null;
      feedback(data.conversation.channel === "device"
        ? `Conversación de ${channelLabel(data.conversation)}: lo que escribas aquí se agrega a ella.`
        : data.conversation.truncated ? "Se muestran los mensajes más recientes." : "");
    } catch (error) {
      feedback(error.status === 404 ? "Esa conversación ya no está disponible." : error.message, true);
      await load();
    }
    render();
  }

  // --- rename and delete dialog -------------------------------------------------------------------------------
  function showDialog({title, text, value = null, confirm, danger = false, run}) {
    view.dialog = run;
    $("history-dialog-title").textContent = title;
    $("history-dialog-text").textContent = text || "";
    $("history-dialog-field").hidden = value === null;
    $("history-dialog-input").value = value ?? "";
    $("history-dialog-confirm").textContent = confirm;
    $("history-dialog-confirm").className = danger ? "danger-button" : "primary-button";
    $("history-dialog").showModal();
    (value === null ? $("history-dialog-cancel") : $("history-dialog-input")).focus();
  }
  function askRename(item) {
    showDialog({title: "Renombrar conversación", text: "Máximo 80 caracteres.", value: item.title || "",
      confirm: "Guardar", run: async () => {
        const title = $("history-dialog-input").value.trim();
        if (!title) { feedback("Escribe un título.", true); return; }
        try {
          await api(`/api/v1/conversations/${encodeURIComponent(item.id)}`, jsonOptions({title}, "PATCH"), 20000);
          feedback("Título guardado.");
        } catch (error) { feedback(error.message, true); }
        await load();
      }});
  }
  function askDelete(item) {
    showDialog({title: "¿Borrar esta conversación?", danger: true, confirm: "Borrar",
      text: `«${titleOf(item)}» desaparecerá de tu historial. ${graceText(view.grace)}` +
        (item.channel === "device" ? ` Tu siguiente mensaje desde ${channelLabel(item)} empezará una conversación nueva.` : ""),
      run: async () => {
        try {
          const {data} = await api(`/api/v1/conversations/${encodeURIComponent(item.id)}`, {method: "DELETE"}, 20000);
          if (chat.current() === item.id) chat.reset();
          feedback(`Conversación borrada. ${purgeText(data.purge_after)}`, false,
            data.grace_days > 0 ? () => restore(item, true) : null);
        } catch (error) { feedback(error.message, true); }
        await load();
      }});
  }
  async function restore(item, undo = false) {
    try {
      await api(`/api/v1/conversations/${encodeURIComponent(item.id)}/restore`, jsonOptions({}), 20000);
      feedback(undo ? "Borrado deshecho." : `«${titleOf(item)}» vuelve a tu historial.`);
    } catch (error) {
      feedback(error.status === 404 ? "Ya no se puede restaurar: pasó el plazo o ya se eliminó." : error.message, true);
    }
    await load();
  }
  $("history-dialog-cancel").addEventListener("click", () => { view.dialog = null; $("history-dialog").close(); });
  $("history-dialog").addEventListener("close", () => { view.dialog = null; });
  $("history-dialog-form").addEventListener("submit", async event => {
    event.preventDefault();
    const run = view.dialog;
    view.dialog = null;
    $("history-dialog").close();
    if (run) await run();
  });

  // --- filters and buttons --------------------------------------------------------------------------------------
  $("history-channel").addEventListener("change", () => {
    $("history-device").disabled = $("history-channel").value === "web";
    if ($("history-channel").value === "web") $("history-device").value = "";
    load();
  });
  $("history-device").addEventListener("change", () => load());
  $("history-deleted").addEventListener("click", () => { view.deleted = !view.deleted; load(); });
  $("history-more").addEventListener("click", () => load(true));
  $("history-refresh").addEventListener("click", () => load());
  $("history-login").addEventListener("click", () => $("user-login").click());

  // A login or logout never leaves the previous person's conversation on screen.
  on("change", () => {
    const user = session.user?.id || null;
    if (user !== view.user) {
      view.user = user;
      view.items = [];
      view.cursor = null;
      view.deleted = false;
      view.saving = null;
      view.lastSaved = null;
      chat.reset();
      loadDevices().then(() => load());
    } else render();
  });

  render();
  return {
    /** After every assistant answer: the conversation is in the list (or the reply says it was not saved). */
    answered(data) {
      view.lastSaved = session.user ? Boolean(data?.history_saved) : null;
      note();
      clearTimeout(view.refreshTimer);
      if (session.user && data?.history_saved) view.refreshTimer = setTimeout(() => load(), 300);
    },
    /** "Nueva conversación" or a reset: nothing is highlighted any more. */
    cleared() { view.lastSaved = null; render(); },
    refresh: load,
  };
}
