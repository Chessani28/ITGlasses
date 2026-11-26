/* 👍/👎 on the assistant's answers (2026-11-26). 👍 is only counted; 👎 shares that question and answer (and the
 * optional comment) with the administrators, so the page says it before sending. Pure helpers here; app.js draws them. */

export const SHARE_NOTICE = "Al enviarlo, esta pregunta y respuesta se comparten con el administrador para mejorar el asistente.";
export const COMMENT_MAX = 500;

/** The endpoint of an answer's vote, or null when the answer was not saved (no conversation or no message id). */
export function feedbackPath(conversation, messageId) {
  if (!conversation || !Number.isInteger(messageId) || messageId < 1) return null;
  return `/api/v1/conversations/${encodeURIComponent(conversation)}/messages/${messageId}/feedback`;
}

/** The vote a click leaves: the same button again removes it (0). */
export function nextVote(current, clicked) {
  return current === clicked ? 0 : clicked;
}

/** The request body: the comment only with 👎, trimmed and cut to COMMENT_MAX. */
export function voteBody(value, comment) {
  const text = value === -1 ? String(comment || "").trim().slice(0, COMMENT_MAX) : "";
  return text ? {value, comment: text} : {value};
}

/** What the row says after a vote. */
export function voteStatus(value) {
  if (value === 1) return "Gracias, nos ayuda a saber qué funciona.";
  if (value === -1) return "Gracias, lo revisaremos para mejorar.";
  return "";
}

/** The admin card: "85 % útiles (17 de 20)" or the empty text. */
export function usefulText(summary) {
  if (!summary || !summary.total) return "Todavía no hay calificaciones.";
  return `${summary.useful_percent} % útiles (${summary.up} de ${summary.total})`;
}
