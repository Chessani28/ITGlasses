import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../frontend/devices.js", import.meta.url), "utf8");
const IDS = ["device-panel", "device-updated", "device-toast", "device-alert", "device-alert-text", "device-retry",
  "device-list", "device-feedback", "device-dialog", "device-dialog-title", "device-dialog-details",
  "device-dialog-consequence", "device-dialog-cancel", "device-dialog-confirm"];
const now = () => Math.floor(Date.now() / 1000);

// --- minimal fake DOM: only what devices.js uses -------------------------------
function makeDocument() {
  const created = [];
  const doc = {hidden: false, activeElement: null, listeners: {}, created};
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.dataset = {};
      this.listeners = {}; this.className = ""; this.hidden = false; this.disabled = false; this.open = false;
      this.own = "";
      const classes = new Set();
      this.classList = {toggle: (name, on) => on ? classes.add(name) : classes.delete(name), contains: name => classes.has(name)};
    }
    get textContent() { return this.own + this.children.map(child => child.textContent).join(""); }
    set textContent(value) { this.children = []; this.own = String(value); }
    append(...items) { for (const item of items) this.children.push(typeof item === "string" ? {textContent: item} : item); }
    replaceChildren(...items) { this.children = []; this.own = ""; this.append(...items); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    emit(type) { return Promise.all((this.listeners[type] || []).map(fn => fn({type}))); }
    click() { return this.emit("click"); }
    focus() { doc.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; return this.emit("close"); }
  }
  const byId = new Map(IDS.map(id => [id, new Element(id === "device-dialog" ? "dialog" : "div")]));
  byId.get("device-alert").hidden = true;
  Object.assign(doc, {
    getElementById: id => byId.get(id) || null,
    createElement: tag => { const node = new Element(tag); created.push(node.tagName); return node; },
    createTextNode: text => ({textContent: String(text)}),
    addEventListener: (type, fn) => { (doc.listeners[type] ||= []).push(fn); },
    emit: type => Promise.all((doc.listeners[type] || []).map(fn => fn({type}))),
  });
  return doc;
}

function walk(node, out = []) {
  if (node && node.tagName) { out.push(node); for (const child of node.children) walk(child, out); }
  return out;
}

async function settle() { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); }

// devices: array or function(call) -> array; server: optional override(path, options) -> response or throw.
async function boot({devices = [], server} = {}) {
  const doc = makeDocument();
  const calls = [];
  const timers = [];
  let list = devices;
  async function fetch(path, options) {
    calls.push({path, ...options});
    const custom = server && await server(path, options, calls);
    if (custom) return custom;
    const rows = typeof list === "function" ? list(calls) : list;
    return {ok: true, status: 200, json: async () => (path.endsWith("/admin/devices") ? {devices: rows} : {ok: true})};
  }
  const context = {
    document: doc, fetch, console, encodeURIComponent,
    setTimeout: (fn, delay) => { const timer = {fn, delay, active: true}; timers.push(timer); return timer; },
    clearTimeout: timer => { if (timer) timer.active = false; },
  };
  vm.runInNewContext(source, context);
  await settle();
  const panel = {
    doc, calls, timers,
    get: id => doc.getElementById(id),
    setDevices: next => { list = next; },
    pending: () => timers.filter(timer => timer.active && timer.delay !== 8000),
    async fire() { const timer = panel.pending().at(-1); timer.active = false; timer.fn(); await settle(); },
    rows: () => walk(doc.getElementById("device-list")).filter(node => node.tagName === "LI"),
    buttons: row => walk(row).filter(node => node.tagName === "BUTTON").map(button => button.textContent),
    button: (row, text) => walk(row).find(node => node.tagName === "BUTTON" && node.textContent === text),
    gets: () => calls.filter(call => call.path === "/api/v1/admin/devices").length,
  };
  return panel;
}

function device(overrides = {}) {
  return {id: "rid-1", variant: "phone", name: "Pixel de Kike", model: "Google Pixel 8", state: "active",
    code: null, created_at: now() - 600, approved_at: now() - 500, last_seen: now() - 120,
    last_ip: "172.16.5.20", expires: now() + 200, ...overrides};
}

// --- tests --------------------------------------------------------------------------

