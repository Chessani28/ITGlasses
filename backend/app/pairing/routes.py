import secrets
import time
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request

from .proof import proof
from .store import digest, origin, throttle, transaction

# Phone endpoints only. Administration lives in admin.py behind require_admin().
router = APIRouter(prefix="/api/v1", dependencies=[Depends(origin)])
MOBILE = {
    ("POST", "/api/v1/chat"), ("POST", "/api/v1/assistant/chat"),
    ("POST", "/api/v1/voice/transcribe"), ("POST", "/api/v1/voice/synthesize"),
    ("GET", "/api/v1/devices/me"),
}
# Open requests (pending, or approved but not yet claimed) allowed at once.
PENDING_PER_IP = 3
PENDING_TOTAL = 20
LABEL_MAX = 64
# Control, format (e.g. U+202E bidi override), line and paragraph separators.
FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}
SEEN_INTERVAL = 60


def peer(request):
    """Informative only, never identity. Behind Caddy, uvicorn fills it from X-Forwarded-For."""
    return request.client.host if request.client else None


def mobile_auth(request, token):
    origin(request)
    with transaction() as db:
        row = db.execute("SELECT id,key_id,state FROM installs WHERE token=? AND token_exp>?",
                         (digest(token), time.time())).fetchone()
        key, _ = proof(request, db, token)
        if not row:
            # A deleted device is told so (410) and pairs again; anything else is 401.
            known = db.execute("SELECT 1 FROM installs WHERE key_id=?", (key,)).fetchone()
            raise HTTPException(401 if known else 410)
        if key != row["key_id"]:
            raise HTTPException(401)
        if row["state"] == "blocked":
            raise HTTPException(403)
        if row["state"] != "active":
            raise HTTPException(401)
        if (request.method, request.url.path) not in MOBILE:
            raise HTTPException(403)
        now = int(time.time())
        db.execute("UPDATE installs SET last_seen=?,last_ip=? WHERE id=? AND (last_seen IS NULL OR last_seen<=?)",
                   (now, peer(request), row["id"], now-SEEN_INTERVAL))
        request.state.device_id = row["id"]


@router.get("/devices/me")
def me(request: Request):
    auth = request.headers.get("authorization","").split(" ",1)
    if len(auth) != 2 or auth[0].lower() != "dpop":
        raise HTTPException(401)
    mobile_auth(request, auth[1])
    return {"device_id":request.state.device_id}


def clean(db, now):
    db.execute("DELETE FROM installs WHERE state IN ('pending','approved','denied') AND expires<=?", (now,))


def label(claims, field):
    """Name and model come from the phone: untrusted text, stored verbatim once validated."""
    value = claims.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422)
    value = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(value) <= LABEL_MAX or any(unicodedata.category(ch) in FORBIDDEN_CATEGORIES for ch in value):
        raise HTTPException(422)
    return value


def open_requests(db, now, ip=None, by_ip=False):
    query = "SELECT COUNT(*),MIN(expires) FROM installs WHERE state IN ('pending','approved') AND expires>?"
    row = db.execute(query + " AND last_ip IS ?", (now, ip)).fetchone() if by_ip else db.execute(query, (now,)).fetchone()
    return tuple(row)  # (count, oldest expiry)


def new_code(db):
    """Six digits for human visual confirmation only. Claims are authorized by the key, never by this code."""
    while True:
        code = f"{secrets.randbelow(10**6):06d}"
        if not db.execute("SELECT 1 FROM installs WHERE code=?", (code,)).fetchone():
            return code


def pending(row):
    return {"request_id":row["id"], "user_code":row["code"], "expires_at":row["expires"], "poll_interval":5}


