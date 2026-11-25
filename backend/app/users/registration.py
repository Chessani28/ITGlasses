"""Self-registration of web accounts (ITGLASSES_REGISTRATION = off | approval | open, default approval).

A request in "approval" mode waits in the table registrations (device database, schema still v2: an older tag
simply ignores the table) until an admin approves it (moved to users with the same scrypt hash, in one
transaction) or rejects it (deleted). Pending requests expire after PENDING_DAYS. The role is always "user",
set here; the request body cannot carry one (routes.RegisterBody forbids extra fields).

A username is unique across users AND registrations. Whether a name is taken is never told in "approval" mode
(the answer is the same and costs the same scrypt); in "open" mode a taken name is refused with a generic
message, since a free one logs in at once. Nothing here returns or logs a password or a hash."""
import sqlite3
import time
import uuid

from . import accounts, passwords
from .access import _choice

MODE_VARIABLE, MODES, MODE_DEFAULT = "ITGLASSES_REGISTRATION", ("off", "approval", "open"), "approval"
PENDING_DAYS = 30
# Every registration attempt from one client address counts, accepted or not.
RATE_PER_IP, RATE_WINDOW = 5, 3600
PUBLIC_FIELDS = "id,username,display_name,created_at,expires_at"


def mode():
    """off, approval or open; an invalid value refuses to start (AccessConfigError), like the loopback mode."""
    return _choice(MODE_VARIABLE, MODES, MODE_DEFAULT)


def _now(now):
    return int(time.time()) if now is None else int(now)


def purge_expired(db, now=None):
    return db.execute("DELETE FROM registrations WHERE expires_at<=?", (_now(now),)).rowcount


def _bucket(ip):
    return "register:" + str(ip or "unknown")


def rate_limited(db, ip, now=None):
    """Counts this attempt; True when the address already made RATE_PER_IP attempts in the window."""
    now = _now(now)
    db.execute("DELETE FROM limits WHERE expires<=?", (now,))
    row = db.execute("SELECT count FROM limits WHERE bucket=?", (_bucket(ip),)).fetchone()
    if row and row[0] >= RATE_PER_IP:
        return True
    db.execute("INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
               (_bucket(ip), now + RATE_WINDOW))
    return False


def name_taken(db, username):
    return bool(db.execute("SELECT 1 FROM users WHERE username=? UNION ALL SELECT 1 FROM registrations "
                           "WHERE username=? LIMIT 1", (username, username)).fetchone())


def validate(username, display_name, password):
    """(username, display name) normalized; AccountError or PasswordPolicyError for a bad value."""
    name = accounts.normalize_username(username)
    display = accounts.clean_display_name(display_name, name)
    passwords.check_policy(password)
    return name, display


def request_account(db, username, display_name, password, ip=None, now=None):
    """'approval' mode: ('pending', id) or ('taken', None). The password is hashed in both cases (same cost)."""
    now = _now(now)
    purge_expired(db, now)
    name, display = validate(username, display_name, password)
    salt, digest, params = passwords.hash_password(password)
    if name_taken(db, name):
        return "taken", None
    registration_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO registrations(id,username,display_name,pw_salt,pw_hash,pw_params,created_at,"
                   "expires_at,ip) VALUES(?,?,?,?,?,?,?,?,?)",
                   (registration_id, name, display, salt, digest, params, now, now + PENDING_DAYS * 86400, ip))
    except sqlite3.IntegrityError:
        return "taken", None
    return "pending", registration_id


def create_open(db, username, display_name, password, now=None):
    """'open' mode: ('created', user id) or ('taken', None). Role "user", nothing else."""
    now = _now(now)
    purge_expired(db, now)
    name, display = validate(username, display_name, password)
    if name_taken(db, name):
        passwords.hash_password(password)  # same cost as a created account
        return "taken", None
    try:
        return "created", accounts.create_user(db, name, password, "user", display, created_by=None, now=now)
    except accounts.AccountError as error:
        if error.code == "username_taken":
            return "taken", None
        raise


def pending(db, now=None):
    purge_expired(db, now)
    return [dict(row) for row in db.execute(f"SELECT {PUBLIC_FIELDS} FROM registrations ORDER BY created_at")]


def get(db, registration_id, now=None):
    purge_expired(db, now)
    return db.execute(f"SELECT {PUBLIC_FIELDS} FROM registrations WHERE id=?", (registration_id,)).fetchone()


def approve(db, registration_id, approved_by, now=None):
    """Id of the new active user. One transaction (the caller's): insert in users with the stored scrypt hash as it
    is (never re-hashed), then delete the request. None if the request does not exist or expired."""
    now = _now(now)
    purge_expired(db, now)
    row = db.execute("SELECT id,username,display_name,pw_salt,pw_hash,pw_params FROM registrations WHERE id=?",
                     (registration_id,)).fetchone()
    if row is None:
        return None
    user_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,must_change_pw,"
                   "created_at,created_by,updated_at,pw_changed_at) VALUES(?,?,?,'user',?,?,?,0,?,?,?,?)",
                   (user_id, row["username"], row["display_name"], row["pw_salt"], row["pw_hash"], row["pw_params"],
                    now, approved_by, now, now))
    except sqlite3.IntegrityError:
        raise accounts.AccountError("username_taken", "Ya existe un usuario con ese nombre.") from None
    db.execute("DELETE FROM registrations WHERE id=?", (registration_id,))
    return user_id


def reject(db, registration_id, now=None):
    """The deleted request (id, username, ...) or None."""
    row = get(db, registration_id, now)
    if row is not None:
        db.execute("DELETE FROM registrations WHERE id=?", (registration_id,))
    return row


def find_for_login(db, username, now=None):
    """The pending request for this username (with its hash), for sessions.authenticate; None otherwise."""
    return db.execute("SELECT id,username,pw_salt,pw_hash,pw_params FROM registrations WHERE username=? "
                      "AND expires_at>?", (username, _now(now))).fetchone()
