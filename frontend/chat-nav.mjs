/** Going back to the chat from any page: "Nueva conversación" and a conversation picked in the history both land on
 * Conversaciones, and the floating sidebar (drawer on a phone, icon rail) closes. The page functions come from
 * app.js, so this stays testable without a page. */

/** deps: {currentPage(), showPage(name), closeSidebar()} */
export function goToChat(deps) {
  if (deps.currentPage() !== "chat") deps.showPage("chat");
  deps.closeSidebar();
}

/** deps: goToChat's plus {reset(), focus()}: an empty new conversation on the chat page. */
export function startNewChat(deps) {
  goToChat(deps);
  deps.reset();
  deps.focus?.();
}

/** deps: goToChat's plus {load()}: the chosen conversation, shown on the chat page. */
export function openInChat(deps) {
  goToChat(deps);
  deps.load();
}
