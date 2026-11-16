import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .proof import proof
from .store import digest, origin, password_hash, throttle, transaction

router = APIRouter(prefix="/api/v1", dependencies=[Depends(origin)])
COOKIE = "__Host-itglasses-admin"
MOBILE = {
    ("POST", "/api/v1/chat"), ("POST", "/api/v1/assistant/chat"),
    ("POST", "/api/v1/voice/transcribe"), ("POST", "/api/v1/voice/synthesize"),
    ("GET", "/api/v1/devices/me"),
}


def mobile_auth(request, token):
    origin(request)
    with transaction() as db:
        row = db.execute("SELECT id,key_id FROM installs WHERE token=? AND state='active' AND token_exp>?",
                         (digest(token), time.time())).fetchone()
        if not row:
            raise HTTPException(401)
        key, _ = proof(request, db, token)
        if key != row["key_id"]:
            raise HTTPException(401)
        if (request.method, request.url.path) not in MOBILE:
            raise HTTPException(403)
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
        now = int(time.time())
        clean(db,now)
        existing = db.execute("SELECT * FROM installs WHERE key_id=? AND state IN ('pending','approved','active')",
                              (key,)).fetchone()
        if existing:
            if existing["state"] == "active":
                return {"request_id":existing["id"], "user_code":"", "state":"active"}
            return pending(existing)
        rid = secrets.token_urlsafe(24)
        while True:
            chars = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(8))
            code = chars[:4]+"-"+chars[4:]
            if not db.execute("SELECT 1 FROM installs WHERE code=?", (code,)).fetchone():
                break
        expires = now+300
        db.execute("INSERT INTO installs(id,key_id,variant,code,state,expires) VALUES(?,?,?,?,'pending',?)",
                   (rid,key,variant,code,expires))
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
        db.execute("UPDATE installs SET state='active',code=NULL WHERE id=?", (row["id"],))
        return issue(db, row["id"])


@router.post("/devices/challenge", dependencies=[Depends(limit_signed_calls)])
def challenge(request: Request):
    with transaction() as db:
        row, _ = identify(request, db)
        if row["state"] != "active":
            db.commit()
            raise HTTPException(401)
        nonce = secrets.token_urlsafe(32)
        db.execute("UPDATE installs SET nonce=?,nonce_exp=? WHERE id=?", (digest(nonce),int(time.time())+60,row["id"]))
        return {"nonce":nonce}


@router.post("/devices/token", dependencies=[Depends(limit_signed_calls)])
def renew(request: Request):
    with transaction() as db:
        row, data = identify(request, db)
        nonce = data.get("nonce")
        if (row["state"] != "active" or not isinstance(nonce,str) or not row["nonce"]
                or row["nonce_exp"] <= time.time() or not secrets.compare_digest(digest(nonce),row["nonce"])):
            db.commit()
            raise HTTPException(401)
        return issue(db, row["id"])


def csrf(request: Request):
    if request.headers.get("origin") != origin(request) or request.headers.get("x-itglasses-admin") != "1":
        raise HTTPException(403)


def admin(request: Request):
    with transaction() as db:
        session = request.cookies.get(COOKIE, "")
        if not session or not db.execute("SELECT 1 FROM sessions WHERE digest=? AND expires>?",
                                         (digest(session),time.time())).fetchone():
            raise HTTPException(401)


class Login(BaseModel):
    password: str = Field(min_length=1,max_length=256)


class Device(BaseModel):
    id: str = Field(max_length=64)


class Decision(Device):
    decision: Literal["approve","deny"]


@router.post("/admin/login", dependencies=[Depends(csrf)])
def login(payload: Login, request: Request, response: Response):
    throttle(request, "login", 5, 25, 300)
    with transaction() as db:
        row = db.execute("SELECT * FROM admin WHERE id=1").fetchone()
        if not row or not secrets.compare_digest(password_hash(payload.password,row["salt"]),row["digest"]):
            raise HTTPException(401)
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES(?,?)", (digest(token),int(time.time())+3600))
    response.set_cookie(COOKIE,token,max_age=3600,secure=True,httponly=True,samesite="strict",path="/")
    return {"ok":True}


@router.post("/admin/logout", dependencies=[Depends(csrf),Depends(admin)])
def logout(request: Request, response: Response):
    with transaction() as db:
        db.execute("DELETE FROM sessions WHERE digest=?", (digest(request.cookies[COOKIE]),))
    response.delete_cookie(COOKIE,secure=True,httponly=True,samesite="strict")
    return {"ok":True}


@router.get("/admin/devices", dependencies=[Depends(admin)])
def inventory():
    with transaction() as db:
        clean(db,int(time.time()))
        rows = db.execute("SELECT id,variant,code,state FROM installs WHERE state IN ('pending','approved','active') ORDER BY rowid DESC")
        return {"devices":[dict(row) for row in rows]}


@router.post("/admin/decision", dependencies=[Depends(csrf),Depends(admin)])
def decision(payload: Decision):
    with transaction() as db:
        state = "approved" if payload.decision=="approve" else "denied"
        count = db.execute("UPDATE installs SET state=? WHERE id=? AND state='pending' AND expires>?",
                           (state,payload.id,time.time())).rowcount
        if not count:
            raise HTTPException(409)
    return {"ok":True}


@router.post("/admin/revoke", dependencies=[Depends(csrf),Depends(admin)])
def revoke(payload: Device):
    with transaction() as db:
        count = db.execute("UPDATE installs SET state='revoked',token=NULL,nonce=NULL,code=NULL WHERE id=? AND state IN ('approved','active','revoked')",
                           (payload.id,)).rowcount
        if not count:
            raise HTTPException(409)
    return {"ok":True}

