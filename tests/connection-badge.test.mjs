import test from "node:test";
import assert from "node:assert/strict";
import {connectionBadge} from "../frontend/core.mjs";

const healthy = {embedding_available: true, eligible_documents: 5};
const lexicalOnly = {embedding_available: false, eligible_documents: 5};

test("a healthy backend with embeddings is plainly connected", () => {
  assert.deepEqual(connectionBadge(true, healthy), {text: "● Backend conectado", style: "good", title: ""});
});

test("manuals searched by words only are a visible warning, with what to do", () => {
  const badge = connectionBadge(true, lexicalOnly);
  assert.equal(badge.style, "warning");
  assert.equal(badge.text, "⚠ Manuales: solo búsqueda por palabras");
  assert.match(badge.title, /start-ollama\.ps1/);
  assert.match(badge.title, /qwen3-embedding/);
});

test("no warning when there are no authorized manuals to search", () => {
  assert.equal(connectionBadge(true, {embedding_available: false, eligible_documents: 0}).style, "good");
});

test("an unknown knowledge status does not invent a warning", () => {
  assert.equal(connectionBadge(true, null).style, "good");
});

test("an unavailable backend wins over everything", () => {
  assert.deepEqual(connectionBadge(false, lexicalOnly), {text: "Backend no disponible", style: "bad", title: ""});
});
