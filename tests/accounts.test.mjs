import test from "node:test";
import assert from "node:assert/strict";
import {api} from "../frontend/core.mjs";
import {forget, on, remember, session, sessionHeaders} from "../frontend/session.mjs";
import {actorName, allowed, anonymousAllowed, describeDetail} from "../frontend/accounts.mjs";

const USER = {user: {id: "u1", username: "ana", display_name: "Ana", role: "user", must_change_pw: false},
  csrf_token: "a".repeat(64), password_min_length: 8};

test("the CSRF token goes only with changes, and only while logged in", () => {
  forget();
  assert.deepEqual(sessionHeaders("POST"), {});
  remember(USER);
  assert.deepEqual(sessionHeaders("GET"), {});
  for (const method of ["POST", "put", "PATCH", "DELETE"]) assert.deepEqual(sessionHeaders(method), {"X-CSRF-Token": "a".repeat(64)});
  assert.equal(session.passwordMinLength, 8);
  assert.equal(globalThis.itgSession, session);  // devices.js reads it there
  forget();
  assert.deepEqual(sessionHeaders("POST"), {});
});

test("api() sends the session cookie, adds the CSRF token to changes and reports a needed login", async () => {
  const original = globalThis.fetch;
  const seen = [];
  remember(USER);
  const required = [];
  const stop = on("required", code => required.push(code));
  try {
    globalThis.fetch = async (path, options) => { seen.push(options); return new Response('{"ok":true}', {status: 200}); };
    await api("/x", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    await api("/y");
    assert.equal(seen[0].credentials, "same-origin");
    assert.equal(seen[0].headers["X-CSRF-Token"], "a".repeat(64));
    assert.equal(seen[0].headers["Content-Type"], "application/json");
    assert.equal(seen[1].headers["X-CSRF-Token"], undefined);
    for (const code of ["login_required", "not_authenticated", "password_change_required"]) {
      globalThis.fetch = async () => new Response(JSON.stringify({error: {code, message: "x"}}), {status: 401});
      await assert.rejects(api("/z"), error => error.code === code);
    }
    globalThis.fetch = async () => new Response('{"error":{"code":"forbidden_role","message":"x"}}', {status: 403});
    await assert.rejects(api("/z"));
    assert.deepEqual(required, ["login_required", "not_authenticated", "password_change_required"]);
  } finally { globalThis.fetch = original; stop(); forget(); }
});

test("pages are shown by role and loopback mode", () => {
  const admin = {user: {role: "admin"}, loopbackMode: "chat"};
  const user = {user: {role: "user"}, loopbackMode: "legacy"};
  const nobodyChat = {user: null, loopbackMode: "chat"};
  const nobodyLegacy = {user: null, loopbackMode: "legacy"};
  assert.deepEqual([allowed("admin", admin), allowed("documents", admin), allowed("login", admin)], [true, true, true]);
  assert.deepEqual([allowed("admin", user), allowed("documents", user), allowed("login", user)], [false, false, true]);
  assert.deepEqual([allowed("admin", nobodyChat), allowed("documents", nobodyChat)], [false, false]);
  assert.equal(allowed("documents", nobodyLegacy), true);
  assert.equal(anonymousAllowed({loopbackMode: "off"}), false);
  assert.equal(anonymousAllowed(nobodyChat), true);
});

test("audit entries read in Spanish without the technical via field", () => {
  assert.equal(describeDetail({username: "ana", via: "local", owner: null}), "username: ana · owner: —");
  assert.equal(actorName({actor_username: "root"}), "root");
  assert.equal(actorName({actor_kind: "script"}), "Script local");
  assert.equal(actorName({actor_kind: "user", detail: {via: "local"}}), "PC sin sesión");
  assert.equal(actorName({actor_kind: "user", detail: {via: "token"}}), "Token compartido");
});
