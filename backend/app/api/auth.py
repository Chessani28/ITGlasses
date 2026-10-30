import os
from fastapi import Header, HTTPException


def _tokens():
    raw = os.environ.get("ITGLASSES_API_TOKENS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if os.environ.get("ITGLASSES_REQUIRE_AUTH", "").lower() not in {"1", "true", "yes"}:
        # Auth only enforced when explicitly enabled or LAN binding is active.
        if os.environ.get("ITGLASSES_ALLOW_LAN_BINDING", "").lower() not in {"1", "true", "yes"}:
            return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_credentials")
    token = authorization.split(" ", 1)[1].strip()
    if token not in _tokens():
        raise HTTPException(status_code=401, detail="invalid_credentials")