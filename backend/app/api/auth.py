"""Bearer-only API authentication. Browser sessions are not enabled.

The unfinished session implementation is preserved in the recovery backup;
cookies and query parameters must never authenticate these API routes.
"""
import os
import secrets
from fastapi import Header, HTTPException
from backend.app.core.config import settings


def _tokens():
    raw = os.environ.get("ITGLASSES_API_TOKENS", "")
    return {token.strip() for token in raw.split(",") if token.strip()}


def require_auth(authorization: str | None = Header(default=None)) -> None:
    enabled = {"1", "true", "yes"}
    required = (
        os.environ.get("ITGLASSES_REQUIRE_AUTH", "").lower() in enabled
        or os.environ.get("ITGLASSES_ALLOW_LAN_BINDING", "").lower() in enabled
        or settings.allow_lan_binding
        or settings.host != "127.0.0.1"
    )
    if not required:
        return  # Preserve explicit loopback development without credentials.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_credentials")
    token = authorization.split(" ", 1)[1].strip().encode("utf-8")
    # Empty configuration fails closed; never accept a cookie as a fallback.
    if not token or not any(secrets.compare_digest(token, candidate.encode("utf-8")) for candidate in _tokens()):
        raise HTTPException(status_code=401, detail="invalid_credentials")
