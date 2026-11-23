"""Device registry storage. The database is created and migrated automatically.

Optional pilot admin password (offline): python -m backend.app.pairing.store PATH
"""
import getpass
import hashlib
import logging
import os
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager, closing
from pathlib import Path

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# storage/ is ignored by git, so the registry never ends up in a commit.
DEFAULT_DB = PROJECT_ROOT / "storage" / "pairing" / "devices.sqlite3"
SCHEMA_VERSION = 2
# Copies taken before every migration (and before a restore) live next to the database, under storage/.
BACKUP_DIR_NAME = "backups"

# Version 0: the original pilot schema. Every database starts here and is
# migrated forward, so new and old databases always converge on one shape.
BASE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB, digest BLOB)",
    "CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS installs (
 id TEXT PRIMARY KEY, key_id TEXT NOT NULL, variant TEXT NOT NULL, code TEXT,
 state TEXT NOT NULL, expires INTEGER NOT NULL, token TEXT UNIQUE, token_exp INTEGER,
 nonce TEXT, nonce_exp INTEGER
)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS open_code ON installs(code) WHERE code IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS install_key ON installs(key_id,state)",
    "CREATE TABLE IF NOT EXISTS seen (key_id TEXT, jti TEXT, expires INTEGER, PRIMARY KEY(key_id,jti))",
    "CREATE INDEX IF NOT EXISTS seen_exp ON seen(expires)",
    "CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, count INTEGER, expires INTEGER)",
)

# Version 1: descriptive data for the admin panel. Informative only: the
# identity of a device is always its Keystore public key (installs.key_id).
V1_COLUMNS = (
    ("name", "TEXT"), ("model", "TEXT"), ("created_at", "INTEGER"),
    ("approved_at", "INTEGER"), ("last_seen", "INTEGER"), ("last_ip", "TEXT"),
)

# Version 2: web users and roles (stage "usuarios"). users, server-side web sessions and the admin audit log,
# plus the owner of every device. Chat history (later) keys on users.id and installs.id.
V2_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY,
 username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 display_name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('superadmin','admin','user')),
 pw_salt BLOB NOT NULL, pw_hash BLOB NOT NULL, pw_params TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK(must_change_pw IN (0,1)),
 created_at INTEGER NOT NULL, created_by TEXT, updated_at INTEGER,
 pw_changed_at INTEGER, last_login_at INTEGER,
 failed_count INTEGER NOT NULL DEFAULT 0, locked_until INTEGER
)""",
    """CREATE TABLE IF NOT EXISTS web_sessions (
 token_digest TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id),
 csrf_digest TEXT NOT NULL,
 created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
 idle_expires INTEGER NOT NULL, absolute_expires INTEGER NOT NULL,
 ip TEXT, revoked_at INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS web_session_user ON web_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS web_session_exp ON web_sessions(absolute_expires)",
    """CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 at INTEGER NOT NULL,
 actor_user_id TEXT,
 actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','script','system')),
 action TEXT NOT NULL, target_type TEXT, target_id TEXT, detail TEXT, ip TEXT,
 outcome TEXT NOT NULL CHECK(outcome IN ('ok','denied','error'))
)""",
    "CREATE INDEX IF NOT EXISTS audit_at ON audit_log(at)",
    "CREATE INDEX IF NOT EXISTS audit_actor ON audit_log(actor_user_id,at)",
)
# Owner of the device (NULL until assigned: an unassigned active device keeps working).
V2_INSTALL_COLUMNS = (
    ("user_id", "TEXT REFERENCES users(id)"), ("assigned_at", "INTEGER"), ("assigned_by", "TEXT"),
)
V2_INDEXES = ("CREATE INDEX IF NOT EXISTS install_user ON installs(user_id)",)

_ready = set()
_ready_lock = threading.Lock()


class SchemaError(RuntimeError):
    """The database was written by a newer, unsupported version."""


class BackupError(RuntimeError):
    """The copy taken before a migration could not be made or verified: nothing is migrated."""


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024**2)


def database_path():
    configured = os.environ.get("ITGLASSES_PAIRING_DB", "").strip()
    return Path(configured) if configured else DEFAULT_DB


def backup_dir(path):
    return Path(path).resolve().parent / BACKUP_DIR_NAME


