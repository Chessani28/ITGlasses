/** The page layout: the sidebar (fixed, icon rail or drawer by width), Esc, the overlay, the composer that grows and
 * the chat that follows the newest message. The pure helpers are exported for the tests. */

export const WIDE = 1280;     // from here the sidebar is fixed
export const NARROW = 768;    // from here an icon rail; below it a drawer
export const COMPOSER_LINES = 6;

/** "fixed" (≥1280), "rail" (768–1279) or "drawer" (<768). */
export function layoutMode(width) {
  return width >= WIDE ? "fixed" : width >= NARROW ? "rail" : "drawer";
}

/** Whether the reader is at the end of the chat (then a new message keeps the view at the end). */
export function nearBottom({scrollTop, clientHeight, scrollHeight}, slack = 80) {
  return scrollHeight - scrollTop - clientHeight <= slack;
}

/** Height of the composer textarea: its content, up to COMPOSER_LINES lines (then it scrolls). */
export function grownHeight({scrollHeight, lineHeight, paddingY = 0, maxLines = COMPOSER_LINES}) {
  return Math.min(scrollHeight, Math.ceil(lineHeight * maxLines + paddingY));
}

export function initLayout() {
  const $ = id => document.getElementById(id);
  const body = document.body, sidebar = $("sidebar"), toggle = $("sidebar-toggle"), overlay = $("sidebar-overlay");
  let returnFocus = null;

  const isOpen = () => body.classList.contains("sidebar-open");
  function open() {
    if (layoutMode(window.innerWidth) === "fixed" || isOpen()) return;
    returnFocus = document.activeElement;
    body.classList.add("sidebar-open");
    overlay.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Cerrar menú");
    sidebar.querySelector("button:not([hidden]):not(:disabled), a")?.focus();
  }
  function close(restoreFocus = true) {
    if (!isOpen()) return;
    body.classList.remove("sidebar-open");
    overlay.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Abrir menú");
    if (restoreFocus) (returnFocus?.isConnected ? returnFocus : toggle).focus();
  }
  toggle.addEventListener("click", () => (isOpen() ? close() : open()));
  overlay.addEventListener("click", () => close());
  // The user menu (Cambiar contraseña, Cerrar sesión): opens from the user button, closes on a choice, a click
  // outside or Esc.
  const menuButton = $("user-menu-button"), menu = $("user-menu");
  function menuOpen(show) {
    menu.hidden = !show;
    menuButton.setAttribute("aria-expanded", String(show));
    if (show) menu.querySelector("button:not([hidden])")?.focus();
  }
  menuButton.addEventListener("click", () => menuOpen(menu.hidden));
  menu.addEventListener("click", event => { if (event.target.closest("button")) menuOpen(false); });
  document.addEventListener("click", event => {
    if (!menu.hidden && !event.target.closest("#user-box")) menuOpen(false);
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;  // a dialog closes itself
    if (!menu.hidden) { menuOpen(false); menuButton.focus(); }
    else if (isOpen()) close();
  });
  window.matchMedia(`(min-width: ${WIDE}px)`).addEventListener("change", event => { if (event.matches) close(false); });

  /** The composer grows with its text up to COMPOSER_LINES lines. */
  function grow(textarea) {
    const style = getComputedStyle(textarea);
    textarea.style.height = "auto";
    textarea.style.height = grownHeight({scrollHeight: textarea.scrollHeight,
      lineHeight: parseFloat(style.lineHeight) || 20,
      paddingY: (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)}) + "px";
  }

  return {open, close, isOpen, grow,
    /** After choosing something in a floating sidebar (rail or drawer), it closes. */
    picked() { if (layoutMode(window.innerWidth) !== "fixed") close(false); }};
}
