// Device administration panel (local beta). Served at http://127.0.0.1:8000 only.
// Everything a phone sends (name, model) is untrusted: text goes in with textContent,
// never as HTML. No imports, so tests can run this file in a vm context.
const POLL_MS = 5000;
const MAX_BACKOFF_MS = 30000;
const TOAST_MS = 8000;
const COLLAPSE_OVER = 5;
const ORDER = ["pending", "approved", "active", "blocked", "denied"];
const GROUPS = {pending: "Pendientes", approved: "Aprobados", active: "Activos", blocked: "Bloqueados", denied: "Rechazados"};
const STATES = {pending: "Pendiente", approved: "Aprobado, esperando al celular", active: "Activo",
  blocked: "Bloqueado", denied: "Rechazado"};
const ACTIONS = {pending: ["approve", "deny", "delete"], approved: ["delete"], active: ["block", "delete"],
  blocked: ["unblock", "delete"], denied: ["delete"]};
const LABELS = {approve: "Aprobar", deny: "Rechazar", block: "Bloquear", unblock: "Desbloquear", delete: "Eliminar"};
const STYLES = {approve: "primary-button", block: "danger-button", delete: "danger-button"};
const CONFIRM = {
  block: {title: "¿Bloquear este dispositivo?", button: "Bloquear",
    consequence: "Dejará de funcionar hasta que lo desbloquees. No hace falta tocar el celular."},
  delete: {title: "¿Eliminar este dispositivo?", button: "Eliminar",
    consequence: "Tendrá que emparejarse y aprobarse de nuevo desde la app."},
};
const FORBIDDEN = "Para administrar dispositivos inicia sesión como administrador en la PC del servidor: http://127.0.0.1:8000";
const OFFLINE = "No se puede contactar con el servidor. Reintentando en ";

const $ = id => document.getElementById(id);
const state = {timer: null, loading: false, again: false, failures: 0, forbidden: false, devices: [],
  known: null, lastSuccess: 0, buttons: new Map(), closed: new Set(), confirming: null, toastTimer: null};

// Stage "usuarios" 6: the login session cookie goes with every request (same origin) and every change carries
// the session's CSRF token (session.mjs keeps it in globalThis.itgSession). The custom header stays: without a
// login it is what the old local mode (ITGLASSES_LOOPBACK_MODE=legacy) accepts.
function csrfHeader(method) {
  const shared = typeof globalThis !== "undefined" ? globalThis.itgSession : null;
  return method !== "GET" && shared && shared.csrf ? {"X-CSRF-Token": shared.csrf} : {};
}

