"""Authorization of the protected API routes (every router that depends on require_auth).

Order (stage "usuarios" 4, see backend/app/users/access.py):
  1. DPoP device proof (phones v3): unchanged, its own MOBILE route list.
  2. Web login session from this PC: the role decides (users/access.py ROUTES and ROLE_CAPABILITIES).
  3. Credential-free direct loopback request: ITGLASSES_LOOPBACK_MODE (legacy, chat or off).
  4. Shared Bearer token (app v1, scripts): ITGLASSES_BEARER_SCOPE (mobile or full).
Admin cookies of the old pilot never authorize anything here.
"""
import os
import secrets
from fastapi import Header, HTTPException, Request
from backend.app.core import local_request
from backend.app.core.config import settings
from backend.app.knowledge.models import KnowledgeError


def _tokens():
    raw = os.environ.get("ITGLASSES_API_TOKENS", "")
    return {token.strip() for token in raw.split(",") if token.strip()}


def _valid_bearer(authorization):
    token = authorization.split(" ", 1)[1].strip().encode("utf-8")
    # Empty configuration fails closed.
    return bool(token) and any(secrets.compare_digest(token, candidate.encode("utf-8")) for candidate in _tokens())


def require_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    from backend.app.users import access
    from backend.app.users.web import optional_session

    enabled = {"1", "true", "yes"}
    required = (
        os.environ.get("ITGLASSES_REQUIRE_AUTH", "").lower() in enabled
        or os.environ.get("ITGLASSES_ALLOW_LAN_BINDING", "").lower() in enabled
        or settings.allow_lan_binding
        or settings.host != "127.0.0.1"
    )
    if authorization and authorization.lower().startswith("dpop "):
        from backend.app.pairing.routes import mobile_auth
        mobile_auth(request, authorization[5:])
        return
    needed = access.capability(request)
    session = optional_session(request, write=request.method not in ("GET", "HEAD"))
    if session is not None:
        if access.role_allows(session.role, needed):
            request.state.user_id = session.user_id
            return
        raise KnowledgeError("forbidden_role", "Tu rol no permite esta accion.", 403)
    # Credential-free local mode: direct loopback requests only, never via a proxy. With a pairing origin
    # (Caddy in front for the phones) the Host must also name this backend, so a relayed or rebound request
    # cannot pass as local.
    pairing = bool(os.environ.get("ITGLASSES_PAIRING_ORIGIN"))
    local = (not required and local_request.is_direct_local(request)
             and (not pairing or local_request.has_local_host(request)))
    if local and access.loopback_allows(needed):
        return
    if authorization and authorization.lower().startswith("bearer ") and _valid_bearer(authorization):
        if access.bearer_allows(request):
            return
        raise HTTPException(status_code=403, detail="bearer_scope")
    if local:
        raise KnowledgeError("login_required", "Inicia sesion para continuar.", 401)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_credentials")
    raise HTTPException(status_code=401, detail="invalid_credentials")
