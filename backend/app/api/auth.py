"""Enterprise Bearer and scoped device proofs. Admin cookies cannot authorize these routes."""
import os
import secrets
from fastapi import Header, HTTPException, Request
from backend.app.core.config import settings


def _tokens():
    raw = os.environ.get("ITGLASSES_API_TOKENS", "")
    return {token.strip() for token in raw.split(",") if token.strip()}


def require_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
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
    if not required and not os.environ.get("ITGLASSES_PAIRING_ORIGIN"):
        return  # Preserve explicit loopback development without credentials.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_credentials")
    token = authorization.split(" ", 1)[1].strip().encode("utf-8")
    # Empty configuration fails closed; never accept a cookie as a fallback.
    if not token or not any(secrets.compare_digest(token, candidate.encode("utf-8")) for candidate in _tokens()):
        raise HTTPException(status_code=401, detail="invalid_credentials")
