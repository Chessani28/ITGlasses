const panel = document.querySelector("#device-panel");
const form = document.querySelector("#device-login");
const management = document.querySelector("#device-management");
const list = document.querySelector("#pairing-list");
const feedback = document.querySelector("#device-feedback");
let timer;
let lastRows;

async function api(endpoint, body) {
  const options = {cache: "no-store", credentials: "same-origin", redirect: "error"};
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json", "X-ITGlasses-Admin": "1"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/v1/admin/" + endpoint, options);
  if (!response.ok) throw Object.assign(new Error(), {status: response.status});
  return response.json();
}

function session(active) {
  management.hidden = !active;
  form.hidden = active;
  if (!active) {
    list.replaceChildren();
    lastRows = undefined;
  }
}

function report(error) {
  if (error.status === 401) session(false);
  feedback.textContent = error.status === 429 ? "Demasiados intentos. Espera antes de reintentar."
    : error.status === 401 ? "Introduce la contraseña de administración."
    : "No se pudo completar la operación. Comprueba la conexión.";
}

async function refresh() {
  const {devices} = await api("devices");
  session(true);
  const current = JSON.stringify(devices);
  if (current === lastRows) return;
  lastRows = current;
  list.replaceChildren();
  for (const device of devices) {
    const row = document.createElement("li");
    const pending = device.state === "pending";
    const state = pending ? "Pendiente" : device.state === "approved" ? "Aprobado, esperando teléfono" : "Autorizado";
    row.append(document.createTextNode([device.variant, device.code || device.id.slice(0, 8), state].join(" · ") + " "));
    for (const [label, decision] of pending ? [["Aprobar","approve"],["Rechazar","deny"]] : [["Revocar","revoke"]]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.onclick = async () => {
        button.disabled = true;
        try {
          if (decision === "revoke") await api("revoke", {id: device.id});
          else await api("decision", {id: device.id, decision});
          await refresh();
          feedback.textContent = "";
        } catch (error) { report(error); }
        finally { button.disabled = false; }
      };
      row.append(button);
    }
    list.append(row);
  }
  if (!devices.length) list.textContent = "Aún no hay dispositivos.";
}

form.onsubmit = async event => {
  event.preventDefault();
  try {
    await api("login", {password: form.elements.password.value});
    form.reset();
    await refresh();
    feedback.textContent = "";
  } catch (error) { report(error); }
};
document.querySelector("#device-logout").onclick = async () => {
  try { await api("logout", {}); session(false); feedback.textContent = ""; }
  catch (error) { report(error); }
};
panel.ontoggle = async () => {
  clearInterval(timer);
  if (!panel.open) return;
  try { await refresh(); } catch (error) { report(error); }
  if (panel.open) timer = setInterval(() => {
    if (!document.hidden && !management.hidden) refresh().catch(report);
  }, 5000);
};
if (location.protocol === "https:") {
  api("devices").then(() => { panel.hidden = false; })
    .catch(error => { if (error.status === 401) panel.hidden = false; });
}

