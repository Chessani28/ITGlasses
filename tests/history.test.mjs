import test from "node:test";
import assert from "node:assert/strict";
import {channelLabel, describe, graceText, historyNote, listPath, purgeText, titleOf, toChatEntries} from "../frontend/history.mjs";
import {historySummary} from "../frontend/accounts.mjs";
import {badgeText, engineName, engineStatus, FALLBACK_NOTICE} from "../frontend/voice-ui.mjs";

test("the list path carries only the chosen filters", () => {
  assert.equal(listPath(), "/api/v1/conversations?limit=30");
  assert.equal(listPath({channel: "device", deviceId: "dev 1", deleted: true, cursor: "10:abc", limit: 5}),
    "/api/v1/conversations?limit=5&channel=device&device_id=dev+1&deleted=true&cursor=10%3Aabc");
});

test("a conversation is described by channel, date and message count", () => {
  assert.equal(channelLabel({channel: "web"}), "Web");
  assert.equal(channelLabel({channel: "device", device_label: "Z Flip6"}), "Z Flip6");
  assert.equal(channelLabel({channel: "device"}), "Dispositivo");
  assert.match(describe({channel: "web", last_message_at: 1764000000, message_count: 1}), /^Web · .+ · 1 mensaje$/);
  assert.match(describe({channel: "device", device_label: "Ray-Ban Meta", message_count: 4}), /Ray-Ban Meta · — · 4 mensajes/);
  assert.equal(titleOf({title: null}), "Conversación sin título");
});

test("saved messages become chat entries with their route and sources", () => {
  assert.deepEqual(toChatEntries([
    {role: "user", content: "¿Qué es una VPN?"},
    {role: "assistant", content: "Un túnel cifrado.", route: "general", sources: []},
    {role: "assistant", content: "Paso 1", route: "knowledge", sources: [{citation_id: "c1", title: "Manual"}]},
  ]), [
    {role: "user", text: "¿Qué es una VPN?"},
    {role: "assistant", text: "Un túnel cifrado.", status: "general", sources: []},
    {role: "assistant", text: "Paso 1", status: "knowledge", sources: [{citation_id: "c1", title: "Manual"}]},
  ]);
  assert.deepEqual(toChatEntries(null), []);
});

test("the note says whether the conversation is saved", () => {
  assert.match(historyNote({user: null}), /Sin sesión · esta conversación no se guarda/);
  assert.match(historyNote({user: {id: "u"}, saving: false}), /desactivado/);
  assert.match(historyNote({user: {id: "u"}, saving: true, lastSaved: false}), /no se guardó/);
  assert.match(historyNote({user: {id: "u"}, saving: true, lastSaved: true}), /solo tú puedes verla/);
});

test("delete texts follow the grace", () => {
  assert.match(graceText(7), /7 días/);
  assert.match(graceText(1), /1 día;/);
  assert.match(graceText(0), /siguiente limpieza/);
  assert.match(purgeText(1764000000), /^Se eliminará definitivamente el /);
  assert.equal(purgeText(null), "");
});

test("the admin column shows counts and devices, never titles", () => {
  assert.deepEqual(historySummary(null), {main: "—", note: ""});
  assert.deepEqual(historySummary({conversations: 0, deleted_pending: 0}), {main: "Sin conversaciones", note: ""});
  const summary = historySummary({conversations: 3, messages: 12, web: 2, deleted_pending: 1, last_at: 1764000000,
    devices: [{label: "Z Flip6", conversations: 1}]});
  assert.equal(summary.main, "3 conv. · 12 mensajes");
  assert.match(summary.note, /^2 web · Z Flip6 \(1\) · 1 borrada pendiente · última /);
});

test("the voice panel names the engine that really transcribed", () => {
  assert.equal(engineName("whisper-small", {whisper_model: "large-v3-turbo", device: "cuda"}), "Whisper · large-v3-turbo · GPU");
  assert.equal(engineName("whisper-small", {}), "Whisper");
  assert.equal(engineName("vosk"), "Vosk · CPU");
  const engines = {vosk: {available: true}, "whisper-small": {available: true, device: "cuda", model: "large-v3-turbo",
    fallback_available: true, fallback_model: "small"}};
  assert.equal(badgeText("whisper-small", engines), "LOCAL · WHISPER GPU");
  assert.equal(badgeText("vosk", engines), "LOCAL · VOSK CPU");
  assert.equal(badgeText("whisper-small", {vosk: {available: true}}), "LOCAL · VOSK CPU");
  assert.equal(engineStatus(engines),
    "Whisper large-v3-turbo en la GPU, con respaldo small en la CPU. Si no puede usarse en un turno, se usa Vosk y se avisa.");
  assert.equal(engineStatus({"whisper-small": {available: false, reason: "no configurado"}}),
    "Whisper no disponible: no configurado. Se usa Vosk (básico).");
  assert.match(FALLBACK_NOTICE, /usando Vosk, la transcripción puede ser menos precisa/);
});
