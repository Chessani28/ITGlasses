import test from "node:test";
import assert from "node:assert/strict";
import {chatTitle, createHistory, HISTORY_KEY, MAX_CHATS, relativeDate, storableMessages} from "../frontend/chat-history.mjs";

function memoryStorage(limit = Infinity) {
  const data = new Map();
  return {
    getItem: key => data.has(key) ? data.get(key) : null,
    setItem: (key, value) => { if (value.length > limit) throw new Error("QuotaExceededError"); data.set(key, value); },
    removeItem: key => data.delete(key),
    data,
  };
}
const clock = (start = 1_000) => { let t = start; return () => ++t; };

test("title is the first user question, trimmed", () => {
  assert.equal(chatTitle([{role: "assistant", text: "Hola"}, {role: "user", text: "  ¿Qué   es DNS?  "}]), "¿Qué es DNS?");
  assert.equal(chatTitle([]), "Nueva conversación");
  const long = chatTitle([{role: "user", text: "a".repeat(100)}]);
  assert.equal(long.length, 60);
  assert.ok(long.endsWith("…"));
});

test("pending messages and one-time options are not stored", () => {
  const stored = storableMessages([
    {role: "user", text: "hola"},
    {role: "assistant", text: "Consultando…", pending: true},
    {role: "assistant", text: "Hola", status: "social", options: [{id: 1}], sources: []},
  ]);
  assert.deepEqual(stored, [{role: "user", text: "hola"}, {role: "assistant", text: "Hola", status: "social"}]);
});

test("save, list newest first, reopen and remove", () => {
  const storage = memoryStorage(), history = createHistory(storage, clock());
  history.save({id: "a", messages: [{role: "user", text: "primero"}], conversation: "s-a"});
  history.save({id: "b", messages: [{role: "user", text: "segundo"}], conversation: "s-b"});
  assert.deepEqual(history.list().map(chat => chat.title), ["segundo", "primero"]);
  assert.equal(history.get("a").conversation, "s-a");
  history.save({id: "a", messages: [{role: "user", text: "primero"}, {role: "assistant", text: "ok"}], conversation: "s-a2"});
  assert.deepEqual(history.list().map(chat => chat.id), ["a", "b"]);
  assert.equal(history.get("a").messages.length, 2);
  assert.equal(history.get("a").conversation, "s-a2");
  history.remove("a");
  assert.deepEqual(history.list().map(chat => chat.id), ["b"]);
  history.clear();
  assert.deepEqual(history.list(), []);
});

test("keeps creation date when a chat is updated", () => {
  const history = createHistory(memoryStorage(), clock(100));
  history.save({id: "a", messages: [{role: "user", text: "x"}]});
  const created = history.get("a").created;
  history.save({id: "a", messages: [{role: "user", text: "x"}, {role: "assistant", text: "y"}]});
  assert.equal(history.get("a").created, created);
  assert.ok(history.get("a").updated > created);
});

test("empty chats are not saved", () => {
  const history = createHistory(memoryStorage());
  assert.equal(history.save({id: "a", messages: [{role: "assistant", text: "…", pending: true}]}), false);
  assert.deepEqual(history.list(), []);
});

test("only the most recent chats are kept", () => {
  const history = createHistory(memoryStorage(), clock());
  for (let i = 0; i < MAX_CHATS + 5; i++) history.save({id: `c${i}`, messages: [{role: "user", text: `chat ${i}`}]});
  const chats = history.list();
  assert.equal(chats.length, MAX_CHATS);
  assert.equal(chats[0].id, `c${MAX_CHATS + 4}`);
});

test("drops oldest chats when the browser runs out of space", () => {
  const storage = memoryStorage(700), history = createHistory(storage, clock());
  for (let i = 0; i < 10; i++) history.save({id: `c${i}`, messages: [{role: "user", text: "x".repeat(80)}]});
  const chats = history.list();
  assert.ok(chats.length > 0 && chats.length < 10);
  assert.equal(chats[0].id, "c9");
});

test("corrupt or missing storage never breaks the page", () => {
  const storage = memoryStorage();
  storage.setItem(HISTORY_KEY, "{no es json");
  assert.deepEqual(createHistory(storage).list(), []);
  assert.deepEqual(createHistory(undefined).list(), []);
  const broken = {getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("SecurityError"); }, removeItem() {}};
  assert.deepEqual(createHistory(broken).list(), []);
  assert.equal(createHistory(broken).save({id: "a", messages: [{role: "user", text: "x"}]}), false);
});

test("relative dates for the chat list", () => {
  const now = new Date(2026, 10, 20, 15, 0).getTime();
  assert.match(relativeDate(new Date(2026, 10, 20, 9, 5).getTime(), now), /^Hoy /);
  assert.match(relativeDate(new Date(2026, 10, 19, 22, 0).getTime(), now), /^Ayer /);
  assert.doesNotMatch(relativeDate(new Date(2026, 9, 1).getTime(), now), /Hoy|Ayer/);
});
