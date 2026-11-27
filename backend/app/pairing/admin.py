"""Device administration for the local beta.

Every admin route depends on require_admin(), the ONLY admin gate in the code: a login session with the
admin or superadmin role, or the original local-browser check while ITGLASSES_LOOPBACK_MODE=legacy.
This router is independent from the phone router: no pairing origin, no HTTPS.
"""
import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core import local_request
from .routes import clean
from .store import transaction

ADMIN_HEADER = "x-itglasses-admin"
LISTED = ("pending", "approved", "active", "blocked", "denied")
# expires: when the registration request lapses (pending, approved or denied rows).
FIELDS = "id,variant,name,model,state,code,created_at,approved_at,last_seen,last_ip,expires"
# Owner (stage "usuarios" 5): who the device belongs to, for the panel.
OWNER_FIELDS = "i.user_id,i.assigned_at,u.username AS owner_username,u.display_name AS owner_display_name"
ASSIGNABLE = ("approved", "active", "blocked")


def require_admin(request: Request) -> None:
    """Stage "usuarios" 4: a login session of an admin or superadmin from this PC (changes need the page Origin
    and the CSRF token), or, only with ITGLASSES_LOOPBACK_MODE=legacy, the original local-browser check below."""
    from backend.app.users import access
    from backend.app.users.web import optional_session

    session = optional_session(request, write=request.method not in ("GET", "HEAD"))
    if session is not None:
        if access.role_allows(session.role, access.DEVICES):
            request.state.user_id = session.user_id
            return
        raise HTTPException(403)
    if access.loopback_mode() != "legacy":
        raise HTTPException(403)
    _legacy_local_browser(request)


def _legacy_local_browser(request: Request) -> None:
    """Accept only a browser on this PC talking directly to the backend."""
    hosts = local_request.local_hosts()
    headers = request.headers
    allowed = (
        # 1-2. Loopback peer and no forwarding header (shared with api/auth.py).
        local_request.is_direct_local(request),
        # 3. Host names this backend directly: defeats DNS rebinding and proxied hosts.
        headers.get("host", "").lower() in hosts,
        # 4. Custom header: a foreign page needs a CORS preflight that is never granted.
        headers.get(ADMIN_HEADER) == "1",
        # 5. Browser provenance, when the browser declares it.
        headers.get("origin") in (None, *(f"http://{host}" for host in hosts)),
        headers.get("sec-fetch-site") in (None, "same-origin", "none"),
    )
    if not all(allowed):
        raise HTTPException(403)


router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)])
DeviceId = Annotated[str, Path(min_length=1, max_length=64)]


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str | None = Field(default=None, max_length=64)


def _audit(db, request, action, device_id, detail=None):
    from backend.app.users import audit_web
    audit_web.record(db, request, action, "device", device_id, detail)


