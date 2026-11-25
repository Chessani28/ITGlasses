"""Login, logout, current user and password change for the web (stage 3). Never published by Caddy."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.knowledge.models import KnowledgeError
from backend.app.pairing.routes import peer
from backend.app.pairing.store import transaction
from . import accounts, audit, passwords, registration, sessions
from .web import current_session, require_local_page, session_token

router = APIRouter(prefix="/api/v1/auth")
# Inputs are bounded before hashing: scrypt time grows with the password length.
FIELD_MAX = 1024


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=FIELD_MAX)


class RegisterBody(BaseModel):
    """Only these fields: a role (or anything else) in the body is refused with 422."""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    password: str = Field(min_length=1, max_length=FIELD_MAX)
    password_repeat: str = Field(min_length=1, max_length=FIELD_MAX)


PENDING_MESSAGE = "Tu cuenta está pendiente de aprobación."
REQUEST_RECEIVED = ("Solicitud recibida. Un administrador la revisará; podrás entrar cuando la apruebe. "
                    f"Si no se aprueba en {registration.PENDING_DAYS} días, caduca.")
TAKEN_MESSAGE = "No se pudo crear la cuenta con ese usuario; prueba con otro."


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
            if row is sessions.PENDING:
                blocked = "pending"
            elif row is not None:
                sessions.revoke(db, previous)
                token, max_age = sessions.create(db, row["id"], ip)
                info = sessions.resolve(db, token)
    if blocked == "pending":
        raise KnowledgeError("account_pending", PENDING_MESSAGE, 403)
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


@router.get("/config")
def config(request: Request, response: Response):
    """What the login screen needs to know: whether this PC may continue without a login, and the password
    minimum. No secrets; local page only."""
    from .access import loopback_mode
    require_local_page(request, write=False)
    response.headers["Cache-Control"] = "no-store"
    return {"loopback_mode": loopback_mode(), "password_min_length": passwords.min_length(),
            "registration": registration.mode()}


@router.post("/register")
def register(body: RegisterBody, request: Request, response: Response):
    """Self-registration. Same gate as the login (this PC's page: Origin and Sec-Fetch-Site; there is no session
    yet, so no CSRF token), rate limit per client address, role always "user"."""
    mode = registration.mode()
    if mode == "off":
        raise KnowledgeError("not_found", "Not Found", 404)
    require_local_page(request, write=True)
    ip = peer(request)
    response.headers["Cache-Control"] = "no-store"
    with transaction() as db:
        limited = registration.rate_limited(db, ip)
    if limited:
        raise KnowledgeError("register_rate_limited", "Demasiadas solicitudes de registro desde este equipo. "
                             "Espera una hora.", 429)
    if body.password != body.password_repeat:
        raise KnowledgeError("password_mismatch", "Las contraseñas no coinciden.", 422)
    try:
        with transaction() as db:
            if mode == "approval":
                outcome, registration_id = registration.request_account(db, body.username, body.display_name,
                                                                         body.password, ip)
                name = accounts.normalize_username(body.username)
                audit.record(db, "user.register", actor_kind="user", ip=ip, target_type="registration",
                             target_id=registration_id, outcome="ok" if outcome == "pending" else "denied",
                             detail={"username": name, "mode": mode,
                                     **({} if outcome == "pending" else {"reason": "taken"})})
            else:
                outcome, user_id = registration.create_open(db, body.username, body.display_name, body.password)
                name = accounts.normalize_username(body.username)
                audit.record(db, "user.register", actor_kind="user", actor_user_id=user_id, ip=ip,
                             target_type="user", target_id=user_id, outcome="ok" if user_id else "denied",
                             detail={"username": name, "mode": mode,
                                     **({} if user_id else {"reason": "taken"})})
                if user_id:
                    token, max_age = sessions.create(db, user_id, ip)
                    info = sessions.resolve(db, token)
    except accounts.AccountError as error:
        raise KnowledgeError(error.code, str(error), 422) from None
    except passwords.PasswordPolicyError as error:
        raise KnowledgeError(error.code, str(error), 422) from None
    if mode == "approval":
        # The same answer whether the name was free or taken: who has an account is never told.
        response.status_code = 202
        return {"status": "pending", "message": REQUEST_RECEIVED}
    if outcome == "taken":
        raise KnowledgeError("register_failed", TAKEN_MESSAGE, 409)
    response.status_code = 201
    _set_cookie(response, token, max_age)
    return {"status": "created", **_answer(info, token)}


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
