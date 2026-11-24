/** The voice in the composer: the mic button starts and stops the same voice controller (voice-ui.mjs, unchanged),
 * a small bar above the composer shows its state while it works, and the gear opens the voice settings
 * (preferences and diagnostic mode). This module only reads and clicks the elements voice-ui.mjs already uses. */

/** The bar is shown while the voice works, while a transcript waits for confirmation, and after an error. */
export function barVisible({phase = "idle", active = false, confirming = false, dismissed = false} = {}) {
  if (active || confirming) return true;
  return phase === "error" && !dismissed;
}
export function micLabel(active) {
  return active ? "Detener micrófono" : "Activar micrófono";
}

export function initVoiceDock() {
  const $ = id => document.getElementById(id);
  const mic = $("voice-mic-button"), bar = $("voice-bar"), state = $("voice-state");
  let dismissed = false;

  const active = () => !$("voice-stop").disabled;
  function update() {
    const on = active();
    mic.setAttribute("aria-pressed", String(on));
    mic.setAttribute("aria-label", micLabel(on));
    mic.title = micLabel(on);
    mic.classList.toggle("on", on);
    bar.dataset.state = state.dataset.state || "idle";
    bar.hidden = !barVisible({phase: state.dataset.state, active: on,
      confirming: !$("voice-transcript-confirmation").hidden, dismissed});
  }
  mic.addEventListener("click", () => {
    if (active()) { $("voice-stop").click(); return; }
    dismissed = false;
    $("voice-start").click();
  });
  $("voice-bar-close").addEventListener("click", () => { dismissed = true; update(); mic.focus(); });
  new MutationObserver(update).observe(state, {attributes: true, attributeFilter: ["data-state"]});
  new MutationObserver(update).observe($("voice-stop"), {attributes: true, attributeFilter: ["disabled"]});
  new MutationObserver(update).observe($("voice-transcript-confirmation"), {attributes: true, attributeFilter: ["hidden"]});

  // The audio player of the answers is only visible in diagnostic mode (it still plays when hidden).
  const diagnostic = $("voice-diagnostic-toggle");
  diagnostic.addEventListener("change", () => { $("voice-audio").hidden = !diagnostic.checked; });

  $("voice-settings-open").addEventListener("click", () => $("voice-settings").showModal());
  $("voice-settings-close").addEventListener("click", () => $("voice-settings").close());
  update();
}
