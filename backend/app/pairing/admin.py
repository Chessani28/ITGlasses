"""Device administration for the local beta.

Every admin route depends on require_admin(), the ONLY admin gate in the code.
To add a real administrator login later (pro version), change that function.
This router is independent from the phone router: no pairing origin, no HTTPS.
"""
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from backend.app.core import local_request
from backend.app.core.config import settings
from .routes import clean
from .store import transaction

ADMIN_HEADER = "x-itglasses-admin"
LISTED = ("pending", "approved", "active", "blocked", "denied")
# expires: when the registration request lapses (pending, approved or denied rows).
FIELDS = "id,variant,name,model,state,code,created_at,approved_at,last_seen,last_ip,expires"


def _local_hosts():
    port = settings.port
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def require_admin(request: Request) -> None:
    """Accept only a browser on this PC talking directly to the backend."""
    hosts = _local_hosts()
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


@router.get("/devices")
def inventory():
    with transaction() as db:
        clean(db, int(time.time()))
        marks = ",".join("?" * len(LISTED))
        rows = db.execute(f"SELECT {FIELDS} FROM installs WHERE state IN ({marks}) ORDER BY rowid DESC", LISTED)
        return {"devices": [dict(row) for row in rows]}


def _move(device_id, allowed, target, approved=False):
    now = int(time.time())
    with transaction() as db:
        row = db.execute("SELECT state,expires FROM installs WHERE id=?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["state"] not in allowed or (row["state"] == "pending" and row["expires"] <= now):
            raise HTTPException(409)
        if approved:
            db.execute("UPDATE installs SET state=?,approved_at=? WHERE id=?", (target, now, device_id))
        else:
            db.execute("UPDATE installs SET state=? WHERE id=?", (target, device_id))
    return {"id": device_id, "state": target}


@router.post("/devices/{device_id}/approve")
def approve(device_id: DeviceId):
    return _move(device_id, ("pending",), "approved", approved=True)


@router.post("/devices/{device_id}/deny")
def deny(device_id: DeviceId):
    return _move(device_id, ("pending",), "denied")


@router.post("/devices/{device_id}/block")
def block(device_id: DeviceId):
    # The token is kept so the phone learns it is blocked (403) instead of re-pairing.
    return _move(device_id, ("active",), "blocked")


@router.post("/devices/{device_id}/unblock")
def unblock(device_id: DeviceId):
    return _move(device_id, ("blocked",), "active")


@router.delete("/devices/{device_id}")
def delete(device_id: DeviceId):
    with transaction() as db:
        if not db.execute("DELETE FROM installs WHERE id=?", (device_id,)).rowcount:
            raise HTTPException(404)
    return {"id": device_id, "deleted": True}
