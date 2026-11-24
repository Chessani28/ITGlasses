"""Who may call which protected route (stage "usuarios" 4).

One table, ROUTES, gives the capability every route behind require_auth needs; ROLE_CAPABILITIES says which roles
have it. Unknown routes fail closed for sessions and for loopback "chat". The phone paths do not change:
DPoP devices keep their own MOBILE list (pairing/routes.py) and the shared Bearer tokens get the same list plus
read-only voice status (ITGLASSES_BEARER_SCOPE=mobile, the default) or everything as before (=full).

Credential-free requests from this PC (ITGLASSES_LOOPBACK_MODE):
  legacy  everything, as before stage 4 (the rollback value: LoopbackMode = 'legacy' in runtime-config.psd1)
  chat    chat and voice only; documents, settings and the device panel need a login session (the default from
          stage "usuarios" 6 to stage "historial" 5)
  off     nothing without a login session (default since stage "historial" 6: every conversation has an owner)
"""
import os

from backend.app.pairing.routes import MOBILE

LOOPBACK_VARIABLE, LOOPBACK_MODES, LOOPBACK_DEFAULT = "ITGLASSES_LOOPBACK_MODE", ("legacy", "chat", "off"), "off"
BEARER_VARIABLE, BEARER_SCOPES, BEARER_DEFAULT = "ITGLASSES_BEARER_SCOPE", ("mobile", "full"), "mobile"

CHAT = "chat"
DOCUMENTS_READ = "documents.read"
DOCUMENTS_WRITE = "documents.write"
SETTINGS = "settings"
DEVICES = "devices"

ROUTES = {
    ("POST", "/api/v1/chat"): CHAT,
    ("POST", "/api/v1/assistant/chat"): CHAT,
    ("POST", "/api/v1/knowledge/chat"): CHAT,
    ("GET", "/api/v1/knowledge/sources/{citation_id}"): CHAT,
    ("GET", "/api/v1/knowledge/status"): CHAT,
    ("GET", "/api/v1/voice/status"): CHAT,
    ("POST", "/api/v1/voice/transcribe"): CHAT,
    ("POST", "/api/v1/voice/synthesize"): CHAT,
    ("GET", "/api/v1/voice/diagnostic/settings"): SETTINGS,
    ("PUT", "/api/v1/voice/diagnostic/settings"): SETTINGS,
    ("GET", "/api/v1/documents"): DOCUMENTS_READ,
    ("POST", "/api/v1/documents"): DOCUMENTS_WRITE,
    ("PUT", "/api/v1/documents/{document_id}"): DOCUMENTS_WRITE,
    ("PATCH", "/api/v1/documents/{document_id}/authorization"): DOCUMENTS_WRITE,
    ("POST", "/api/v1/documents/{document_id}/withdraw"): DOCUMENTS_WRITE,
    ("DELETE", "/api/v1/documents/{document_id}"): DOCUMENTS_WRITE,
    ("POST", "/api/v1/documents/{document_id}/reindex"): DOCUMENTS_WRITE,
}
ROLE_CAPABILITIES = {
    "superadmin": frozenset({CHAT, DOCUMENTS_READ, DOCUMENTS_WRITE, SETTINGS, DEVICES}),
    "admin": frozenset({CHAT, DOCUMENTS_READ, DOCUMENTS_WRITE, DEVICES}),
    "user": frozenset({CHAT}),
}
# Shared Bearer tokens (app v1): the phone routes plus read-only voice status, which older clients poll.
BEARER_MOBILE = frozenset(MOBILE) | {("GET", "/api/v1/voice/status")}


class AccessConfigError(ValueError):
    code = "access_config"


def _choice(variable, allowed, default):
    raw = os.environ.get(variable)
    text = (raw or "").strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise AccessConfigError(f"{variable} debe ser uno de: {', '.join(allowed)} "
                                f"(valor actual: {(raw or '').strip()[:20]!r}).")
    return text


def loopback_mode():
    return _choice(LOOPBACK_VARIABLE, LOOPBACK_MODES, LOOPBACK_DEFAULT)


def bearer_scope():
    return _choice(BEARER_VARIABLE, BEARER_SCOPES, BEARER_DEFAULT)


def route_key(request):
    """(method, path template) of the matched route, e.g. ("PATCH", "/api/v1/documents/{document_id}/...")."""
    route = request.scope.get("route")
    return request.method, getattr(route, "path", None) or request.url.path


def capability(request):
    return ROUTES.get(route_key(request))


def role_allows(role, needed):
    return needed is not None and needed in ROLE_CAPABILITIES.get(role, frozenset())


def loopback_allows(needed, mode=None):
    mode = loopback_mode() if mode is None else mode
    return mode == "legacy" or (mode == "chat" and needed == CHAT)


def bearer_allows(request, scope=None):
    scope = bearer_scope() if scope is None else scope
    return scope == "full" or (request.method, request.url.path) in BEARER_MOBILE