test("lista agrupada con pendientes arriba, código legible, estado, conexión relativa e IP", async () => {
  const panel = await boot({devices: [
    device({id: "a", state: "active"}),
    device({id: "b", state: "blocked", name: "Viejo", last_seen: null, last_ip: null}),
    device({id: "p", state: "pending", code: "123456", name: "Nuevo", approved_at: null}),
  ]});
  const rows = panel.rows();
  assert.equal(rows[0].textContent.includes("Nuevo"), true);
  assert.match(rows[0].textContent, /123 456/);
  assert.match(rows[0].textContent, /Pendiente/);
  assert.match(rows[0].textContent, /Caduca en [0-4]:\d\d/);
  assert.match(rows[1].textContent, /Activo.*hace 2 min.*IP: 172\.16\.5\.20 \(informativa\)/);
  assert.match(rows[2].textContent, /Bloqueado.*Última conexión: nunca/);
  const titles = walk(panel.get("device-list")).filter(node => node.tagName === "H3").map(node => node.textContent);
  assert.deepEqual(titles, ["Pendientes (1)", "Activos (1)", "Bloqueados (1)"]);
  const code = walk(rows[0]).find(node => node.className === "device-code");
  assert.equal(code.getAttribute("aria-label"), "Código 1 2 3 4 5 6");
});

test("nombre y modelo con HTML se muestran como texto literal y no crean elementos", async () => {
  for (const api of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
    assert.equal(source.includes(api), false, `devices.js no debe usar ${api}`);
  }
  const name = "<script>alert(1)</script>";
  const model = "<img src=x onerror=alert(1)>";
  const panel = await boot({devices: [device({state: "pending", code: "654321", name, model})]});
  const [row] = panel.rows();
  assert.equal(walk(row).find(node => node.className === "device-name").textContent, name);
  assert.match(walk(row).find(node => node.className === "device-meta").textContent, /<img src=x onerror=alert\(1\)>/);
  for (const tag of ["SCRIPT", "IMG"]) assert.equal(panel.doc.created.includes(tag), false);
  assert.equal(walk(panel.get("device-list")).some(node => ["SCRIPT", "IMG"].includes(node.tagName)), false);
});

for (const [status, expected] of [
  ["pending", ["Aprobar", "Rechazar", "Eliminar"]],
  ["approved", ["Eliminar"]],
  ["active", ["Bloquear", "Eliminar"]],
  ["blocked", ["Desbloquear", "Eliminar"]],
  ["denied", ["Eliminar"]],
]) {
  test(`botones para el estado ${status}`, async () => {
    const panel = await boot({devices: [device({state: status, code: status === "pending" ? "111222" : null})]});
    assert.deepEqual(panel.buttons(panel.rows()[0]), expected);
  });
}

test("aprobar, rechazar y desbloquear llaman al endpoint correcto sin confirmación", async () => {
  const panel = await boot({devices: [
    device({id: "p1", state: "pending", code: "111111"}),
    device({id: "p2", state: "pending", code: "222222", created_at: now()}),
    device({id: "b1", state: "blocked"}),
  ]});
  const rows = panel.rows();
  await panel.button(rows[0], "Aprobar").click();
  await panel.button(rows[1], "Rechazar").click();
  await panel.button(rows[2], "Desbloquear").click();
  const actions = panel.calls.filter(call => call.method === "POST").map(call => call.path);
  assert.deepEqual(actions, ["/api/v1/admin/devices/p1/approve", "/api/v1/admin/devices/p2/deny",
    "/api/v1/admin/devices/b1/unblock"]);
  assert.equal(panel.get("device-dialog").open, false);
});

test("bloquear pide confirmación mostrando qué dispositivo es", async () => {
  const panel = await boot({devices: [device({id: "a1", name: "Pixel del almacén"})]});
  const row = panel.rows()[0];
  await panel.button(row, "Bloquear").click();
  const dialog = panel.get("device-dialog");
  assert.equal(dialog.open, true);
  assert.match(panel.get("device-dialog-title").textContent, /Bloquear/);
  assert.match(panel.get("device-dialog-details").textContent, /Pixel del almacén.*Google Pixel 8.*172\.16\.5\.20/);
  assert.equal(panel.doc.activeElement, panel.get("device-dialog-cancel"));
  await panel.get("device-dialog-cancel").click();
  await settle();
  assert.equal(dialog.open, false);
  assert.equal(panel.calls.some(call => call.method === "POST"), false);
  await panel.button(panel.rows()[0], "Bloquear").click();
  await panel.get("device-dialog-confirm").click();
  await settle();
  assert.deepEqual(panel.calls.filter(call => call.method === "POST").map(call => call.path),
    ["/api/v1/admin/devices/a1/block"]);
});

test("eliminar pide confirmación y usa DELETE", async () => {
  const panel = await boot({devices: [device({id: "d1", state: "blocked", name: "Tablet"})]});
  await panel.button(panel.rows()[0], "Eliminar").click();
  assert.equal(panel.get("device-dialog").open, true);
  assert.match(panel.get("device-dialog-title").textContent, /Eliminar/);
  assert.match(panel.get("device-dialog-details").textContent, /Tablet/);
  assert.match(panel.get("device-dialog-consequence").textContent, /emparejarse/);
  assert.equal(panel.calls.some(call => call.method === "DELETE"), false);
  await panel.get("device-dialog-confirm").click();
  await settle();
  assert.deepEqual(panel.calls.filter(call => call.method === "DELETE").map(call => call.path),
    ["/api/v1/admin/devices/d1"]);
});

