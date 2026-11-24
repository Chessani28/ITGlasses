import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {goToChat, openInChat, startNewChat} from "../frontend/chat-nav.mjs";

function fakePage(page) {
  const calls = [];
  const deps = {
    page,
    currentPage: () => deps.page,
    showPage: name => { calls.push(`show:${name}`); deps.page = name; },
    closeSidebar: () => calls.push("close"),
    reset: () => calls.push(`reset@${deps.page}`),
    focus: () => calls.push("focus"),
    load: () => calls.push(`load@${deps.page}`),
  };
  return {deps, calls};
}

test("Nueva conversación from another page lands on an empty chat and closes the drawer", () => {
  for (const page of ["documents", "system", "users", "owners", "audit", "my-devices"]) {
    const {deps, calls} = fakePage(page);
    startNewChat(deps);
    assert.equal(deps.page, "chat", page);
    assert.deepEqual(calls, ["show:chat", "close", "reset@chat", "focus"], page);
  }
});

test("Nueva conversación on the chat page only empties it", () => {
  const {deps, calls} = fakePage("chat");
  startNewChat(deps);
  assert.deepEqual(calls, ["close", "reset@chat", "focus"]);
});

test("a conversation picked in the history from another page opens on the chat page", () => {
  const {deps, calls} = fakePage("documents");
  openInChat(deps);
  assert.deepEqual(calls, ["show:chat", "close", "load@chat"]);
  const same = fakePage("chat");
  goToChat(same.deps);
  assert.deepEqual(same.calls, ["close"]);
});

test("app.js uses these for the button and the history", () => {
  const app = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
  assert.match(app, /\$\("new-chat"\)\.addEventListener\("click", \(\) => startNewChat\(/);
  assert.match(app, /openInChat\(/);
});
