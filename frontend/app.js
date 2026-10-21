import {api, chatPayload, documentState, elapsed, jsonOptions, limits, sourcePath, validateFile} from "./core.mjs";

import {initVoice} from "./voice-ui.mjs";
let voiceActive = false;
const $ = id => document.getElementById(id);
const state = {mode: "knowledge", busy: false, documentBusy: false, updating: null, withdrawing: null,
  sourceRequest: 0, chats: {knowledge: {messages: [], conversation: null}, general: {messages: [], conversation: null}}};
const welcome = $("welcome");
const modeText = {
  knowledge: {eyebrow: "CONOCIMIENTO CON EVIDENCIA", title: "Tu documentación.", subtitle: "Respuestas con respaldo.",
    description: "Encuentra procedimientos, resuelve dudas y consulta las fuentes. Todo desde tu equipo, con tus documentos autorizados.",
    context: "Conocimiento · contexto temporal del backend. No se guarda al recargar.",
    prompt: "Describe lo que necesitas encontrar…", label: "Documentación autorizada"},
  general: {eyebrow: "CONVERSACIÓN LOCAL", title: "Una pregunta.", subtitle: "Un punto de partida.",
    description: "Explora ideas y conceptos con el proveedor configurado. Estas respuestas no se verifican contra la documentación de tu empresa.",
    context: "General · cada petición es independiente. El historial es solo visual y se pierde al recargar.",
    prompt: "Escribe una pregunta para el asistente…", label: "Conocimiento general · sin fuentes"}
};
function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}
function action(text, className, handler) {
  const button = node("button", className, text);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}
