"""Web accounts in the device database (schema v2, table users).

Every function takes an open connection inside a transaction: pairing.store.transaction() in the API, connect()
in the local scripts. Nothing here prints, logs or returns a password or a hash."""
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import closing, contextmanager

from backend.app.pairing import store
from . import passwords

ROLES = ("superadmin", "admin", "user")
STATUSES = ("active", "disabled")
# Devices that already belong to someone when the first superadmin is created.
OWNED_STATES = ("approved", "active", "blocked")
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
DISPLAY_MAX = 64
FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}
PUBLIC_FIELDS = ("id,username,display_name,role,status,must_change_pw,created_at,created_by,updated_at,"
                 "pw_changed_at,last_login_at,failed_count,locked_until")


class AccountError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def connect(path=None):
    """Transaction on the device database for the local scripts (created and migrated if needed)."""
    path = store.ensure_database(path)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        yield db


def _now(now):
    return int(time.time()) if now is None else int(now)


def normalize_username(raw):
    value = unicodedata.normalize("NFKC", str(raw or "")).strip().lower()
    if not USERNAME.fullmatch(value):
        raise AccountError("username_invalid", "El usuario debe tener de 3 a 64 caracteres (letras a-z, numeros, "
                                               "punto, guion o guion bajo) y empezar por letra o numero.")
    return value


def clean_display_name(raw, fallback):
    value = unicodedata.normalize("NFC", str(raw or "")).strip() or fallback
    if len(value) > DISPLAY_MAX or any(unicodedata.category(char) in FORBIDDEN_CATEGORIES for char in value):
        raise AccountError("display_name_invalid",
                           f"El nombre a mostrar debe tener hasta {DISPLAY_MAX} caracteres visibles.")
    return value


def create_user(db, username, password, role, display_name=None, created_by=None, must_change=False, now=None):
    """Id of the new user. Validates the username, the display name, the role and the password policy."""
    if role not in ROLES:
        raise AccountError("role_invalid", "Rol no valido.")
    username = normalize_username(username)
    display = clean_display_name(display_name, username)
    passwords.check_policy(password)
    salt, digest, params = passwords.hash_password(password)
    now = _now(now)
    user_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,must_change_pw,"
                   "created_at,created_by,updated_at,pw_changed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (user_id, username, display, role, salt, digest, params, int(bool(must_change)), now,
                    created_by, now, now))
    except sqlite3.IntegrityError:
        raise AccountError("username_taken", "Ya existe un usuario con ese nombre.") from None
    return user_id


def get(db, user_id):
    return db.execute(f"SELECT {PUBLIC_FIELDS} FROM users WHERE id=?", (user_id,)).fetchone()


def find(db, username):
    """The user with this username (any case) or None. Public fields only."""
    try:
        username = normalize_username(username)
    except AccountError:
        return None
    return db.execute(f"SELECT {PUBLIC_FIELDS} FROM users WHERE username=?", (username,)).fetchone()


def list_users(db):
    order = "CASE role WHEN 'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END"
    return [dict(row) for row in db.execute(f"SELECT {PUBLIC_FIELDS} FROM users ORDER BY {order}, username")]


def active_superadmins(db):
    return db.execute("SELECT COUNT(*) FROM users WHERE role='superadmin' AND status='active'").fetchone()[0]


def any_superadmin(db):
    return db.execute("SELECT 1 FROM users WHERE role='superadmin' LIMIT 1").fetchone() is not None


def _require(db, user_id):
    row = get(db, user_id)
    if row is None:
        raise AccountError("user_not_found", "No existe ese usuario.")
    return row


def revoke_sessions(db, user_id, now=None):
    """Close every open web session of the user; returns how many."""
    return db.execute("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                      (_now(now), user_id)).rowcount


def set_password(db, user_id, password, must_change=False, now=None):
    """New password (policy checked); also clears the lockout and closes every session of the user."""
    row = _require(db, user_id)
    passwords.check_policy(password)
    salt, digest, params = passwords.hash_password(password)
    now = _now(now)
    db.execute("UPDATE users SET pw_salt=?,pw_hash=?,pw_params=?,must_change_pw=?,pw_changed_at=?,updated_at=?,"
               "failed_count=0,locked_until=NULL WHERE id=?",
               (salt, digest, params, int(bool(must_change)), now, now, user_id))
    revoke_sessions(db, user_id, now)


def set_status(db, user_id, status, now=None):
    """Enable or disable. At least one active superadmin always remains; disabling closes the sessions."""
    if status not in STATUSES:
        raise AccountError("status_invalid", "Estado no valido.")
    row = _require(db, user_id)
    if (status == "disabled" and row["role"] == "superadmin" and row["status"] == "active"
            and active_superadmins(db) <= 1):
        raise AccountError("last_superadmin", "Debe quedar al menos un superadmin activo.")
    now = _now(now)
    db.execute("UPDATE users SET status=?,updated_at=? WHERE id=?", (status, now, user_id))
    if status == "disabled":
        revoke_sessions(db, user_id, now)


def unlock(db, user_id, now=None):
    _require(db, user_id)
    db.execute("UPDATE users SET failed_count=0,locked_until=NULL,updated_at=? WHERE id=?", (_now(now), user_id))


def assign_unowned_devices(db, user_id, assigned_by, now=None):
    """Give every approved, active or blocked device without an owner to ``user_id``; returns how many."""
    marks = ",".join("?" * len(OWNED_STATES))
    return db.execute(f"UPDATE installs SET user_id=?,assigned_at=?,assigned_by=? "
                      f"WHERE user_id IS NULL AND state IN ({marks})",
                      (user_id, _now(now), assigned_by, *OWNED_STATES)).rowcount
