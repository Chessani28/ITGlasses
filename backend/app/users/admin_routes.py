"""Web administration of users, the read-only audit log and "my devices" (stage "usuarios" 5).

Every route needs a login session from this PC (users/web.py); changes need the page Origin and the CSRF token.
Never published by Caddy (it refuses /api/v1/admin and publishes only the phone routes).

Who manages whom (accounts.can_manage): an admin manages users; a superadmin manages users and admins and is the
only one who changes roles. Nobody manages a superadmin or themself here. The audit log is read-only for both:
an admin sees user, device, document and conversation actions (deletes and purges: never content or titles); a
superadmin sees everything (logins, lockouts, bootstrap...).
"""
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.knowledge.models import KnowledgeError
from backend.app.pairing.store import transaction
from . import accounts, audit, audit_web
from .web import current_session

users_router = APIRouter(prefix="/api/v1/admin/users")
audit_router = APIRouter(prefix="/api/v1/admin")
me_router = APIRouter(prefix="/api/v1/me")

ADMIN_ROLES = ("admin", "superadmin")
ADMIN_AUDIT_PREFIXES = ("user.", "device.", "document.", "conversation.")
DEVICE_FIELDS = "id,variant,name,model,state,created_at,approved_at,last_seen"
ACCOUNT_STATUS = {"username_taken": 409, "last_superadmin": 409, "user_not_found": 404}


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=64)
    role: Literal["user", "admin"] = "user"


class UserChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=64)
    status: Literal["active", "disabled"] | None = None


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "admin"]


def _admin(request, write=False):
    session = current_session(request, write=write)
    if session.role not in ADMIN_ROLES:
        raise KnowledgeError("forbidden_role", "Tu rol no permite esta accion.", 403)
    request.state.user_id = session.user_id
    return session


def _account_error(error):
    return KnowledgeError(error.code, str(error), ACCOUNT_STATUS.get(error.code, 422))


def _target(db, session, user_id):
    target = accounts.get(db, user_id)
    if target is None:
        raise KnowledgeError("user_not_found", "No existe ese usuario.", 404)
    if not accounts.can_manage(session.role, session.user_id, target):
        raise KnowledgeError("forbidden_target", "No puedes administrar a este usuario.", 403)
    return target


def _public(row, session, devices=None):
    user = dict(row)
    user["must_change_pw"] = bool(user["must_change_pw"])
    user["manageable"] = accounts.can_manage(session.role, session.user_id, row)
    if devices is not None:
        user["devices"] = devices.get(row["id"], 0)
    return user


@users_router.get("")
def list_users(request: Request, response: Response):
    session = _admin(request)
    response.headers["Cache-Control"] = "no-store"
    with transaction() as db:
        devices = accounts.devices_per_user(db)
        rows = db.execute(f"SELECT {accounts.PUBLIC_FIELDS} FROM users ORDER BY CASE role WHEN 'superadmin' "
                          "THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, username").fetchall()
        return {"users": [_public(row, session, devices) for row in rows], "you": session.user_id}


@users_router.post("", status_code=201)
def create_user(body: NewUser, request: Request, response: Response):
    session = _admin(request, write=True)
    if body.role == "admin" and session.role != "superadmin":
        raise KnowledgeError("forbidden_role", "Solo el superadmin puede crear administradores.", 403)
    temporary = accounts.temporary_password()
    try:
        with transaction() as db:
            user_id = accounts.create_user(db, body.username, temporary, body.role, body.display_name,
                                           created_by=session.user_id, must_change=True)
            row = accounts.get(db, user_id)
            audit_web.record(db, request, "user.create", "user", user_id,
                             {"username": row["username"], "role": row["role"]})
    except accounts.AccountError as error:
        raise _account_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    # Shown once: never stored in clear, never logged, never in the audit log.
    return {"user": _public(row, session), "temporary_password": temporary}


@users_router.patch("/{user_id}")
def change_user(user_id: str, body: UserChange, request: Request):
    session = _admin(request, write=True)
    if body.display_name is None and body.status is None:
        raise KnowledgeError("nothing_to_change", "Indica el nombre a mostrar o el estado.", 422)
    try:
        with transaction() as db:
            target = _target(db, session, user_id)
            if body.display_name is not None:
                value = accounts.set_display_name(db, user_id, body.display_name)
                audit_web.record(db, request, "user.update", "user", user_id,
                                 {"username": target["username"], "display_name": value})
            if body.status is not None and body.status != target["status"]:
                accounts.set_status(db, user_id, body.status)
                action = "user.disable" if body.status == "disabled" else "user.enable"
                audit_web.record(db, request, action, "user", user_id, {"username": target["username"]})
            return {"user": _public(accounts.get(db, user_id), session)}
    except accounts.AccountError as error:
        raise _account_error(error) from None


@users_router.post("/{user_id}/reset-password")
def reset_password(user_id: str, request: Request, response: Response):
    session = _admin(request, write=True)
    temporary = accounts.temporary_password()
    with transaction() as db:
        target = _target(db, session, user_id)
        accounts.set_password(db, user_id, temporary, must_change=True)  # also unlocks and closes sessions
        audit_web.record(db, request, "user.password_reset", "user", user_id,
                         {"username": target["username"], "temporary": True})
        row = accounts.get(db, user_id)
    response.headers["Cache-Control"] = "no-store"
    return {"user": _public(row, session), "temporary_password": temporary}


@users_router.post("/{user_id}/unlock")
def unlock(user_id: str, request: Request):
    session = _admin(request, write=True)
    with transaction() as db:
        target = _target(db, session, user_id)
        accounts.unlock(db, user_id)
        audit_web.record(db, request, "user.unlock", "user", user_id, {"username": target["username"]})
        return {"user": _public(accounts.get(db, user_id), session)}


@users_router.post("/{user_id}/role")
def change_role(user_id: str, body: RoleChange, request: Request):
    session = _admin(request, write=True)
    if session.role != "superadmin":
        raise KnowledgeError("forbidden_role", "Solo el superadmin cambia roles.", 403)
    try:
        with transaction() as db:
            target = _target(db, session, user_id)
            previous = accounts.set_role(db, user_id, body.role)
            if previous != body.role:
                audit_web.record(db, request, "user.role", "user", user_id,
                                 {"username": target["username"], "from": previous, "to": body.role})
            return {"user": _public(accounts.get(db, user_id), session)}
    except accounts.AccountError as error:
        raise _account_error(error) from None


@audit_router.get("/audit")
def read_audit(request: Request, response: Response, limit: int = Query(100, ge=1, le=500),
               before_id: int | None = Query(None, ge=1)):
    session = _admin(request)
    actions = None
    if session.role != "superadmin":
        actions = sorted(action for action in audit.ACTIONS if action.startswith(ADMIN_AUDIT_PREFIXES))
    response.headers["Cache-Control"] = "no-store"
    with transaction() as db:
        entries = audit.entries(db, limit=limit, before_id=before_id, actions=actions)
    return {"entries": entries, "complete": session.role == "superadmin",
            "next_before_id": entries[-1]["id"] if len(entries) == limit else None}


@me_router.get("/devices")
def my_devices(request: Request, response: Response):
    session = current_session(request)
    response.headers["Cache-Control"] = "no-store"
    with transaction() as db:
        rows = db.execute(f"SELECT {DEVICE_FIELDS} FROM installs WHERE user_id=? AND state IN "
                          "('approved','active','blocked') ORDER BY rowid DESC", (session.user_id,)).fetchall()
    return {"devices": [dict(row) for row in rows]}
