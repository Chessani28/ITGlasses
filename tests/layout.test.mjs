import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {layoutMode, nearBottom, grownHeight, WIDE, NARROW, COMPOSER_LINES} from "../frontend/layout.mjs";

test("the sidebar is fixed from 1280 px, an icon rail from 768 px and a drawer below", () => {
  assert.equal(layoutMode(1920), "fixed");
  assert.equal(layoutMode(WIDE), "fixed");
  assert.equal(layoutMode(1366), "fixed");
  assert.equal(layoutMode(1279), "rail");
  assert.equal(layoutMode(1024), "rail");
  assert.equal(layoutMode(NARROW), "rail");
  assert.equal(layoutMode(767), "drawer");
  assert.equal(layoutMode(414), "drawer");
  assert.equal(layoutMode(360), "drawer");
});

test("the chat follows a new message only when the reader is at the end", () => {
  assert.equal(nearBottom({scrollTop: 920, clientHeight: 600, scrollHeight: 1520}), true);
  assert.equal(nearBottom({scrollTop: 860, clientHeight: 600, scrollHeight: 1520}), true);    // within the slack
  assert.equal(nearBottom({scrollTop: 300, clientHeight: 600, scrollHeight: 1520}), false);   // reading above
  assert.equal(nearBottom({scrollTop: 0, clientHeight: 600, scrollHeight: 400}), true);       // nothing to scroll
});

test("the composer grows with its text up to six lines, then scrolls", () => {
  assert.equal(COMPOSER_LINES, 6);
  assert.equal(grownHeight({scrollHeight: 30, lineHeight: 22, paddingY: 8}), 30);
  assert.equal(grownHeight({scrollHeight: 400, lineHeight: 22, paddingY: 8}), 22 * 6 + 8);
  assert.equal(grownHeight({scrollHeight: 400, lineHeight: 21.7, paddingY: 0}), Math.ceil(21.7 * 6));
});

test("the page has the parts the layout script needs and no inline style for the new layout", () => {
  const page = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
  for (const id of ["sidebar", "sidebar-toggle", "sidebar-overlay", "user-menu-button", "user-menu", "new-chat",
    "history-panel", "conversation", "welcome", "suggestions", "chat-form", "message", "send"]) {
    assert.ok(page.includes(`id="${id}"`), id);
  }
  assert.match(page, /id="sidebar-toggle"[^>]*aria-label="Abrir menú"/);
  assert.match(page, /id="sidebar-toggle"[^>]*aria-controls="sidebar"/);
  assert.match(page, /id="user-menu-button"[^>]*aria-expanded="false"/);
  // The account actions live in the user menu.
  const menu = page.slice(page.indexOf('id="user-menu"'), page.indexOf("</div>", page.indexOf('id="user-menu"')));
  for (const id of ["user-login", "user-password", "user-logout"]) assert.ok(menu.includes(`id="${id}"`), id);
});

test("the hero suggestions are complete, real questions", () => {
  const app = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
  for (const question of ["¿Qué es COMPUTER PREPARATION?", "¿Qué es una VPN?", "¿Cuál es la última versión de Windows 11?"]) {
    assert.ok(app.includes(`"${question}"`), question);
  }
});

test("the stylesheet keeps the chat column and never lets the page scroll sideways", () => {
  const css = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");
  assert.match(css, /--column:760px/);
  assert.match(css, /--sidebar:260px/);
  assert.match(css, /minmax\(0,1fr\)/);
  assert.match(css, /@media \(max-width:1279px\)/);
  assert.match(css, /@media \(max-width:767px\)/);
  assert.match(css, /\.conversation \{[^}]*overflow-x:hidden/);
  // The open drawer lists its items left-aligned, as on desktop: only the closed icon rail centres them.
  for (const block of css.match(/@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^}]*\}/g) || []) {
    for (const line of block.split("\n").filter(l => /nav-button|new-chat|user-menu-button/.test(l))) {
      assert.match(line, /body:not\(\.sidebar-open\)/, line);
    }
  }
  for (const old of [".page-footer", ".sidebar-note", ".local-footer", ".chat-toolbar", ".hero-symbol", ".chat-layout"]) {
    assert.ok(!css.includes(old), old);
  }
});
