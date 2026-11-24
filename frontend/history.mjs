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
/** Groups of the sidebar list by the day of the last message, in the browser's time zone. */
export const GROUPS = ["Hoy", "Ayer", "7 días", "Anteriores"];
export function groupOf(seconds, now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const at = (seconds || 0) * 1000;
  if (at >= today) return "Hoy";
  if (at >= today - DAY * 1000) return "Ayer";
  if (at >= today - 7 * DAY * 1000) return "7 días";
  return "Anteriores";
}
/** [{label, items}] in GROUPS order, without empty groups; the order of the items is kept. */
export function groupByDate(items, now = new Date()) {
  const groups = new Map(GROUPS.map(label => [label, []]));
  for (const item of items) groups.get(groupOf(item.last_message_at, now)).push(item);
  return [...groups].filter(([, list]) => list.length).map(([label, list]) => ({label, items: list}));
}
/** "web", "glasses" or "phone". Glasses only when the user's own device list (from the backend) says the device is
 * a Ray-Ban Meta; any other device, or one not in that list, is shown as a phone. */
export function channelKind(item, variants = new Map()) {
  if (item.channel === "web") return "web";
  return variants.get(item.device_id) === "meta" ? "glasses" : "phone";
}
export const KIND_LABELS = {web: "Web", phone: "Celular", glasses: "Lentes"};
/** How many filters are active (shown on the filter button). */
export function activeFilters({channel = "", deviceId = "", deleted = false} = {}) {
  return (channel ? 1 : 0) + (deviceId ? 1 : 0) + (deleted ? 1 : 0);
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
const ICONS = {
  web: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M3 12h18", "M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"],
  phone: ["M8 2.5h8a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5Z", "M11 18.5h2"],
  glasses: ["M3 10.5h18", "M3.5 10.5l1 4.5a2 2 0 0 0 2 1.5h2.5a2 2 0 0 0 2-1.6l.5-2.6", "M20.5 10.5l-1 4.5a2 2 0 0 1-2 1.5H15a2 2 0 0 1-2-1.6l-.5-2.6"],
};
function icon(kind) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "channel-icon");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[kind]) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
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
    loading: false, dialog: null, refreshTimer: null, variants: new Map(), menu: null};

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
    $("history-filter-toggle").hidden = !logged;
    if (!logged) filters(false);
    const count = activeFilters({channel: $("history-channel").value, deviceId: $("history-device").value, deleted: view.deleted});
    $("history-filter-count").textContent = count ? String(count) : "";
    $("history-filter-toggle").classList.toggle("on", count > 0);
    list.hidden = !logged;
    $("history-count").textContent = logged ? String(view.items.length) + (view.cursor ? "+" : "") : "—";
    $("history-more").hidden = !logged || !view.cursor;
    $("history-deleted").textContent = view.deleted ? "← Volver a mis conversaciones" : "Ver borradas";
    $("history-deleted").setAttribute("aria-pressed", String(view.deleted));
    $("history-title").textContent = view.deleted ? "Borradas" : "Historial";
    note();
    if (!logged) { list.replaceChildren(); return; }
    const current = chat.current();
    const shown = view.items.find(item => item.id === current);
    if (shown) chat.titled?.(titleOf(shown));   // the header follows a rename
    closeMenu();
    const row = item => {
      const row = el("li", "history-item" + (item.id === current ? " active" : ""));
      const kind = channelKind(item, view.variants);
      const open = button("", "history-open", () => openConversation(item.id), `Abrir: ${titleOf(item)} · ${KIND_LABELS[kind]}`);
      open.title = describe(item);
      if (view.deleted) open.disabled = true;
      open.append(icon(kind), el("span", "history-title", titleOf(item)));
      if (view.deleted) open.append(el("small", "warning-text", purgeText(item.purge_after)));
      if (item.id === current) open.setAttribute("aria-current", "true");
      const actions = el("div", "history-actions");
      if (view.deleted) {
        actions.append(button("Restaurar", "secondary-button", () => restore(item), `Restaurar ${titleOf(item)}`));
      } else {
        const more = button("⋯", "history-more-button", event => { event.stopPropagation(); toggleMenu(item, more); },
          `Opciones de ${titleOf(item)}`);
        more.setAttribute("aria-haspopup", "menu");
        more.setAttribute("aria-expanded", "false");
        actions.append(more);
      }
      row.append(open, actions);
      return row;
    };
    list.replaceChildren(...groupByDate(view.items).flatMap(group => [
      el("li", "history-group", group.label), ...group.items.map(row)]));
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
    view.variants = new Map();
    if (!session.user) return;
    try {
      const {data} = await api("/api/v1/me/devices", {}, 20000);
      for (const device of data.devices) {
        view.variants.set(device.id, device.variant);
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

  // --- the ⋯ menu of a row (Renombrar, Borrar) ------------------------------------------------------------------
  function closeMenu(focus = false) {
    if (!view.menu) return;
    const {node, opener} = view.menu;
    view.menu = null;
    node.remove();
    opener.setAttribute("aria-expanded", "false");
    opener.closest(".history-item")?.classList.remove("menu-open");
    if (focus) opener.focus();
  }
  function toggleMenu(item, opener) {
    const same = view.menu?.opener === opener;
    closeMenu();
    if (same) return;
    const node = el("div", "history-menu");
    node.setAttribute("role", "menu");
    const choose = (text, className, run) => {
      const choice = button(text, "menu-item" + (className ? " " + className : ""), () => { closeMenu(); run(); });
      choice.setAttribute("role", "menuitem");
      return choice;
    };
    node.append(choose("Renombrar", "", () => askRename(item)), choose("Borrar", "danger-text", () => askDelete(item)));
    opener.closest(".history-item").classList.add("menu-open");
    opener.closest(".history-item").append(node);
    opener.setAttribute("aria-expanded", "true");
    view.menu = {node, opener};
    node.querySelector("button").focus();
  }
  function filters(show) {
    $("history-tools").hidden = !show;
    $("history-filter-toggle").setAttribute("aria-expanded", String(show));
  }
  document.addEventListener("click", event => {
    if (view.menu && !view.menu.node.contains(event.target)) closeMenu();
    if (!$("history-tools").hidden && !event.target.closest("#history-tools, #history-filter-toggle")) filters(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
    if (view.menu) { event.stopImmediatePropagation(); closeMenu(true); }
    else if (!$("history-tools").hidden) { event.stopImmediatePropagation(); filters(false); $("history-filter-toggle").focus(); }
  }, true);
  $("history-filter-toggle").addEventListener("click", event => {
    event.stopPropagation();
    filters($("history-tools").hidden);
    if (!$("history-tools").hidden) $("history-channel").focus();
  });

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