@router.post("/pairing/requests", status_code=201)
def register(request: Request):
    throttle(request, "register", 15, 120, 60)
    with transaction() as db:
        key, data = proof(request, db)
        variant = data.get("variant")
        if variant not in ("phone","meta"):
            raise HTTPException(422)
        name, model = label(data, "name"), label(data, "model")
        ip = peer(request)
        now = int(time.time())
        clean(db,now)
        existing = db.execute("SELECT * FROM installs WHERE key_id=? AND state IN ('pending','approved','active','blocked')",
                              (key,)).fetchone()
        if existing:
            if existing["state"] == "blocked":
                db.commit()
                raise HTTPException(403)
            if existing["state"] == "active":
                return {"request_id":existing["id"], "user_code":"", "state":"active"}
            return pending(existing)
        for count, oldest, maximum in (open_requests(db, now, ip, by_ip=True) + (PENDING_PER_IP,),
                                       open_requests(db, now) + (PENDING_TOTAL,)):
            if count >= maximum:
                db.commit()  # Consume the proof before refusing.
                raise HTTPException(429, headers={"Retry-After": str(max(1, oldest-now))})
        rid = secrets.token_urlsafe(24)
        code = new_code(db)
        expires = now+300
        db.execute("INSERT INTO installs(id,key_id,variant,code,state,expires,name,model,created_at,last_seen,last_ip) "
                   "VALUES(?,?,?,?,'pending',?,?,?,?,?,?)",
                   (rid,key,variant,code,expires,name,model,now,now,ip))
        return {"request_id":rid,"user_code":code,"expires_at":expires,"poll_interval":5}


def identify(request, db):
    key, claims = proof(request, db)
    rid = claims.get("rid")
    if not isinstance(rid,str) or len(rid)>64:
        raise HTTPException(401)
    row = db.execute("SELECT * FROM installs WHERE id=? AND key_id=?", (rid,key)).fetchone()
    if not row:
        exists = db.execute("SELECT 1 FROM installs WHERE id=?", (rid,)).fetchone()
        raise HTTPException(401 if exists else 410)
    return row, claims


def limit_signed_calls(request: Request):
    throttle(request, "pairing-proofs", 240, 1200, 60)


def refuse(db, row):
    db.commit()  # Consume the proof before refusing.
    raise HTTPException(403 if row["state"] == "blocked" else 401)


@router.post("/pairing/status", dependencies=[Depends(limit_signed_calls)])
def status(request: Request):
    with transaction() as db:
        row, _ = identify(request, db)
        state = row["state"]
        if state in ("pending","approved","denied") and row["expires"] <= time.time():
            state = "expired"
        return {"state":state}


def issue(db, rid):
    token = secrets.token_urlsafe(32)
    db.execute("UPDATE installs SET token=?,token_exp=?,nonce=NULL,nonce_exp=NULL WHERE id=?",
               (digest(token), int(time.time())+600, rid))
    return {"device_id":rid,"access_token":token,"token_type":"DPoP","expires_in":600}


@router.post("/pairing/claim", dependencies=[Depends(limit_signed_calls)])
def claim(request: Request):
    with transaction() as db:
        row, _ = identify(request, db)
        if row["state"] != "approved" or row["expires"] <= time.time():
            db.commit()  # Consume the proof even while approval is pending.
            raise HTTPException(409)
        db.execute("UPDATE installs SET state='active',code=NULL,last_seen=?,last_ip=? WHERE id=?",
                   (int(time.time()), peer(request), row["id"]))
        return issue(db, row["id"])


@router.post("/devices/challenge", dependencies=[Depends(limit_signed_calls)])
def challenge(request: Request):
    with transaction() as db:
        row, _ = identify(request, db)
        if row["state"] != "active":
            refuse(db, row)
        nonce = secrets.token_urlsafe(32)
        db.execute("UPDATE installs SET nonce=?,nonce_exp=? WHERE id=?", (digest(nonce),int(time.time())+60,row["id"]))
        return {"nonce":nonce}


@router.post("/devices/token", dependencies=[Depends(limit_signed_calls)])
def renew(request: Request):
    with transaction() as db:
        row, data = identify(request, db)
        if row["state"] != "active":
            refuse(db, row)
        nonce = data.get("nonce")
        if (not isinstance(nonce,str) or not row["nonce"]
                or row["nonce_exp"] <= time.time() or not secrets.compare_digest(digest(nonce),row["nonce"])):
            db.commit()
            raise HTTPException(401)
        return issue(db, row["id"])
