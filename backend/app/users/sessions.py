"""Server-side web sessions (table web_sessions) and the login lockout.

Cookie itg_session: a random 32-byte token; the database only keeps its SHA-256. The CSRF token is derived from the
session token (HMAC), so /auth/me can hand it out again; only its digest is stored. Expiry by inactivity
(ITGLASSES_SESSION_IDLE_MINUTES, default 30) and absolute (ITGLASSES_SESSION_MAX_HOURS, default 12).

Lockout: every MAX_FAILURES consecutive wrong passwords lock the account (15 min, doubling up to 24 h); a correct
login resets the count. Separately, LOGIN_IP_FAILURES failures from one address within LOGIN_IP_WINDOW block that
address for the rest of the window. Every failure looks the same to the client.
"""
import hashlib
import hmac
import os
import secrets
import time

from . import accounts, audit, passwords

COOKIE = "itg_session"
CSRF_HEADER = "x-csrf-token"
IDLE_VARIABLE, IDLE_DEFAULT, IDLE_RANGE = "ITGLASSES_SESSION_IDLE_MINUTES", 30, (5, 1440)
MAX_VARIABLE, MAX_DEFAULT, MAX_RANGE = "ITGLASSES_SESSION_MAX_HOURS", 12, (1, 168)
TOUCH_INTERVAL = 60            # seconds between two updates of last_seen for one session
RETENTION = 7 * 24 * 3600      # expired or revoked sessions are deleted after a week
TOKEN_MAX = 128
MAX_FAILURES = 5
LOCK_SECONDS, LOCK_MAX_SECONDS = 15 * 60, 24 * 3600
LOGIN_IP_FAILURES, LOGIN_IP_WINDOW = 20, 15 * 60
# authenticate() result for the right password of a registration still waiting for approval.
PENDING = "pending"
GENERIC_FAILURE = "Usuario o contraseña incorrectos, o la cuenta está bloqueada temporalmente."

_dummy = None


class SessionConfigError(ValueError):
    code = "session_config"


def _now(now):
    return int(time.time()) if now is None else int(now)


def _whole(variable, default, low, high):
    raw = os.environ.get(variable)
    text = "" if raw is None else raw.strip()
    if not text:
        return default
    if not (text.isascii() and text.isdecimal()) or not low <= int(text) <= high:
        raise SessionConfigError(f"{variable} debe ser un numero entero entre {low} y {high} "
                                 f"(valor actual: {text[:20]!r}).")
    return int(text)


def lifetimes():
    """(idle seconds, absolute seconds). SessionConfigError when the settings are invalid."""
    idle = _whole(IDLE_VARIABLE, IDLE_DEFAULT, *IDLE_RANGE) * 60
    absolute = _whole(MAX_VARIABLE, MAX_DEFAULT, *MAX_RANGE) * 3600
    if idle > absolute:
        raise SessionConfigError(f"{IDLE_VARIABLE} no puede durar mas que {MAX_VARIABLE}.")
    return idle, absolute


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for(token):
    return hmac.new(token.encode("utf-8"), b"itglasses-web-csrf-v1", hashlib.sha256).hexdigest()


def check_csrf(token, supplied):
    return bool(token and supplied) and hmac.compare_digest(csrf_for(token), str(supplied))


def create(db, user_id, ip=None, now=None):
    """(token, max_age) of a new session for an active user."""
    now = _now(now)
    idle, absolute = lifetimes()
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO web_sessions(token_digest,user_id,csrf_digest,created_at,last_seen,idle_expires,"
               "absolute_expires,ip) VALUES(?,?,?,?,?,?,?,?)",
               (_digest(token), user_id, _digest(csrf_for(token)), now, now, now + idle, now + absolute, ip))
    cleanup(db, now)
    return token, absolute


def resolve(db, token, now=None):
    """The session and its (active) user, or None. Slides the inactivity expiry, never past the absolute one."""
    if not token or len(token) > TOKEN_MAX:
        return None
    now = _now(now)
    row = db.execute(
        "SELECT s.token_digest,s.user_id,s.last_seen,s.idle_expires,s.absolute_expires,s.revoked_at,"
        "u.username,u.display_name,u.role,u.status,u.must_change_pw "
        "FROM web_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_digest=?", (_digest(token),)).fetchone()
    if (row is None or row["revoked_at"] is not None or row["idle_expires"] <= now
            or row["absolute_expires"] <= now or row["status"] != "active"):
        return None
    idle_expires = row["idle_expires"]
    if now - row["last_seen"] >= TOUCH_INTERVAL:
        idle, _ = lifetimes()
        idle_expires = min(now + idle, row["absolute_expires"])
        db.execute("UPDATE web_sessions SET last_seen=?,idle_expires=? WHERE token_digest=?",
                   (now, idle_expires, row["token_digest"]))
    return {"user_id": row["user_id"], "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "must_change_pw": bool(row["must_change_pw"]),
            "idle_expires": idle_expires, "absolute_expires": row["absolute_expires"]}


def revoke(db, token, now=None):
    if token and len(token) <= TOKEN_MAX:
        db.execute("UPDATE web_sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL",
                   (_now(now), _digest(token)))


def cleanup(db, now=None):
    limit = _now(now) - RETENTION
    db.execute("DELETE FROM web_sessions WHERE absolute_expires<? OR (revoked_at IS NOT NULL AND revoked_at<?)",
               (limit, limit))


