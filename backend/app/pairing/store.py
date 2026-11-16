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
SCHEMA_VERSION = 1

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

_ready = set()
_ready_lock = threading.Lock()


class SchemaError(RuntimeError):
    """The database was written by a newer, unsupported version."""


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024**2)


def database_path():
    configured = os.environ.get("ITGLASSES_PAIRING_DB", "").strip()
    return Path(configured) if configured else DEFAULT_DB


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
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def ensure_database(path=None):
    """Create the database if missing and migrate it once per process."""
    path = Path(path or database_path()).resolve()
    with _ready_lock:
        if path in _ready and path.is_file():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5, isolation_level=None)) as db:
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
    except (OSError, sqlite3.Error, SchemaError):
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
