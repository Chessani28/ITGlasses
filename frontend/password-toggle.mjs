/** Show / hide password: an eye button in every password field of the page (login, registration, change password)
 * and on the temporary password of the administration dialog. A field hides again when its form is sent or its
 * dialog closes. The button never reads or copies the value. */

export function toggleText(shown) {
  return {type: shown ? "text" : "password", label: shown ? "Ocultar contraseña" : "Mostrar contraseña",
    pressed: String(Boolean(shown))};
}

/** control: {button, show(shown)}; sets the field and the button's accessible state together. */
export function setShown(control, shown) {
  const text = toggleText(shown);
  control.show(shown);
  control.button.setAttribute("aria-pressed", text.pressed);
  control.button.setAttribute("aria-label", text.label);
  control.button.title = text.label;
  control.button.classList?.toggle("on", Boolean(shown));
  control.shown = Boolean(shown);
}

export function hideAll(controls) {
  for (const control of controls) if (control.shown) setShown(control, false);
}

const EYE = ["M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12Z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"];

function eyeButton(doc) {
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "password-eye";
  const NS = "http://www.w3.org/2000/svg", svg = doc.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of EYE) {
    const path = doc.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  const slash = doc.createElementNS(NS, "path");
  slash.setAttribute("d", "M4 4l16 16");
  slash.setAttribute("class", "eye-slash");
  svg.append(slash);
  button.append(svg);
  return button;
}

/** Adds the buttons; returns the controls (for the tests and for hideAll). */
export function initPasswordToggles(doc = document) {
  const controls = [];
  const register = (control, owner) => {
    setShown(control, false);
    control.button.addEventListener("click", () => setShown(control, !control.shown));
    const form = owner.closest("form"), dialog = owner.closest("dialog");
    // Capture phase: the field is hidden before the form's own submit handler runs.
    form?.addEventListener("submit", () => setShown(control, false), true);
    dialog?.addEventListener("close", () => setShown(control, false));
    controls.push(control);
  };
  for (const input of doc.querySelectorAll('input[type="password"]')) {
    const wrap = doc.createElement("span");
    wrap.className = "password-field";
    input.replaceWith(wrap);
    const button = eyeButton(doc);
    wrap.append(input, button);
    register({button, show: shown => { input.type = shown ? "text" : "password"; }}, input);
  }
  // The temporary password of a new or reset user: masked by default, still copied by "Copiar".
  const secret = doc.getElementById("secret-value");
  if (secret) {
    const button = eyeButton(doc);
    secret.after(button);
    button.classList.add("secret-eye");
    register({button, show: shown => secret.classList.toggle("masked", !shown)}, secret);
  }
  return controls;
}