function feedback(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", error);
}
function errorText(error) {
  const suffix = error.code ? ` [${error.code}]` : "";
  return `${error.message}${suffix}${error.ms !== undefined ? ` · ${elapsed(error.ms)}` : ""}`;
}
function updateControls() {
  $("send").disabled = state.busy || state.documentBusy;
  $("new-chat").disabled = state.busy || voiceActive;
  document.querySelectorAll("[data-mode]").forEach(button => button.disabled = state.busy || voiceActive);
  $("upload-submit").disabled = state.documentBusy || state.busy;
  $("cancel-update").disabled = state.documentBusy;
  document.querySelectorAll(".document-actions button").forEach(button => {
    button.disabled = state.documentBusy || state.busy || button.dataset.withdrawn === "true";
  });
  document.querySelectorAll(".option-button").forEach(button => button.disabled = state.busy || state.documentBusy || button.dataset.stale === "true");
}
function updateCounter() { $("character-count").textContent = `${$("message").value.length} / ${limits[state.mode]}`; }
function setMode(mode) {
  if (state.busy) return;
  state.mode = mode;
  const text = modeText[mode];
  document.querySelectorAll("[data-mode]").forEach(button => {
    const selected = button.dataset.mode === mode;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("message").maxLength = limits[mode];
  $("message").placeholder = text.prompt;
  $("context-note").textContent = text.context;
  $("composer-mode").textContent = text.label;
  $("welcome-eyebrow").textContent = text.eyebrow;
  $("welcome-title").replaceChildren(document.createTextNode(text.title), node("br"), node("em", "", text.subtitle));
  $("welcome-description").textContent = text.description;
  const prompts = mode === "knowledge" ? [
    ["Busca un procedimiento", "¿Cuál es el procedimiento 24?"],
    ["Descríbelo con tus palabras", "Necesito instalar Windows desde cero con una USB. ¿Cómo se llama ese proceso?"]
  ] : [["Entiende un concepto", "Explica brevemente qué es DNS."], ["Compara dos conceptos", "¿Cuál es la diferencia entre RAM y almacenamiento?"]];
  $("suggestions").replaceChildren(...prompts.map(([label, prompt]) => {
    const button = action("", "", () => { $("message").value = prompt; updateCounter(); $("message").focus(); });
    button.append(node("span", "", "↗"), node("strong", "", label), node("small", "", prompt));
    return button;
  }));
  updateCounter();
  renderChat();
}
function renderChat() {
  const messages = state.chats[state.mode].messages;
  if (!messages.length) { $("conversation").replaceChildren(welcome); return; }
  $("conversation").replaceChildren(...messages.map(entry => {
    const article = node("article", `message ${entry.role}${entry.error ? " error" : ""}`);
    const body = node("div", "message-body");
    const heading = node("div", "message-label", entry.role === "user" ? "Tú" : "ITGlasses");
    if (entry.ms !== undefined) heading.append(node("span", "message-time", elapsed(entry.ms)));
    if (entry.status) heading.append(node("span", "badge neutral", ({
      answered: "Respuesta documentada", clarification_required: "Necesita aclaración", conflict: "Versiones en conflicto",
      insufficient_evidence: "Evidencia insuficiente"
    })[entry.status] || entry.status));
    const text = node("div", "message-text", entry.text);
    if (entry.pending) text.prepend(node("span", "loading-dot"));
    body.append(heading, text);
    if (entry.sources?.length) {
      const citations = node("div", "citation-list");
      entry.sources.forEach(source => citations.append(action(
        `▤ ${source.title || source.filename || "Fuente"}${source.page ? ` · p. ${source.page}` : ""}`,
        "citation", () => inspectSource(source.citation_id))));
      body.append(citations);
    }
    if (entry.options?.length && ["clarification_required", "conflict"].includes(entry.status)) {
      const options = node("div", "option-list");
      entry.options.forEach(option => {
        const button = action("", "option-button", () => {
          if (entry.conversation !== state.chats.knowledge.conversation) return;
          // Preserve the prior intent and avoid re-parsing an ID in the title.
          sendMessage("La opción seleccionada.", option.id);
        });
        button.append(node("span", "", option.title), node("small", "",
          `${option.scope} · ID ${option.procedure_id || "—"} · versión ${option.version}`));
        // Old choices cannot be reused after a newer reply.
        button.disabled = entry !== messages[messages.length - 1];
        button.dataset.stale = String(button.disabled);
        options.append(button);
      });
      body.append(options);
    }
    article.append(node("div", "avatar", entry.role === "user" ? "TÚ" : "it"), body);
    return article;
  }));
  updateControls();
  document.querySelectorAll('.option-button[data-stale="true"]').forEach(button => button.disabled = true);
}
async function sendMessage(message, choiceId, signal) {
  if (state.busy || state.documentBusy) { if (signal) throw new Error("Hay otra operación en curso."); return; }
  let result;
  const mode = state.mode, chat = state.chats[mode];
  let payload;
  try { payload = chatPayload(mode, message, chat.conversation, choiceId); }
  catch (error) { $("message").setCustomValidity(error.message); $("message").reportValidity(); return; }
  $("message").setCustomValidity("");
  chat.messages.push({role: "user", text: payload.message});
  const pending = {role: "assistant", text: "Consultando el asistente local…", pending: true};
  chat.messages.push(pending);
  state.busy = true;
  $("message").value = ""; updateCounter(); renderChat(); updateControls();
  pendingScroll();
  try {
    const {data, ms} = await api(mode === "knowledge" ? "/api/v1/knowledge/chat" : "/api/v1/chat", {...jsonOptions(payload), signal}, 300000);
    result = data;
    if (typeof data.answer !== "string") throw new Error("El backend no devolvió una respuesta de chat válida.");
    if (mode === "knowledge") chat.conversation = data.conversation_id || null;
    Object.assign(pending, {text: data.answer, ms, status: data.status, sources: mode === "knowledge" ? data.sources : [],
      options: data.options, conversation: chat.conversation});
  } catch (error) {
    if (error.code === "conversation_expired") {
      chat.conversation = null;
      error.message = "La conversación del servidor expiró. La siguiente pregunta iniciará una nueva; también puedes pulsar «Nueva conversación».";
    }
    Object.assign(pending, {text: errorText(error), error: true, ms: error.ms});
    if (signal) throw error;
  } finally {
    pending.pending = false; state.busy = false; renderChat(); updateControls(); pendingScroll();
  }
  return result;
}
function pendingScroll() { $("chat-form").scrollIntoView({block: "nearest"}); }
async function inspectSource(id) {
  const request = ++state.sourceRequest;
  $("source-title").textContent = "Consultando fuente";
  $("source-meta").textContent = "";
  $("source-text").textContent = "";
  feedback("source-feedback", "Comprobando que la fuente siga autorizada y vigente…");
  $("source-dialog").showModal();
  try {
    const {data, ms} = await api(sourcePath(id), {}, 20000);
    if (request !== state.sourceRequest) return;
    $("source-title").textContent = data.title || "Fuente";
    $("source-meta").textContent = `${data.filename} · versión ${data.version} · revisión ${data.revision}${data.page ? ` · página ${data.page}` : ""}\n${data.section || ""}\nSHA-256: ${data.sha256}`;
    $("source-text").textContent = data.quote || "";
    feedback("source-feedback", `Fuente verificada ahora · ${elapsed(ms)}`);
  } catch (error) {
    if (request !== state.sourceRequest) return;
    $("source-title").textContent = "Fuente no disponible";
    feedback("source-feedback", errorText(error), true);
  }
}
function closeSource() {
  state.sourceRequest++;
  $("source-dialog").close();
  $("source-text").textContent = "";
}
async function refreshStatus() {
  $("refresh-status").disabled = true;
  const results = await Promise.allSettled([
    api("/health", {}, 12000), api("/api/v1/status", {}, 15000), api("/api/v1/knowledge/status", {}, 15000)
  ]);
  const [health, provider, knowledge] = results;
  const online = health.status === "fulfilled" && health.value.data.status === "ok";
  $("connection").textContent = online ? "● Backend conectado" : "Backend no disponible";
  $("connection").className = `badge ${online ? "good" : "bad"}`;
  $("backend-status").textContent = online ? `Disponible · ${elapsed(health.value.ms)}` : "No disponible";
  $("provider-status").textContent = provider.status === "fulfilled" ?
    `${provider.value.data.provider} · ${provider.value.data.status === "ready" ? "disponible" : "no disponible"} · ${elapsed(provider.value.ms)}` : "No se pudo consultar";
  $("provider-model").textContent = provider.status === "fulfilled" ? provider.value.data.model : "Modelo no informado";
  $("provider-note").textContent = provider.status !== "fulfilled" ? "No se pudo verificar el proveedor activo." :
    provider.value.data.mock ? "El chat general utiliza MockLLM. Este estado no verifica la disponibilidad del modelo de respuestas de Ollama." :
    "El estado del proveedor indica la disponibilidad que reporta el backend; no constituye una prueba de inferencia.";
  if (knowledge.status === "fulfilled") {
    const {data, ms} = knowledge.value;
    $("knowledge-status").textContent = `Embeddings ${data.embedding_available ? "disponibles" : "no disponibles"} · ${data.eligible_documents} documentos autorizados · ${elapsed(ms)}`;
    $("embedding-model").textContent = `${data.embedding_model} · ${data.embedding_device}`;
    $("document-count").textContent = data.eligible_documents;
  } else {
    $("knowledge-status").textContent = "No se pudo consultar";
    $("embedding-model").textContent = "Disponibilidad desconocida";
    $("document-count").textContent = "—";
  }
  $("status-updated").textContent = `Última comprobación: ${new Date().toLocaleTimeString("es-MX")}. Actualización manual.`;
  $("refresh-status").disabled = false;
}
async function refreshDocuments() {
  $("refresh-documents").disabled = true;
  feedback("documents-feedback", "Consultando documentos…");
  try {
    const {data, ms} = await api("/api/v1/documents", {}, 20000);
    $("list-count").textContent = data.documents.length;
    $("document-count").textContent = data.documents.filter(doc => doc.authorized && !doc.withdrawn).length;
    $("document-list").replaceChildren(...data.documents.map(renderDocument));
    if (!data.documents.length) $("document-list").append(node("div", "empty-panel", "Aún no hay documentos. Sube uno y autorízalo para comenzar."));
    feedback("documents-feedback", `Lista actualizada · ${elapsed(ms)}`);
  } catch (error) { feedback("documents-feedback", errorText(error), true); }
  finally { $("refresh-documents").disabled = false; updateControls(); }
}
function renderDocument(doc) {
  const card = node("article", "document-card"), heading = node("div", "document-heading");
  const label = documentState(doc);
  heading.append(node("h3", "", doc.title), node("span", `badge ${label.style}`, label.label));
  card.append(heading, node("p", "document-meta",
    `${doc.filename} · ${doc.scope} · ID ${doc.procedure_id || "—"} · versión ${doc.version} · revisión ${doc.current_revision}\nClave: ${doc.procedure_key}\nUUID: ${doc.id}`));
  const buttons = node("div", "document-actions");
  buttons.append(action("Nueva versión", "secondary-button", () => startUpdate(doc)),
    action("Reindexar", "secondary-button", () => mutateDocument(doc, "reindex")),
    action(doc.authorized ? "Revocar autorización" : "Autorizar", "secondary-button", () => mutateDocument(doc, "authorization")),
    action("Retirar", "danger-button", () => {
      state.withdrawing = doc;
      $("withdraw-description").textContent = `Documento: ${doc.title} · versión ${doc.version}`;
      $("withdraw-dialog").showModal();
    }));
  buttons.querySelectorAll("button").forEach(button => button.dataset.withdrawn = String(Boolean(doc.withdrawn)));
  card.append(buttons);
  return card;
}
function startUpdate(doc) {
  state.updating = doc.id;
  $("upload-title").textContent = "Nueva versión del documento";
  $("cancel-update").hidden = false;
  $("document-title").value = doc.title;
  $("document-version").value = doc.version;
  $("document-scope").value = doc.scope;
  $("document-procedure").value = doc.procedure_id || "";
  $("document-key").value = doc.procedure_key || "";
  $("document-authorized").checked = Boolean(doc.authorized);
  $("document-file").value = "";
  feedback("upload-feedback", "Selecciona el archivo nuevo y ajusta la versión. Se conservará el original.");
  $("upload-form").scrollIntoView({block: "start"});
  $("document-version").focus();
}
function resetUpload() {
  state.updating = null; $("upload-form").reset();
  $("upload-title").textContent = "Añadir documento"; $("cancel-update").hidden = true;
}
async function upload(event) {
  event.preventDefault();
  if (state.documentBusy || state.busy) return;
  const file = $("document-file").files[0];
  try { validateFile(file); }
  catch (error) { feedback("upload-feedback", error.message, true); return; }
  const metadata = {title: $("document-title").value.trim(), version: $("document-version").value.trim(),
    scope: $("document-scope").value.trim(), authorized: $("document-authorized").checked};
  if ($("document-procedure").value.trim()) metadata.procedure_id = $("document-procedure").value.trim();
  if ($("document-key").value.trim()) metadata.procedure_key = $("document-key").value.trim();
  if (!metadata.title || !metadata.version || !metadata.scope) { feedback("upload-feedback", "Título, versión y ámbito no pueden estar vacíos.", true); return; }
  const form = new FormData(); form.append("file", file); form.append("metadata", JSON.stringify(metadata));
  state.documentBusy = true; updateControls();
  feedback("upload-feedback", "Subiendo e indexando… Puede tardar mientras se generan los embeddings.");
  try {
    const {data, ms} = await api(`/api/v1/documents${state.updating ? `/${encodeURIComponent(state.updating)}` : ""}`,
      {method: state.updating ? "PUT" : "POST", body: form}, 600000);
    resetUpload();
    feedback("upload-feedback", `Documento indexado · ${data.chunk_count} fragmentos · ${elapsed(ms)}${data.authorized ? "" : ". Pendiente de autorización para consultas."}`);
    await refreshDocuments(); await refreshStatus();
  } catch (error) { feedback("upload-feedback", errorText(error), true); }
  finally { state.documentBusy = false; updateControls(); }
}
async function mutateDocument(doc, operation) {
  if (state.documentBusy || state.busy) return;
  state.documentBusy = true; updateControls();
  feedback("documents-feedback", operation === "reindex" ? "Reindexando el documento…" : "Actualizando el documento…");
  try {
    const options = operation === "authorization" ? jsonOptions({authorized: !doc.authorized}, "PATCH") : {method: "POST"};
    const {ms} = await api(`/api/v1/documents/${encodeURIComponent(doc.id)}/${operation}`, options, 600000);
    // Never keep an already opened passage visible after a local mutation.
    closeSource();
    await refreshDocuments(); await refreshStatus();
    feedback("documents-feedback", `Operación completada · ${elapsed(ms)}`);
  } catch (error) { feedback("documents-feedback", errorText(error), true); }
  finally { state.documentBusy = false; updateControls(); }
}
function showPage(page) {
  voice.stop();
  for (const name of ["chat", "documents", "system"]) $(`${name}-page`).hidden = name !== page;
  document.querySelectorAll("[data-page]").forEach(button => {
    button.classList.toggle("active", button.dataset.page === page);
    if (button.dataset.page === page) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("page-title").textContent = {chat: "Conversaciones", documents: "Documentos", system: "Estado del sistema"}[page];
  if (page === "documents") refreshDocuments();
  if (page === "system") refreshStatus();
}
document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
$("chat-form").addEventListener("submit", event => { event.preventDefault(); sendMessage($("message").value); });
$("message").addEventListener("input", () => { $("message").setCustomValidity(""); updateCounter(); });
$("message").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("chat-form").requestSubmit(); }
});
$("new-chat").addEventListener("click", () => {
  state.chats[state.mode] = {messages: [], conversation: null}; renderChat(); $("message").focus();
});
$("refresh-status").addEventListener("click", refreshStatus);
$("refresh-documents").addEventListener("click", refreshDocuments);
$("upload-form").addEventListener("submit", upload);
$("cancel-update").addEventListener("click", () => { resetUpload(); feedback("upload-feedback", ""); });
$("document-file").addEventListener("change", () => {
  try {
    const file = $("document-file").files[0]; validateFile(file);
    if (!$("document-title").value.trim()) $("document-title").value = file.name.replace(/\.[^.]+$/, "").slice(0, 200);
    feedback("upload-feedback", `Archivo seleccionado: ${file.name} · ${(file.size / 1024).toFixed(1)} KiB`);
  } catch (error) { feedback("upload-feedback", error.message, true); }
});
$("close-source").addEventListener("click", closeSource);
$("source-dialog").addEventListener("cancel", () => { state.sourceRequest++; $("source-text").textContent = ""; });
$("cancel-withdraw").addEventListener("click", () => { state.withdrawing = null; $("withdraw-dialog").close(); });
$("confirm-withdraw").addEventListener("click", () => {
  const doc = state.withdrawing; state.withdrawing = null; $("withdraw-dialog").close();
  if (doc) mutateDocument(doc, "withdraw");
});
const voice = initVoice({send: (text, signal) => sendMessage(text, undefined, signal), lock: active => {voiceActive = active; updateControls();}});
setMode("knowledge");
refreshStatus();
