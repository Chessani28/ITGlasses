"""Login, logout, current user and password change for the web (stage 3). Never published by Caddy."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.knowledge.models import KnowledgeError
from backend.app.pairing.routes import peer
from backend.app.pairing.store import transaction
from . import accounts, audit, passwords, sessions
from .web import current_session, require_local_page, session_token

router = APIRouter(prefix="/api/v1/auth")
# Inputs are bounded before hashing: scrypt time grows with the password length.
FIELD_MAX = 1024


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=FIELD_MAX)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=FIELD_MAX)
    new_password: str = Field(min_length=1, max_length=FIELD_MAX)


def _set_cookie(response, token, max_age):
    response.set_cookie(sessions.COOKIE, token, max_age=max_age, path="/", secure=True, httponly=True,
                        samesite="strict")


def _clear_cookie(response):
    response.delete_cookie(sessions.COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def _answer(info, token):
    return {"user": {"id": info["user_id"], "username": info["username"], "display_name": info["display_name"],
                     "role": info["role"], "must_change_pw": info["must_change_pw"]},
            "csrf_token": sessions.csrf_for(token),
            "session": {"idle_expires": info["idle_expires"], "absolute_expires": info["absolute_expires"]},
            "password_min_length": passwords.min_length()}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    require_local_page(request, write=True)
    ip = peer(request)
    previous = session_token(request)
    blocked = row = None
    with transaction() as db:
        if sessions.ip_blocked(db, ip):
            blocked = True
        else:
            row = sessions.authenticate(db, body.username, body.password, ip)
            if row is not None:
                sessions.revoke(db, previous)
                token, max_age = sessions.create(db, row["id"], ip)
                info = sessions.resolve(db, token)
    if blocked:
        raise KnowledgeError("login_rate_limited", "Demasiados intentos fallidos desde esta PC. Espera unos minutos.",
                             429)
    if row is None:
        raise KnowledgeError("login_failed", sessions.GENERIC_FAILURE, 401)
    _set_cookie(response, token, max_age)
    return _answer(info, token)


@router.post("/logout")
def logout(request: Request, response: Response):
    require_local_page(request, write=True)
    token = session_token(request)
    with transaction() as db:
        info = sessions.resolve(db, token)
        if info is not None:
            if not sessions.check_csrf(token, request.headers.get(sessions.CSRF_HEADER)):
                raise KnowledgeError("csrf_failed", "La sesion de esta pagina no es valida; recargala.", 403)
            sessions.revoke(db, token)
            audit.record(db, "auth.logout", actor_kind="user", actor_user_id=info["user_id"], target_type="user",
                         target_id=info["user_id"], ip=peer(request))
    _clear_cookie(response)
    return {"logged_out": True}


@router.get("/me")
def me(request: Request, response: Response):
    session = current_session(request, allow_must_change=True)
    response.headers["Cache-Control"] = "no-store"
    return _answer({"user_id": session.user_id, "username": session.username, "display_name": session.display_name,
                    "role": session.role, "must_change_pw": session.must_change_pw,
                    "idle_expires": session.idle_expires, "absolute_expires": session.absolute_expires},
                   session.token)


@router.post("/password")
def change_password(body: PasswordBody, request: Request, response: Response):
    session = current_session(request, write=True, allow_must_change=True)
    ip = peer(request)
    error = None
    with transaction() as db:
        if not sessions.verify_current_password(db, session.user_id, body.current_password, ip):
            error = KnowledgeError("current_password_wrong", "La contraseña actual no es correcta.", 403)
        else:
            try:
                accounts.set_password(db, session.user_id, body.new_password, must_change=False)
            except passwords.PasswordPolicyError as policy:
                error = KnowledgeError(policy.code, str(policy), 422)
            else:
                # set_password closed every session (this one too): this browser gets a fresh one.
                token, max_age = sessions.create(db, session.user_id, ip)
                info = sessions.resolve(db, token)
                audit.record(db, "user.password_change", actor_kind="user", actor_user_id=session.user_id,
                             target_type="user", target_id=session.user_id, ip=ip)
    if error is not None:
        raise error
    _set_cookie(response, token, max_age)
    return _answer(info, token)
