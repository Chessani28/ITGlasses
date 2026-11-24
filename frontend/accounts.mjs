/** Login screen, user menu, password change and the administration pages (stage "usuarios" 6).
 * Everything an account or a phone sends (names, models) goes in with textContent, never as HTML. */
import {api, jsonOptions} from "./core.mjs";
import {forget, hasRole, on, remember, session} from "./session.mjs";

const $ = id => document.getElementById(id);
const ADMINS = ["admin", "superadmin"];
const ANONYMOUS_KEY = "itg-continue-without-login";
export const ROLE_NAMES = {superadmin: "Superadmin", admin: "Administrador", user: "Usuario"};
const DEVICE_STATES = {pending: "Pendiente", approved: "Aprobado, esperando al celular", active: "Activo",
  blocked: "Bloqueado", denied: "Rechazado"};
const ACTIONS = {
  "bootstrap.superadmin": "Creación del superadmin", "user.create": "Usuario creado", "user.update": "Usuario editado",
  "user.disable": "Usuario desactivado", "user.enable": "Usuario activado", "user.unlock": "Usuario desbloqueado",
  "user.role": "Cambio de rol", "user.password_reset": "Contraseña restablecida",
  "user.password_change": "Contraseña cambiada", "device.approve": "Dispositivo aprobado",
  "device.deny": "Dispositivo rechazado", "device.block": "Dispositivo bloqueado",
  "device.unblock": "Dispositivo desbloqueado", "device.delete": "Dispositivo eliminado",
  "device.assign": "Dispositivo asignado", "document.upload": "Documento subido",
  "document.replace": "Documento actualizado", "document.authorize": "Autorización de documento",
  "document.withdraw": "Documento retirado", "document.delete": "Documento eliminado",
  "document.reindex": "Documento reindexado", "settings.update": "Ajustes cambiados",
  "auth.login": "Inicio de sesión", "auth.logout": "Cierre de sesión", "auth.locked": "Cuenta bloqueada por intentos",
  "conversation.delete": "Conversación borrada", "conversation.restore": "Conversación restaurada",
  "conversation.purge": "Historial depurado",
};
const VIA = {local: "PC sin sesión", token: "Token compartido", device: "Dispositivo", session: "Sesión"};
const OUTCOMES = {ok: ["Correcto", "good"], denied: ["Rechazado", "warning"], error: ["Error", "bad"]};
const ANONYMOUS_NOTES = {
  legacy: "Sin sesión esta PC puede usar todo, como antes (modo legacy).",
  chat: "Sin sesión solo podrás usar el chat y la voz.",
};

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function button(text, className, handler) {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
export function when(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString("es-MX", {dateStyle: "short", timeStyle: "short"}) : "—";
}
function feedback(id, text, error = false) {
  $(id).textContent = text || "";
  $(id).classList.toggle("error", Boolean(error));
}
function storage() { try { return globalThis.sessionStorage || null; } catch { return null; } }
function anonymousChosen() { try { return storage()?.getItem(ANONYMOUS_KEY) === "1"; } catch { return false; } }
function chooseAnonymous(value) {
  try { value ? storage()?.setItem(ANONYMOUS_KEY, "1") : storage()?.removeItem(ANONYMOUS_KEY); } catch { /* private mode */ }
}

/** Who may see what (the server decides anyway; this only hides what would be refused). */
export function allowed(need, state = session) {
  const admin = Boolean(state.user) && ADMINS.includes(state.user.role);
  if (need === "login") return Boolean(state.user);
  if (need === "admin") return admin;
  if (need === "documents") return admin || (!state.user && state.loopbackMode === "legacy");
  return true;
}
export function anonymousAllowed(state = session) { return Boolean(state.loopbackMode) && state.loopbackMode !== "off"; }

export function describeDetail(detail) {
  if (!detail) return "";
  return Object.entries(detail).filter(([key]) => key !== "via")
    .map(([key, value]) => `${key}: ${value === null ? "—" : value}`).join(" · ");
}
/** Admin column "Historial": counts and dates only (the server never sends titles or messages). */
export function historySummary(stats) {
  if (!stats) return {main: "—", note: ""};
  if (!stats.conversations && !stats.deleted_pending) return {main: "Sin conversaciones", note: ""};
  const main = `${stats.conversations} conv. · ${stats.messages} mensajes`;
  const parts = [];
  if (stats.web) parts.push(`${stats.web} web`);
  if (stats.devices?.length) parts.push(stats.devices.map(device => `${device.label || "Dispositivo"} (${device.conversations})`).join(", "));
  if (stats.deleted_pending) parts.push(`${stats.deleted_pending} borrada${stats.deleted_pending === 1 ? "" : "s"} pendiente${stats.deleted_pending === 1 ? "" : "s"}`);
  if (stats.last_at) parts.push(`última ${when(stats.last_at)}`);
  return {main, note: parts.join(" · ")};
}
export function actorName(entry) {
  if (entry.actor_username) return entry.actor_username;
  if (entry.actor_kind === "script") return "Script local";
  if (entry.actor_kind === "system") return "Sistema";
  return VIA[entry.detail?.via] || "—";
}

export function initAccounts({showPage, currentPage, onChange}) {
  const pages = {users: loadUsers, owners: loadOwners, "my-devices": loadMine, audit: () => loadAudit(false)};
  let booting = true;
  let auditBefore = null;
  let confirmAction = null;
  let forced = false;

  function applyVisibility() {
    document.querySelectorAll("[data-needs]").forEach(node => { node.hidden = !allowed(node.dataset.needs); });
    const devicePanel = $("device-panel");
    if (devicePanel) devicePanel.hidden = !allowed("documents");
    const user = session.user;
    $("user-name").textContent = user ? (user.display_name || user.username) : "Sin sesión";
    $("user-role").textContent = user ? ROLE_NAMES[user.role] || user.role
      : ({legacy: "Modo local completo", chat: "Solo chat y voz"}[session.loopbackMode] || "Inicia sesión para usarlo");
    $("user-avatar").textContent = user ? (user.display_name || user.username).slice(0, 1).toUpperCase() : "?";
    $("user-login").hidden = Boolean(user);
    $("user-password").hidden = !user;
    $("user-logout").hidden = !user;
    const page = currentPage();
    const section = document.querySelector(`[data-page="${page}"]`);
    if (section && section.hidden) showPage("chat");
  }

  function showLogin(message = "") {
    $("login-screen").hidden = false;
    feedback("login-feedback", message, true);
    const anonymous = anonymousAllowed();
    $("login-anonymous").hidden = !anonymous;
    $("login-anonymous-note").textContent = anonymous ? ANONYMOUS_NOTES[session.loopbackMode] || "" :
      "Esta PC necesita iniciar sesión para cualquier uso.";
    $("login-username").focus();
  }
  function hideLogin() {
    $("login-screen").hidden = true;
    $("login-password").value = "";
    feedback("login-feedback", "");
  }

  function openPassword(mustChange) {
    forced = Boolean(mustChange);
    $("password-form").reset();
    $("password-reason").textContent = forced
      ? "Tu contraseña es temporal o fue restablecida. Elige una nueva para continuar."
      : `Mínimo ${session.passwordMinLength} caracteres.`;
    $("password-current-label").firstChild.textContent = forced ? "Contraseña temporal " : "Contraseña actual ";
    $("password-cancel").hidden = forced;
    feedback("password-feedback", "");
    if (!$("password-dialog").open) $("password-dialog").showModal();
    $("password-current").focus();
  }

  async function changed() {
    applyVisibility();
    try { await onChange?.(); } catch { /* each page reports its own errors */ }
  }

  async function boot() {
    try {
      const {data} = await api("/api/v1/auth/config", {}, 10000);
      session.loopbackMode = data.loopback_mode;
      session.passwordMinLength = data.password_min_length;
    } catch {
      // A backend older than this page (no /auth/config yet) behaves as before: legacy, everything allowed.
      session.loopbackMode = "legacy";
    }
    try { remember((await api("/api/v1/auth/me", {}, 10000)).data); } catch { forget(); }
    booting = false;
    if (session.user?.must_change_pw) openPassword(true);
    else if (!session.user && !(anonymousAllowed() && anonymousChosen())) showLogin();
    await changed();
  }

  on("required", code => {
    if (booting) return;
    if (code === "password_change_required") { openPassword(true); return; }
    forget();
    showLogin("Tu sesión terminó o esta acción necesita iniciar sesión.");
  });

  $("login-form").addEventListener("submit", async event => {
    event.preventDefault();
    $("login-submit").disabled = true;
    feedback("login-feedback", "");
    try {
      const {data} = await api("/api/v1/auth/login", jsonOptions({username: $("login-username").value.trim(),
        password: $("login-password").value}), 20000);
      remember(data);
      chooseAnonymous(false);
      hideLogin();
      if (data.user.must_change_pw) openPassword(true);
      await changed();
    } catch (error) {
      feedback("login-feedback", error.message, true);
      $("login-password").select();
    } finally { $("login-submit").disabled = false; }
  });
  $("login-anonymous").addEventListener("click", async () => {
    chooseAnonymous(true);
    hideLogin();
    await changed();
  });
  $("user-login").addEventListener("click", () => showLogin());
  $("user-password").addEventListener("click", () => openPassword(false));
  $("user-logout").addEventListener("click", async () => {
    try { await api("/api/v1/auth/logout", jsonOptions({}), 10000); } catch { /* the session is gone anyway */ }
    forget();
    chooseAnonymous(false);
    showLogin();
    await changed();
  });

  $("password-dialog").addEventListener("cancel", event => { if (forced) event.preventDefault(); });
  $("password-cancel").addEventListener("click", () => $("password-dialog").close());
  $("password-form").addEventListener("submit", async event => {
    event.preventDefault();
    const next = $("password-new").value;
    if (next !== $("password-repeat").value) { feedback("password-feedback", "Las contraseñas nuevas no coinciden.", true); return; }
    if (next.length < session.passwordMinLength || !next.trim()) {
      feedback("password-feedback", `La contraseña debe tener al menos ${session.passwordMinLength} caracteres y no ser solo espacios.`, true);
      return;
    }
    $("password-submit").disabled = true;
    try {
      const {data} = await api("/api/v1/auth/password", jsonOptions({current_password: $("password-current").value,
        new_password: next}), 20000);
      remember(data);
      forced = false;
      $("password-dialog").close();
      await changed();
    } catch (error) {
      if (error.status === 401) { $("password-dialog").close(); forced = false; return; }
      feedback("password-feedback", error.message, true);
    } finally { $("password-submit").disabled = false; }
  });

  // --- confirmation and one-time password dialogs ---------------------------------------------------------
  function confirm(title, text, label, action) {
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = label;
    confirmAction = action;
    $("confirm-dialog").showModal();
    $("confirm-cancel").focus();
  }
  $("confirm-cancel").addEventListener("click", () => { confirmAction = null; $("confirm-dialog").close(); });
  $("confirm-ok").addEventListener("click", async () => {
    const action = confirmAction;
    confirmAction = null;
    $("confirm-dialog").close();
    if (action) await action();
  });
  function reveal(user, secret) {
    $("secret-description").textContent = `Contraseña temporal de ${user.display_name || user.username} (${user.username}):`;
    $("secret-value").textContent = secret;
    $("secret-dialog").showModal();
    $("secret-close").focus();
  }
  $("secret-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("secret-value").textContent); $("secret-copy").textContent = "Copiada"; }
    catch { $("secret-copy").textContent = "Selecciónala y cópiala"; }
  });
  $("secret-dialog").addEventListener("close", () => {
    $("secret-value").textContent = "";
    $("secret-copy").textContent = "Copiar";
  });
  $("secret-close").addEventListener("click", () => $("secret-dialog").close());

  // --- users ----------------------------------------------------------------------------------------------
  async function userAction(path, options, done) {
    try {
      const {data} = await api(path, options, 20000);
      if (data.temporary_password) reveal(data.user, data.temporary_password);
      feedback("users-feedback", done);
    } catch (error) { feedback("users-feedback", error.message, true); }
    await loadUsers();
  }
  function userRow(user, stats) {
    const row = el("tr");
    const name = el("td");
    name.append(el("strong", "", user.username));
    if (user.display_name && user.display_name !== user.username) name.append(el("small", "cell-note", user.display_name));
    const status = el("td");
    const locked = user.locked_until && user.locked_until > Date.now() / 1000;
    status.append(el("span", "badge " + (user.status === "active" ? "good" : "neutral"),
      user.status === "active" ? "Activo" : "Desactivado"));
    if (locked) status.append(el("small", "cell-note warning-text", "Bloqueado hasta " + when(user.locked_until)));
    if (user.must_change_pw) status.append(el("small", "cell-note", "Debe cambiar la contraseña"));
    const actions = el("td", "row-actions");
    if (user.manageable) {
      const who = user.display_name || user.username;
      actions.append(button("Restablecer contraseña", "secondary-button", () => confirm("¿Restablecer la contraseña?",
        `${who} recibirá una contraseña temporal y se cerrarán sus sesiones abiertas.`, "Restablecer",
        () => userAction(`/api/v1/admin/users/${encodeURIComponent(user.id)}/reset-password`, jsonOptions({}),
          "Contraseña restablecida."))));
      if (locked) actions.append(button("Desbloquear", "secondary-button", () => userAction(
        `/api/v1/admin/users/${encodeURIComponent(user.id)}/unlock`, jsonOptions({}), "Usuario desbloqueado.")));
      const disabling = user.status === "active";
      actions.append(button(disabling ? "Desactivar" : "Activar", disabling ? "danger-button" : "secondary-button", () => {
        const run = () => userAction(`/api/v1/admin/users/${encodeURIComponent(user.id)}`,
          jsonOptions({status: disabling ? "disabled" : "active"}, "PATCH"), disabling ? "Usuario desactivado." : "Usuario activado.");
        return disabling ? confirm("¿Desactivar este usuario?", `${who} no podrá iniciar sesión y se cerrarán sus sesiones.`,
          "Desactivar", run) : run();
      }));
      if (hasRole("superadmin")) {
        const target = user.role === "admin" ? "user" : "admin";
        actions.append(button(target === "admin" ? "Hacer administrador" : "Quitar administrador", "secondary-button",
          () => confirm("¿Cambiar el rol?", `${who} pasará a ser ${ROLE_NAMES[target]}. Se cerrarán sus sesiones.`,
            "Cambiar rol", () => userAction(`/api/v1/admin/users/${encodeURIComponent(user.id)}/role`,
              jsonOptions({role: target}), "Rol cambiado."))));
      }
    } else {
      actions.append(el("small", "cell-note", user.id === session.user?.id ? "Tu cuenta" : "Sin permisos"));
    }
    const history = el("td");
    const summary = historySummary(stats?.[user.id]);
    history.append(el("span", "", summary.main));
    if (summary.note) history.append(el("small", "cell-note", summary.note));
    row.append(name, el("td", "", ROLE_NAMES[user.role] || user.role), status, el("td", "", user.devices ?? 0),
      history, el("td", "", when(user.last_login_at)), actions);
    return row;
  }
  async function loadUsers() {
    $("new-role-admin").hidden = !hasRole("superadmin");
    $("new-role-admin").disabled = !hasRole("superadmin");
    try {
      const [{data}, stats] = await Promise.all([api("/api/v1/admin/users", {}, 20000),
        // Metadata only; an older backend without it shows "—".
        api("/api/v1/admin/conversations/stats", {}, 20000).then(({data: found}) =>
          Object.fromEntries(found.users.map(item => [item.user_id, item]))).catch(() => null)]);
      $("users-body").replaceChildren(...data.users.map(user => userRow(user, stats)));
      $("users-count").textContent = String(data.users.length);
    } catch (error) { feedback("users-feedback", error.message, true); }
  }
  $("user-create").addEventListener("submit", async event => {
    event.preventDefault();
    const body = {username: $("new-username").value.trim(), role: $("new-role").value};
    if ($("new-display").value.trim()) body.display_name = $("new-display").value.trim();
    $("new-submit").disabled = true;
    try {
      const {data} = await api("/api/v1/admin/users", jsonOptions(body), 20000);
      $("user-create").reset();
      feedback("users-feedback", `Usuario ${data.user.username} creado.`);
      reveal(data.user, data.temporary_password);
    } catch (error) { feedback("users-feedback", error.message, true); }
    finally { $("new-submit").disabled = false; }
    await loadUsers();
  });
  $("users-refresh").addEventListener("click", loadUsers);

  // --- devices and owners ---------------------------------------------------------------------------------
  function ownerSelect(users, current) {
    const select = el("select", "owner-select");
    select.append(Object.assign(el("option", "", "— Sin usuario —"), {value: ""}));
    for (const user of users) {
      const option = el("option", "", `${user.display_name || user.username} (${user.username})`);
      option.value = user.id;
      option.selected = user.id === current;
      select.append(option);
    }
    return select;
  }
  function ownerRow(device, users) {
    const row = el("tr");
    const name = el("td");
    name.append(el("strong", "", device.name || "Sin nombre"));
    name.append(el("small", "cell-note", [device.model, device.variant === "meta" ? "Ray-Ban Meta" : "Celular"]
      .filter(Boolean).join(" · ")));
    const state = el("td");
    state.append(el("span", "", DEVICE_STATES[device.state] || device.state));
    if (device.state === "pending" && device.code) state.append(el("small", "cell-note", "Código " + device.code));
    const owner = el("td");
    const select = ownerSelect(users, device.user_id);
    select.setAttribute("aria-label", "Usuario de " + (device.name || "dispositivo"));
    owner.append(select);
    const actions = el("td", "row-actions");
    const pending = device.state === "pending";
    actions.append(button(pending ? "Aprobar y asignar" : "Guardar", pending ? "primary-button" : "secondary-button",
      async () => {
        if (pending && !select.value) { feedback("owners-feedback", "Elige el usuario antes de aprobar.", true); return; }
        const id = encodeURIComponent(device.id);
        try {
          await api(`/api/v1/admin/devices/${id}/${pending ? "approve" : "assign"}`,
            jsonOptions({user_id: select.value || null}), 20000);
          feedback("owners-feedback", pending ? "Dispositivo aprobado y asignado." : "Asignación guardada.");
        } catch (error) {
          feedback("owners-feedback", error.status === 409 ? "El estado del dispositivo cambió; lista actualizada."
            : error.status === 422 ? "Ese usuario no existe o está desactivado." : error.message, true);
        }
        await loadOwners();
        globalThis.itgDevicesRefresh?.();
      }));
    row.append(name, state, owner, el("td", "", when(device.last_seen)), actions);
    return row;
  }
  async function loadOwners() {
    try {
      const [{data: people}, {data: inventory}] = await Promise.all([
        api("/api/v1/admin/users", {}, 20000), api("/api/v1/admin/devices", {headers: {"X-ITGlasses-Admin": "1"}}, 20000)]);
      const active = people.users.filter(user => user.status === "active");
      const now = Date.now() / 1000;
      const rows = inventory.devices.filter(device => device.state !== "denied"
        && !(device.state === "pending" && device.expires && device.expires <= now));
      $("owners-body").replaceChildren(...rows.map(device => ownerRow(device, active)));
      if (!rows.length) {
        const empty = el("tr");
        const cell = el("td", "empty-cell", "Aún no hay dispositivos aprobados ni pendientes.");
        cell.colSpan = 5;
        empty.append(cell);
        $("owners-body").append(empty);
      }
    } catch (error) { feedback("owners-feedback", error.message, true); }
  }
  $("owners-refresh").addEventListener("click", loadOwners);

  // --- my devices -----------------------------------------------------------------------------------------
  async function loadMine() {
    try {
      const {data} = await api("/api/v1/me/devices", {}, 20000);
      const list = $("mine-list");
      list.replaceChildren();
      if (!data.devices.length) {
        list.append(el("div", "empty-panel", "Todavía no tienes dispositivos asignados. Pídele a un administrador que asigne tu celular o tus lentes a tu cuenta."));
        return;
      }
      for (const device of data.devices) {
        const card = el("article", "document-card");
        card.append(el("h3", "", device.name || "Sin nombre"));
        card.append(el("p", "document-meta", [device.model, device.variant === "meta" ? "Ray-Ban Meta" : "Celular"]
          .filter(Boolean).join(" · ")));
        card.append(el("span", "badge " + (device.state === "active" ? "good" : device.state === "blocked" ? "bad" : "neutral"),
          DEVICE_STATES[device.state] || device.state));
        card.append(el("p", "document-meta", `Aprobado: ${when(device.approved_at)} · Última conexión: ${when(device.last_seen)}`));
        if (device.state !== "blocked") {
          const label = device.name || device.model || "este dispositivo";
          card.append(button("Nueva conversación en este dispositivo", "secondary-button", async () => {
            try {
              const {data: done} = await api(`/api/v1/me/devices/${encodeURIComponent(device.id)}/new-conversation`,
                jsonOptions({}), 20000);
              feedback("mine-feedback", done.closed ? `Listo: el siguiente mensaje desde ${label} empezará una conversación nueva.`
                : `${label} no tenía una conversación abierta; la siguiente ya será nueva.`);
            } catch (error) { feedback("mine-feedback", error.message, true); }
          }));
        }
        list.append(card);
      }
    } catch (error) { feedback("mine-feedback", error.message, true); }
  }
  $("mine-refresh").addEventListener("click", loadMine);

  // --- audit (read only) ----------------------------------------------------------------------------------
  function auditRow(entry) {
    const row = el("tr");
    const [label, style] = OUTCOMES[entry.outcome] || [entry.outcome, "neutral"];
    const result = el("td");
    result.append(el("span", "badge " + style, label));
    row.append(el("td", "", when(entry.at)), el("td", "", actorName(entry)), el("td", "", ACTIONS[entry.action] || entry.action),
      el("td", "", entry.target_type ? `${entry.target_type}` : "—"), el("td", "cell-detail", describeDetail(entry.detail)), result);
    return row;
  }
  async function loadAudit(more) {
    if (!more) { auditBefore = null; $("audit-body").replaceChildren(); }
    const query = new URLSearchParams({limit: "50"});
    if (more && auditBefore) query.set("before_id", String(auditBefore));
    try {
      const {data} = await api(`/api/v1/admin/audit?${query}`, {}, 20000);
      $("audit-scope").textContent = data.complete ? "Vista completa (superadmin): incluye inicios de sesión y bloqueos."
        : "Vista de administración: acciones sobre usuarios, dispositivos, documentos y conversaciones (sin su contenido).";
      $("audit-body").append(...data.entries.map(auditRow));
      auditBefore = data.next_before_id;
      $("audit-more").hidden = !auditBefore;
      feedback("audit-feedback", "");
    } catch (error) { feedback("audit-feedback", error.message, true); }
  }
  $("audit-refresh").addEventListener("click", () => loadAudit(false));
  $("audit-more").addEventListener("click", () => loadAudit(true));

  boot();
  return {opened: page => pages[page]?.(), refresh: applyVisibility};
}