test("avisa 'Dispositivo conectado, guardado' al pasar a activo, no en la carga inicial", async () => {
  const panel = await boot({devices: [device({id: "old", state: "active"}), device({id: "new", state: "approved", name: "Moto G"})]});
  assert.equal(panel.get("device-toast").textContent, "");
  panel.setDevices([device({id: "old", state: "active"}), device({id: "new", state: "active", name: "Moto G"})]);
  await panel.fire();
  assert.equal(panel.get("device-toast").textContent, "Dispositivo conectado, guardado: Moto G");
  panel.get("device-toast").textContent = "";
  panel.setDevices([device({id: "old", state: "blocked"}), device({id: "new", state: "active", name: "Moto G"})]);
  await panel.fire();
  panel.setDevices([device({id: "old", state: "active"}), device({id: "new", state: "active", name: "Moto G"})]);
  await panel.fire();
  assert.equal(panel.get("device-toast").textContent, "", "desbloquear no es una conexión nueva");
});

test("se refresca solo cada 5 s y se pausa con la pestaña oculta", async () => {
  const panel = await boot();
  assert.equal(panel.gets(), 1);
  assert.deepEqual(panel.pending().map(timer => timer.delay), [5000]);
  await panel.fire();
  assert.equal(panel.gets(), 2);
  panel.doc.hidden = true;
  await panel.doc.emit("visibilitychange");
  assert.equal(panel.pending().length, 0);
  panel.doc.hidden = false;
  await panel.doc.emit("visibilitychange");
  await settle();
  assert.equal(panel.gets(), 3);
  assert.equal(panel.pending().length, 1);
});

test("un 403 muestra cómo abrir el panel, sin acciones ni reintentos automáticos", async () => {
  const panel = await boot({devices: [device()], server: async () => ({ok: false, status: 403, json: async () => ({})})});
  assert.equal(panel.get("device-alert").hidden, false);
  assert.equal(panel.get("device-alert-text").textContent, "Abre el panel desde la PC del servidor: http://127.0.0.1:8000");
  assert.equal(panel.rows().length, 0);
  assert.equal(panel.pending().length, 0);
  assert.equal(panel.calls.length, 1);
});

test("si el servidor no responde avisa, reintenta con espera creciente y se recupera", async () => {
  let down = true;
  const panel = await boot({devices: [device()], server: async () => { if (down) throw new TypeError("Failed to fetch"); }});
  assert.equal(panel.get("device-alert").hidden, false);
  assert.match(panel.get("device-alert-text").textContent, /No se puede contactar con el servidor\. Reintentando en 5 s\./);
  assert.equal(panel.pending().at(-1).delay, 5000);
  await panel.fire();
  assert.equal(panel.pending().at(-1).delay, 10000);
  await panel.fire();
  assert.equal(panel.pending().at(-1).delay, 20000);
  await panel.fire();
  assert.equal(panel.pending().at(-1).delay, 30000);
  down = false;
  await panel.fire();
  assert.equal(panel.get("device-alert").hidden, true);
  assert.equal(panel.rows().length, 1);
  assert.equal(panel.pending().at(-1).delay, 5000);
});

test("un 409 en una acción avisa y actualiza la lista", async () => {
  const panel = await boot({
    devices: [device({id: "p1", state: "pending", code: "333444"})],
    server: async (path, options) => options.method === "POST" ? {ok: false, status: 409, json: async () => ({})} : null,
  });
  const before = panel.gets();
  await panel.button(panel.rows()[0], "Aprobar").click();
  await settle();
  assert.equal(panel.get("device-feedback").textContent, "El estado cambió; lista actualizada.");
  assert.equal(panel.gets(), before + 1);
});

test("estado vacío", async () => {
  const panel = await boot();
  assert.match(panel.get("device-list").textContent, /Aún no hay dispositivos/);
  assert.equal(panel.get("device-alert").hidden, true);
});

test("todas las peticiones llevan la cabecera propia y no envían cookies", async () => {
  const panel = await boot({devices: [device({state: "pending", code: "555666"})]});
  await panel.button(panel.rows()[0], "Aprobar").click();
  await panel.button(panel.rows()[0], "Eliminar").click();
  await panel.get("device-dialog-confirm").click();
  await settle();
  assert.ok(panel.calls.length >= 4);
  for (const call of panel.calls) {
    assert.equal(call.headers["X-ITGlasses-Admin"], "1");
    assert.equal(call.credentials, "omit");
    assert.equal(call.redirect, "error");
    assert.equal(Object.keys(call.headers).some(name => name.toLowerCase() === "cookie"), false);
  }
});