def _owner(db, user_id):
    """The active user that will own a device; 422 when unknown or disabled."""
    row = db.execute("SELECT id,username,status FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None or row["status"] != "active":
        raise HTTPException(422)
    return row


def _signed_in(request):
    """Assigning an owner needs a login session (the old local panel without login cannot do it)."""
    if not getattr(request.state, "user_id", None):
        raise HTTPException(403)


@router.get("/pairing-link")
def pairing_link(response: Response):
    """QR for one APK, any server: the public origin and its certificate pin (see link.py). 404 without a
    configured origin; 503 when the server's certificate cannot be read (Caddy stopped)."""
    import os
    from . import link

    origin = os.getenv("ITGLASSES_PAIRING_ORIGIN", "")
    if not link.valid_origin(origin):
        raise HTTPException(404)
    try:
        pin = link.server_pin(origin)
    except link.Unavailable:
        raise HTTPException(503) from None
    text = link.link(origin, pin)
    response.headers["Cache-Control"] = "no-store"
    return {"origin": origin, "pin": pin, "link": text, "svg": link.qr_svg(text)}


@router.get("/devices")
def inventory():
    with transaction() as db:
        clean(db, int(time.time()))
        marks = ",".join("?" * len(LISTED))
        fields = ",".join("i." + name for name in FIELDS.split(","))
        rows = db.execute(f"SELECT {fields},{OWNER_FIELDS} FROM installs i LEFT JOIN users u ON u.id=i.user_id "
                          f"WHERE i.state IN ({marks}) ORDER BY i.rowid DESC", LISTED)
        return {"devices": [dict(row) for row in rows]}


def _move(request, device_id, allowed, target, approved=False, owner=None):
    now = int(time.time())
    with transaction() as db:
        row = db.execute("SELECT state,expires,name FROM installs WHERE id=?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["state"] not in allowed or (row["state"] == "pending" and row["expires"] <= now):
            raise HTTPException(409)
        user = _owner(db, owner) if owner else None
        if approved:
            db.execute("UPDATE installs SET state=?,approved_at=? WHERE id=?", (target, now, device_id))
        else:
            db.execute("UPDATE installs SET state=? WHERE id=?", (target, device_id))
        detail = {"name": row["name"], "from": row["state"]}
        if user is not None:
            db.execute("UPDATE installs SET user_id=?,assigned_at=?,assigned_by=? WHERE id=?",
                       (user["id"], now, request.state.user_id, device_id))
            detail["owner"] = user["username"]
        _audit(db, request, "device." + {"approved": "approve", "denied": "deny", "blocked": "block",
                                         "active": "unblock"}[target], device_id, detail)
    return {"id": device_id, "state": target, **({"user_id": user["id"]} if user is not None else {})}


@router.post("/devices/{device_id}/approve")
def approve(device_id: DeviceId, request: Request, body: Owner | None = Body(default=None)):
    """Optional owner (stage 5): with a login session, approve and assign in one step. Without an owner the device
    is approved unassigned, as before; it keeps working and can be assigned later."""
    owner = body.user_id if body else None
    if owner:
        _signed_in(request)
    return _move(request, device_id, ("pending",), "approved", approved=True, owner=owner)


@router.post("/devices/{device_id}/deny")
def deny(device_id: DeviceId, request: Request):
    return _move(request, device_id, ("pending",), "denied")


@router.post("/devices/{device_id}/block")
def block(device_id: DeviceId, request: Request):
    # The token is kept so the phone learns it is blocked (403) instead of re-pairing.
    return _move(request, device_id, ("active",), "blocked")


@router.post("/devices/{device_id}/unblock")
def unblock(device_id: DeviceId, request: Request):
    return _move(request, device_id, ("blocked",), "active")


@router.post("/devices/{device_id}/assign")
def assign(device_id: DeviceId, body: Owner, request: Request):
    """Give the device to a user (user_id) or leave it without owner (null). Never changes its state or token."""
    _signed_in(request)
    now = int(time.time())
    with transaction() as db:
        row = db.execute("SELECT i.state,i.name,u.username AS previous FROM installs i LEFT JOIN users u "
                         "ON u.id=i.user_id WHERE i.id=?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["state"] not in ASSIGNABLE:
            raise HTTPException(409)
        user = _owner(db, body.user_id) if body.user_id else None
        db.execute("UPDATE installs SET user_id=?,assigned_at=?,assigned_by=? WHERE id=?",
                   (user["id"] if user else None, now if user else None, request.state.user_id if user else None,
                    device_id))
        _audit(db, request, "device.assign", device_id,
               {"name": row["name"], "owner": user["username"] if user else None, "previous": row["previous"]})
    return {"id": device_id, "user_id": user["id"] if user else None}


@router.delete("/devices/{device_id}")
def delete(device_id: DeviceId, request: Request):
    with transaction() as db:
        row = db.execute("SELECT name,state FROM installs WHERE id=?", (device_id,)).fetchone()
        if not row or not db.execute("DELETE FROM installs WHERE id=?", (device_id,)).rowcount:
            raise HTTPException(404)
        _audit(db, request, "device.delete", device_id, {"name": row["name"], "from": row["state"]})
    return {"id": device_id, "deleted": True}