# --- login --------------------------------------------------------------------------------------------------

def _dummy_hash():
    global _dummy
    if _dummy is None:
        _dummy = passwords.hash_password(secrets.token_urlsafe(16))
    return _dummy


def _lock_for(failures):
    if failures % MAX_FAILURES:
        return None
    level = min(failures // MAX_FAILURES - 1, 10)
    return min(LOCK_SECONDS * 2 ** level, LOCK_MAX_SECONDS)


def _ip_bucket(ip):
    return "login-fail:" + str(ip or "unknown")


def ip_blocked(db, ip, now=None):
    now = _now(now)
    db.execute("DELETE FROM limits WHERE expires<=?", (now,))
    row = db.execute("SELECT count FROM limits WHERE bucket=?", (_ip_bucket(ip),)).fetchone()
    return bool(row and row[0] >= LOGIN_IP_FAILURES)


def _ip_failure(db, ip, now):
    db.execute("INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
               (_ip_bucket(ip), now + LOGIN_IP_WINDOW))


def authenticate(db, username, password, ip=None, now=None):
    """The user row on success, None on ANY failure (unknown user, wrong password, locked or disabled account),
    with the same cost for every reason. Updates the counters, the lockout and the audit log; the caller commits
    the transaction even when the login fails."""
    now = _now(now)
    password = password if isinstance(password, str) else ""
    try:
        name = accounts.normalize_username(username)
    except accounts.AccountError:
        name = None
    row = None
    if name:
        row = db.execute("SELECT id,username,role,status,pw_salt,pw_hash,pw_params,failed_count,locked_until "
                         "FROM users WHERE username=?", (name,)).fetchone()
    if row is None:
        # A request waiting for approval is checked instead of the dummy hash: one scrypt either way.
        from . import registration
        waiting = registration.find_for_login(db, name, now) if name else None
        if waiting is not None:
            if passwords.verify_password(password, waiting["pw_salt"], waiting["pw_hash"], waiting["pw_params"]):
                audit.record(db, "auth.login", actor_kind="user", outcome="denied", ip=ip, now=now,
                             target_type="registration", target_id=waiting["id"], detail={"reason": "pending"})
                return PENDING
        else:
            passwords.verify_password(password, *_dummy_hash())
        _ip_failure(db, ip, now)
        # The typed name is not stored: people sometimes type their password in the user field.
        audit.record(db, "auth.login", actor_kind="user", outcome="denied", ip=ip, now=now,
                     detail={"reason": "unknown_user"})
        return None
    target = {"actor_user_id": row["id"], "target_type": "user", "target_id": row["id"]}
    if row["locked_until"] and row["locked_until"] > now:
        passwords.verify_password(password, *_dummy_hash())
        _ip_failure(db, ip, now)
        audit.record(db, "auth.login", actor_kind="user", outcome="denied", ip=ip, now=now,
                     detail={"reason": "locked"}, **target)
        return None
    correct = passwords.verify_password(password, row["pw_salt"], row["pw_hash"], row["pw_params"])
    if correct and row["status"] == "active":
        db.execute("UPDATE users SET failed_count=0,locked_until=NULL,last_login_at=? WHERE id=?", (now, row["id"]))
        if passwords.needs_rehash(row["pw_params"]):
            salt, digest, params = passwords.hash_password(password)
            db.execute("UPDATE users SET pw_salt=?,pw_hash=?,pw_params=? WHERE id=?",
                       (salt, digest, params, row["id"]))
        audit.record(db, "auth.login", actor_kind="user", ip=ip, now=now, **target)
        return row
    _ip_failure(db, ip, now)
    if not correct:
        failures = row["failed_count"] + 1
        lock = _lock_for(failures)
        db.execute("UPDATE users SET failed_count=?,locked_until=? WHERE id=?",
                   (failures, now + lock if lock else row["locked_until"], row["id"]))
        audit.record(db, "auth.login", actor_kind="user", outcome="denied", ip=ip, now=now,
                     detail={"reason": "bad_password"}, **target)
        if lock:
            audit.record(db, "auth.locked", actor_kind="system", ip=ip, now=now, target_type="user",
                         target_id=row["id"], detail={"failures": failures, "minutes": lock // 60})
    else:
        audit.record(db, "auth.login", actor_kind="user", outcome="denied", ip=ip, now=now,
                     detail={"reason": "disabled"}, **target)
    return None


def verify_current_password(db, user_id, password, ip=None, now=None):
    """For a password change from a session: a wrong current password counts towards the lockout too."""
    now = _now(now)
    row = db.execute("SELECT pw_salt,pw_hash,pw_params,failed_count FROM users WHERE id=?", (user_id,)).fetchone()
    if row and passwords.verify_password(password if isinstance(password, str) else "", row["pw_salt"],
                                         row["pw_hash"], row["pw_params"]):
        return True
    if row:
        failures = row["failed_count"] + 1
        lock = _lock_for(failures)
        db.execute("UPDATE users SET failed_count=?,locked_until=COALESCE(?,locked_until) WHERE id=?",
                   (failures, now + lock if lock else None, user_id))
        if lock:  # a locked account loses its sessions too
            accounts.revoke_sessions(db, user_id, now)
            audit.record(db, "auth.locked", actor_kind="system", ip=ip, now=now, target_type="user",
                         target_id=user_id, detail={"failures": failures, "minutes": lock // 60})
    return False
