/** Login session shared by every page script (stage "usuarios" 6). DOM-free, so node tests can import it.
 * The session cookie is HttpOnly: scripts never see it. They only keep the CSRF token that /api/v1/auth/me and
 * /api/v1/auth/login hand out, and send it with every change (POST, PUT, PATCH, DELETE). */
const WRITE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Answers that mean "log in (again)" or "change your password first".
export const AUTH_CODES = new Set(["login_required", "not_authenticated", "password_change_required"]);
export const session = {user: null, csrf: null, loopbackMode: null, passwordMinLength: 5};
// devices.js is a classic script without imports: it reads the CSRF token from here.
globalThis.itgSession = session;
const listeners = {change: new Set(), required: new Set()};

export function sessionHeaders(method = "GET") {
  return WRITE.has(String(method).toUpperCase()) && session.csrf ? {"X-CSRF-Token": session.csrf} : {};
}
export function on(type, handler) {
  listeners[type].add(handler);
  return () => listeners[type].delete(handler);
}
export function emit(type, detail) {
  for (const handler of [...listeners[type]]) {
    try { handler(detail); } catch (error) { console.error(error); }
  }
}
export function remember(data) {
  session.user = data?.user || null;
  session.csrf = data?.csrf_token || null;
  if (data?.password_min_length) session.passwordMinLength = data.password_min_length;
  emit("change", session);
}
export function forget() {
  session.user = null;
  session.csrf = null;
  emit("change", session);
}
export function hasRole(...roles) { return Boolean(session.user) && roles.includes(session.user.role); }
