"""Chat history storage: its own database, storage/history/history.sqlite3 (ITGLASSES_HISTORY_DB).

Kept apart from the device database (storage/pairing/devices.sqlite3) on purpose: going back to an older backend
never meets a newer device schema, the content of the chats lives in its own file, and purging or compacting the
history never blocks the phones' authentication. There are no foreign keys to users or devices across the two
files: users are never deleted (only disabled) and a conversation keeps its device label when a device is removed.

Text only, never audio. Created and migrated automatically; an existing database is copied to backups/ (and the
copy verified) before any migration, exactly like the device database.
"""
import logging
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

from backend.app.pairing.store import PROJECT_ROOT, BackupError, SchemaError, backup_database

logger = logging.getLogger(__name__)

DEFAULT_DB = PROJECT_ROOT / "storage" / "history" / "history.sqlite3"
SCHEMA_VERSION = 1

# Version 1: conversations (web or one device, always with an owner) and their messages.
V1_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 device_id TEXT,
 device_label TEXT,
 channel TEXT NOT NULL CHECK(channel IN ('web','device')),
 title TEXT,
 title_source TEXT NOT NULL DEFAULT 'auto' CHECK(title_source IN ('auto','user')),
 created_at INTEGER NOT NULL,
 last_message_at INTEGER NOT NULL,
 message_count INTEGER NOT NULL DEFAULT 0,
 state_json TEXT,
 closed_at INTEGER,
 deleted_at INTEGER,
 purge_after INTEGER,
 CHECK((channel = 'device') = (device_id IS NOT NULL))
)""",
    "CREATE INDEX IF NOT EXISTS conversation_user ON conversations(user_id,deleted_at,last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS conversation_device ON conversations(device_id,last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS conversation_purge ON conversations(purge_after) WHERE purge_after IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 content TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('web','phone','glasses')),
 route TEXT,
 sources_json TEXT,
 created_at INTEGER NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id,id)",
)

_ready = set()
_ready_lock = threading.Lock()


class HistoryUnavailable(RuntimeError):
    """The history database cannot be opened or migrated (the chat itself keeps working)."""


def database_path():
    configured = os.environ.get("ITGLASSES_HISTORY_DB", "").strip()
    return Path(configured) if configured else DEFAULT_DB


def migrate(db):
    """Bring a connection opened with isolation_level=None to SCHEMA_VERSION atomically."""
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaError(f"history database version {version} is newer than {SCHEMA_VERSION}")
        if version < 1:
            for statement in V1_SCHEMA:
                db.execute(statement)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def ensure_database(path=None):
    """Create the database if missing and migrate it once per process (backup first, as for the devices)."""
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
                logger.info("History database backed up before migration from=%d to=%d file=%s",
                            version, SCHEMA_VERSION, copy.name)
            migrate(db)
        _ready.add(path)
    return path


@contextmanager
def transaction(path=None):
    """One write transaction (BEGIN IMMEDIATE) with foreign keys on, so deleting a conversation deletes its
    messages. HistoryUnavailable when the database cannot be used."""
    try:
        path = ensure_database(path)
    except (OSError, sqlite3.Error, SchemaError, BackupError) as error:
        logger.exception("History database is unavailable")
        raise HistoryUnavailable(type(error).__name__) from None
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        # A purged conversation is overwritten in the file, not only unlinked (stage "historial" 4).
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        yield db
