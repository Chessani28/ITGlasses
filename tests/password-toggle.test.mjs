import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {hideAll, setShown, toggleText} from "../frontend/password-toggle.mjs";

function fake() {
  const attributes = {};
  const input = {type: "password"};
  const control = {button: {setAttribute: (name, value) => { attributes[name] = value; }, title: ""},
    show: shown => { input.type = shown ? "text" : "password"; }};
  return {control, input, attributes};
}

test("the eye says what a click will do and whether the password is visible", () => {
  assert.deepEqual(toggleText(false), {type: "password", label: "Mostrar contraseña", pressed: "false"});
  assert.deepEqual(toggleText(true), {type: "text", label: "Ocultar contraseña", pressed: "true"});
});

test("showing and hiding changes the field and the button together", () => {
  const {control, input, attributes} = fake();
  setShown(control, true);
  assert.equal(input.type, "text");
  assert.equal(attributes["aria-pressed"], "true");
  assert.equal(attributes["aria-label"], "Ocultar contraseña");
  assert.equal(control.button.title, "Ocultar contraseña");
  setShown(control, false);
  assert.equal(input.type, "password");
  assert.equal(attributes["aria-pressed"], "false");
  assert.equal(attributes["aria-label"], "Mostrar contraseña");
});

test("sending the form hides every visible password", () => {
  const a = fake(), b = fake();
  setShown(a.control, true);
  setShown(b.control, false);
  hideAll([a.control, b.control]);
  assert.equal(a.input.type, "password");
  assert.equal(b.input.type, "password");
  const source = readFileSync(new URL("../frontend/password-toggle.mjs", import.meta.url), "utf8");
  assert.match(source, /form\?\.addEventListener\("submit", \(\) => setShown\(control, false\), true\)/);
  assert.match(source, /dialog\?\.addEventListener\("close", \(\) => setShown\(control, false\)\)/);
});

test("every password field of the page and the temporary password get the eye", () => {
  const page = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
  const fields = page.match(/<input [^>]*type="password"/g) || [];
  assert.ok(fields.length >= 4, "login + current, new and repeated password");
  assert.ok(page.includes('id="secret-value"'));
  const app = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
  assert.match(app, /initPasswordToggles\(\)/);
  const css = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.secret-value\.masked \{/);
});