def backup_database(db, path, version, label=""):
    """Consistent copy of an open database (SQLite backup API) in backups/, verified before it is trusted.

    Name: <stem>-v<version>[-<label>]-<UTC time>-<random>.sqlite3. Raises BackupError when it cannot be verified."""
    folder = backup_dir(path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    middle = f"-{label}" if label else ""
    target = folder / f"{Path(path).stem}-v{version}{middle}-{stamp}-{secrets.token_hex(3)}.sqlite3"
    try:
        with closing(sqlite3.connect(target)) as copy:
            db.backup(copy)
            healthy = copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            copied = copy.execute("PRAGMA user_version").fetchone()[0]
    except (OSError, sqlite3.Error) as error:
        raise BackupError(f"device database backup failed: {type(error).__name__}") from error
    if not healthy or copied != version:
        raise BackupError("device database backup could not be verified")
    return target


def migrate(db):
    """Bring a connection opened with isolation_level=None to SCHEMA_VERSION atomically."""
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaError(f"device database version {version} is newer than {SCHEMA_VERSION}")
        if version < 1:
            for statement in BASE_SCHEMA:
                db.execute(statement)
            existing = {row[1] for row in db.execute("PRAGMA table_info(installs)")}
            for name, kind in V1_COLUMNS:
                if name not in existing:
                    db.execute(f"ALTER TABLE installs ADD COLUMN {name} {kind}")
        if version < 2:
            for statement in V2_SCHEMA:
                db.execute(statement)
            existing = {row[1] for row in db.execute("PRAGMA table_info(installs)")}
            for name, kind in V2_INSTALL_COLUMNS:
                if name not in existing:
                    db.execute(f"ALTER TABLE installs ADD COLUMN {name} {kind}")
            for statement in V2_INDEXES:
                db.execute(statement)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def ensure_database(path=None):
    """Create the database if missing and migrate it once per process.

    An existing database older than SCHEMA_VERSION is first copied to backups/ (see backup_database); if the copy
    fails, nothing is migrated. A new database needs no copy. Revert: python -m backend.app.pairing.restore."""
    path = Path(path or database_path()).resolve()
    with _ready_lock:
        if path in _ready and path.is_file():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.is_file() and path.stat().st_size > 0
        with closing(sqlite3.connect(path, timeout=5, isolation_level=None)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if existed and version < SCHEMA_VERSION:
                copy = backup_database(db, path, version)
                logger.info("Device database backed up before migration from=%d to=%d file=%s",
                            version, SCHEMA_VERSION, copy.name)
            migrate(db)
        _ready.add(path)
    return path


def provision(path, password):
    if not 14 <= len(password) <= 256:
        raise ValueError("Use 14 to 256 characters")
    salt = secrets.token_bytes(16)
    path = ensure_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO admin VALUES(1,?,?)", (salt, password_hash(password, salt)))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin(request: Request):
    configured = os.getenv("ITGLASSES_PAIRING_ORIGIN", "")
    if not configured:
        raise HTTPException(404)
    if request.url.scheme != "https" or configured != f"https://{request.url.netloc}":
        raise HTTPException(403)
    return configured


def permitted_browser_origin(request):
    supplied = request.headers.get("origin")
    if supplied in (None, "http://localhost:8000", "http://127.0.0.1:8000"):
        return True
    return (request.url.scheme == "https" and supplied == os.getenv("ITGLASSES_PAIRING_ORIGIN")
            and supplied == f"https://{request.url.netloc}")


@contextmanager
def transaction():
    try:
        path = ensure_database()
    except (OSError, sqlite3.Error, SchemaError, BackupError):
        logger.exception("Device database is unavailable")
        raise HTTPException(503) from None
    with closing(sqlite3.connect(path.as_uri()+"?mode=rw", uri=True, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        yield db


def throttle(request, name, per_ip, total, window):
    now = int(time.time())
    peer = request.client.host if request.client else "unknown"
    with transaction() as db:
        db.execute("DELETE FROM limits WHERE expires<=?", (now,))
        for key, maximum in ((name,total), (name+":"+peer,per_ip)):
            row = db.execute("SELECT count FROM limits WHERE bucket=?", (key,)).fetchone()
            if row and row[0] >= maximum:
                raise HTTPException(429, headers={"Retry-After":str(window)})
            db.execute("INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
                       (key,now+window))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m backend.app.pairing.store DATABASE")
    password = getpass.getpass("New ITGlasses administrator password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords differ")
    provision(sys.argv[1], password)
