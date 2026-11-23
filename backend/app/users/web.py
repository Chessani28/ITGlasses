"""Gates shared by every web route that uses a login session (stage 3; roles are added on top in stage 4).

The web is served on this PC only (127.0.0.1:8000; Caddy publishes only the phone routes), so a session request
must be a direct local request whose Host names this backend (defeats DNS rebinding). A request that changes
something must also come from the backend's own page: Origin is one of its local origins, Sec-Fetch-Site (when
the browser sends it) is same-origin, and X-CSRF-Token matches the session.
"""
from dataclasses import dataclass

from fastapi import Request

from backend.app.core import local_request
from backend.app.knowledge.models import KnowledgeError
from backend.app.pairing.store import transaction
from . import sessions


@dataclass(frozen=True)
class WebSession:
    token: str
    user_id: str
    username: str
    display_name: str
    role: str
    must_change_pw: bool
    idle_expires: int
    absolute_expires: int


def _refuse(code, message, status):
    raise KnowledgeError(code, message, status)


def local_origins():
    return {f"http://{host}" for host in local_request.local_hosts()}


def require_local_page(request: Request, write: bool):
    """403 unless the request comes straight from this PC to this backend (and, for a change, from its page)."""
    headers = request.headers
    if not local_request.is_direct_local(request) or headers.get("host", "").lower() not in local_request.local_hosts():
        _refuse("web_forbidden", "Esta pagina solo se puede usar desde la PC del servidor.", 403)
    if write and (headers.get("origin") not in local_origins()
                  or headers.get("sec-fetch-site") not in (None, "same-origin")):
        _refuse("origin_forbidden", "La solicitud no viene de la pagina de ITGlasses.", 403)


def session_token(request: Request):
    return request.cookies.get(sessions.COOKIE)


def _is_local_page(request):
    return (local_request.is_direct_local(request)
            and request.headers.get("host", "").lower() in local_request.local_hosts())


def optional_session(request: Request, write=False, allow_must_change=False):
    """The valid session of a request from this PC, or None (no cookie, unknown or expired cookie, or a request
    that is not local: a cookie never counts through a proxy). A valid session that tries a change without the
    page Origin or the CSRF token, or that must change its password first, is refused (403), never ignored."""
    token = session_token(request)
    if not token or not _is_local_page(request):
        return None
    with transaction() as db:
        info = sessions.resolve(db, token)
    if info is None:
        return None
    if write:
        require_local_page(request, write=True)
        if not sessions.check_csrf(token, request.headers.get(sessions.CSRF_HEADER)):
            _refuse("csrf_failed", "La sesion de esta pagina no es valida; recargala e intentalo de nuevo.", 403)
    if info["must_change_pw"] and not allow_must_change:
        _refuse("password_change_required", "Debes cambiar tu contrasena antes de continuar.", 403)
    return WebSession(token=token, **info)


def current_session(request: Request, write=False, allow_must_change=False):
    """The logged-in session or a 401/403 (KnowledgeError). For a change, checks Origin and the CSRF token."""
    require_local_page(request, write)
    session = optional_session(request, write, allow_must_change)
    if session is None:
        _refuse("not_authenticated", "Inicia sesion para continuar.", 401)
    return session
