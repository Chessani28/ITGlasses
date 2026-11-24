import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {barVisible, micLabel} from "../frontend/voice-dock.mjs";

const page = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

test("the voice bar shows while the voice works, waits for a confirmation, or failed", () => {
  assert.equal(barVisible(), false);
  assert.equal(barVisible({phase: "idle"}), false);
  assert.equal(barVisible({phase: "listening", active: true}), true);
  assert.equal(barVisible({phase: "speaking", active: true}), true);
  assert.equal(barVisible({phase: "idle", confirming: true}), true);
  assert.equal(barVisible({phase: "error"}), true);
  assert.equal(barVisible({phase: "error", dismissed: true}), false);
  assert.equal(barVisible({phase: "error", active: true, dismissed: true}), true);
});

test("the mic button says what a click will do", () => {
  assert.equal(micLabel(false), "Activar micrófono");
  assert.equal(micLabel(true), "Detener micrófono");
  assert.match(page, /id="voice-mic-button"[^>]*aria-label="Activar micrófono"[^>]*aria-pressed="false"/);
});

test("the mic and send buttons are inside the composer, the voice bar right above it", () => {
  const form = page.slice(page.indexOf('<form id="chat-form"'), page.indexOf("</form>", page.indexOf('<form id="chat-form"')));
  assert.ok(form.includes('id="voice-mic-button"') && form.includes('id="send"'));
  const area = page.slice(page.indexOf('<div class="composer-area">'), page.indexOf('<form id="chat-form"'));
  for (const id of ["voice-bar", "voice-state", "voice-transcript", "voice-engine-active", "voice-stop", "voice-transcript-confirmation"]) {
    assert.ok(area.includes(`id="${id}"`), id);
  }
});

test("preferences and diagnostic mode live in the settings dialog; the big panel is gone", () => {
  const dialog = page.slice(page.indexOf('<dialog id="voice-settings"'), page.indexOf("</dialog>", page.indexOf('<dialog id="voice-settings"')));
  for (const id of ["voice-preferences", "voice-engine", "voice-diagnostic-toggle", "voice-diagnostic-section", "voice-badge"]) {
    assert.ok(dialog.includes(`id="${id}"`), id);
  }
  assert.ok(!page.includes('class="voice-panel"'));
  assert.match(page, /id="voice-settings-open"[^>]*aria-label="Ajustes de voz"/);
  assert.match(page, /<audio id="voice-audio"[^>]*hidden/);       // only shown in diagnostic mode
  assert.ok(!/\sstyle="/.test(page), "no inline style: CSP style-src 'self'");
});