async function api(path, method = "GET") {
  const response = await fetch("/api/v1/admin/" + path, {
    method, cache: "no-store", credentials: "same-origin", redirect: "error",
    headers: {"X-ITGlasses-Admin": "1", ...csrfHeader(method)},
  });
  if (!response.ok) throw Object.assign(new Error("HTTP " + response.status), {status: response.status});
  return response.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nowSeconds() { return Math.floor(Date.now() / 1000); }

function relative(seconds) {
  if (!seconds) return "nunca";
  const diff = Math.max(0, nowSeconds() - seconds);
  if (diff < 60) return "hace unos segundos";
  if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
  return `hace ${Math.floor(diff / 86400)} d`;
}

function formatCode(code) { return /^\d{6}$/.test(code) ? code.slice(0, 3) + " " + code.slice(3) : code; }

function remaining(expires) {
  const left = Math.max(0, expires - nowSeconds());
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

function label(device) { return device.name || "Sin nombre"; }

function ordered(group, status) {
  return [...group].sort(status === "pending"
    ? (a, b) => (a.created_at || 0) - (b.created_at || 0)
    : (a, b) => (b.last_seen || 0) - (a.last_seen || 0));
}

function row(device) {
  const item = el("li", "device-row device-" + device.state);
  const info = el("div", "device-info");
  info.append(el("strong", "device-name", label(device)));
  const kind = device.variant === "meta" ? "Ray-Ban Meta" : "Celular";
  info.append(el("span", "device-meta", [device.model, kind].filter(Boolean).join(" · ")));
  info.append(el("span", "device-state", STATES[device.state] || device.state));
  if (device.state === "pending" && device.code) {
    const code = el("span", "device-code", formatCode(device.code));
    code.setAttribute("aria-label", "Código " + device.code.split("").join(" "));
    info.append(code);
    if (device.expires) info.append(el("span", "device-expiry", "Caduca en " + remaining(device.expires)));
  }
  if (device.state !== "pending" && device.state !== "denied") {
    info.append(el("span", "device-owner", device.owner_username
      ? "Usuario: " + (device.owner_display_name || device.owner_username)
      : "Sin usuario asignado"));
  }
  const seen = el("span", "device-seen", "Última conexión: " + relative(device.last_seen));
  if (device.last_seen) seen.setAttribute("title", new Date(device.last_seen * 1000).toLocaleString("es-MX"));
  info.append(seen);
  if (device.last_ip) info.append(el("span", "device-ip", "IP: " + device.last_ip + " (informativa)"));
  const actions = el("div", "device-actions");
  for (const action of ACTIONS[device.state] || ["delete"]) {
    const button = el("button", STYLES[action] || "secondary-button", LABELS[action]);
    button.type = "button";
    button.setAttribute("aria-label", LABELS[action] + " " + label(device));
    button.dataset.key = device.id + ":" + action;
    button.addEventListener("click", () => choose(device, action, button));
    state.buttons.set(button.dataset.key, button);
    actions.append(button);
  }
  item.append(info, actions);
  return item;
}

function render(focusKey) {
  const list = $("device-list");
  const active = document.activeElement;
  const keep = focusKey || (active && active.dataset && active.dataset.key);
  state.buttons = new Map();
  list.replaceChildren();
  if (!state.devices.length && !state.forbidden) {
    list.append(el("p", "empty-panel", "Aún no hay dispositivos. Instala la app en un celular y aparecerá aquí como pendiente."));
  }
  for (const status of ORDER) {
    const group = state.devices.filter(device => device.state === status);
    if (!group.length) continue;
    const title = `${GROUPS[status]} (${group.length})`;
    const collapsible = status !== "pending" && group.length > COLLAPSE_OVER;
    const section = el(collapsible ? "details" : "section", "device-group device-group-" + status);
    if (collapsible) {
      section.open = !state.closed.has(status);
      section.addEventListener("toggle", () => section.open ? state.closed.delete(status) : state.closed.add(status));
      section.append(el("summary", "device-group-title", title));
    } else {
      section.append(el("h3", "device-group-title", title));
    }
    const rows = el("ul", "device-rows");
    for (const device of ordered(group, status)) rows.append(row(device));
    section.append(rows);
    list.append(section);
  }
  if (keep) {
    const button = state.buttons.get(keep);
    if (button) button.focus();
    else if (focusKey) list.focus();
  }
}

function setAlert(message) {
  $("device-alert-text").textContent = message || "";
  $("device-alert").hidden = !message;
}

function setFeedback(message) { $("device-feedback").textContent = message; }

function toast(message) {
  $("device-toast").textContent = message;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { $("device-toast").textContent = ""; }, TOAST_MS);
}

function stamp(stale) {
  $("device-list").classList.toggle("stale", stale);
  $("device-updated").textContent = state.lastSuccess
    ? (stale ? "Datos de " : "Actualizado ") + relative(Math.floor(state.lastSuccess / 1000)) : "";
}

function announce(devices) {
  if (state.known) {
    for (const device of devices) {
      const before = state.known.get(device.id);
      if (device.state === "active" && (before === "pending" || before === "approved")) {
        toast("Dispositivo conectado, guardado: " + label(device));
      }
    }
  }
  state.known = new Map(devices.map(device => [device.id, device.state]));
}

function schedule(delay) {
  clearTimeout(state.timer);
  state.timer = null;
  if (!state.forbidden && !document.hidden) state.timer = setTimeout(() => { state.timer = null; refresh(); }, delay);
}

function forbidden() {
  state.forbidden = true;
  state.devices = [];
  clearTimeout(state.timer);
  state.timer = null;
  render();
  setAlert(FORBIDDEN);
}

async function refresh(focusKey) {
  if (state.loading) { state.again = true; return; }
  state.loading = true;
  try {
    const {devices} = await api("devices");
    state.failures = 0;
    state.forbidden = false;
    setAlert("");
    announce(devices);
    state.devices = devices;
    state.lastSuccess = Date.now();
    render(focusKey);
    stamp(false);
    schedule(POLL_MS);
  } catch (error) {
    if (error.status === 403) {
      forbidden();
    } else {
      state.failures += 1;
      const delay = Math.min(POLL_MS * 2 ** (state.failures - 1), MAX_BACKOFF_MS);
      setAlert(OFFLINE + Math.round(delay / 1000) + " s.");
      stamp(true);
      schedule(delay);
    }
  } finally {
    state.loading = false;
    if (state.again) { state.again = false; await refresh(); }
  }
}

async function perform(device, action, button) {
  button.disabled = true;
  const path = "devices/" + encodeURIComponent(device.id) + (action === "delete" ? "" : "/" + action);
  try {
    await api(path, action === "delete" ? "DELETE" : "POST");
    setFeedback("");
  } catch (error) {
    if (error.status === 403) { forbidden(); return; }
    setFeedback(error.status === 409 ? "El estado cambió; lista actualizada."
      : error.status === 404 ? "Ese dispositivo ya no existe; lista actualizada."
      : "No se pudo completar la acción. Comprueba la conexión.");
  } finally {
    button.disabled = false;
  }
  await refresh(button.dataset.key);
}

function choose(device, action, button) {
  const info = CONFIRM[action];
  if (!info) return perform(device, action, button);
  $("device-dialog-title").textContent = info.title;
  const details = $("device-dialog-details");
  details.replaceChildren();
  for (const [name, value] of [["Nombre", label(device)], ["Modelo", device.model || "—"],
    ["IP", device.last_ip || "—"], ["Última conexión", relative(device.last_seen)]]) {
    details.append(el("dt", "", name), el("dd", "", value));
  }
  $("device-dialog-consequence").textContent = info.consequence;
  $("device-dialog-confirm").textContent = info.button;
  state.confirming = {device, action, button};
  $("device-dialog").showModal();
  $("device-dialog-cancel").focus();
}

// QR pairing (2026-11-26): one APK for any server. The phone scans the server's address and certificate pin;
// the admin still approves it with its code. The SVG goes in as an <img> (data URL), never as HTML.
const PAIR_ERRORS = {
  404: "Falta configurar la dirección pública del servidor (ITGLASSES_PAIRING_ORIGIN).",
  503: "No pude leer el certificado del servidor. Revisa que Caddy esté encendido y vuelve a intentarlo.",
  403: "Solo un administrador puede ver el QR de emparejamiento.",
};

async function showPairing() {
  const status = $("pair-status");
  const image = $("pair-qr");
  $("pair-origin").textContent = "";
  image.hidden = true;
  status.textContent = "Generando el QR…";
  $("pair-dialog").showModal();
  try {
    const data = await api("pairing-link");
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data.svg);
    image.hidden = false;
    $("pair-origin").textContent = data.origin;
    status.textContent = "En la app ITGlasses toca «Escanear QR del servidor» y apunta a este código. "
      + "Después aprueba el celular aquí comparando su código.";
  } catch (error) {
    status.textContent = PAIR_ERRORS[error.status] || "No se pudo generar el QR. Inténtalo de nuevo.";
  }
}

function start() {
  if (!$("device-panel")) return;
  if ($("pair-open")) {
    $("pair-open").addEventListener("click", showPairing);
    $("pair-close").addEventListener("click", () => $("pair-dialog").close());
  }
  $("device-dialog").addEventListener("close", () => {
    if (!state.confirming) return;
    const {button} = state.confirming;
    state.confirming = null;
    button.focus();
  });
  $("device-dialog-cancel").addEventListener("click", () => $("device-dialog").close());
  $("device-dialog-confirm").addEventListener("click", () => {
    const chosen = state.confirming;
    state.confirming = null;
    $("device-dialog").close();
    return chosen && perform(chosen.device, chosen.action, chosen.button);
  });
  $("device-retry").addEventListener("click", () => {
    state.forbidden = false;
    state.failures = 0;
    return refresh();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(state.timer); state.timer = null; }
    else if (!state.forbidden) refresh();
  });
  // After a login or logout the page asks the panel to try again (accounts.mjs).
  globalThis.itgDevicesRefresh = () => { state.forbidden = false; state.failures = 0; setAlert(""); return refresh(); };
  refresh();
}

start();
