import sqlite3
from contextlib import closing

import pytest
from fastapi import HTTPException

from backend.app.pairing import store

# Exact schema written by the pilot before versioning existed (user_version 0).
PILOT_SCHEMA = """
CREATE TABLE admin (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB, digest BLOB);
CREATE TABLE sessions (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE installs (
 id TEXT PRIMARY KEY, key_id TEXT NOT NULL, variant TEXT NOT NULL, code TEXT,
 state TEXT NOT NULL, expires INTEGER NOT NULL, token TEXT UNIQUE, token_exp INTEGER,
 nonce TEXT, nonce_exp INTEGER
);
CREATE UNIQUE INDEX open_code ON installs(code) WHERE code IS NOT NULL;
CREATE INDEX install_key ON installs(key_id,state);
CREATE TABLE seen (key_id TEXT, jti TEXT, expires INTEGER, PRIMARY KEY(key_id,jti));
CREATE INDEX seen_exp ON seen(expires);
CREATE TABLE limits (bucket TEXT PRIMARY KEY, count INTEGER, expires INTEGER);
"""
NEW_COLUMNS = {"name", "model", "created_at", "approved_at", "last_seen", "last_ip"}


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(store, "_ready", set())


def inspect(path):
    with closing(sqlite3.connect(path)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        columns = [row[1] for row in db.execute("PRAGMA table_info(installs)")]
    return version, columns


def test_database_is_created_on_first_use(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "devices.sqlite3"
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM installs").fetchone()[0] == 0
    version, columns = inspect(path)
    assert version == store.SCHEMA_VERSION
    assert NEW_COLUMNS <= set(columns)


def test_default_location_is_used_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_PAIRING_DB", raising=False)
    monkeypatch.setattr(store, "DEFAULT_DB", tmp_path / "storage" / "pairing" / "devices.sqlite3")
    assert store.database_path() == store.DEFAULT_DB
    store.ensure_database()
    assert store.DEFAULT_DB.is_file()


def test_default_location_lives_under_ignored_storage():
    assert store.DEFAULT_DB.relative_to(store.PROJECT_ROOT).parts[0] == "storage"


def test_pilot_database_is_migrated_without_losing_rows(tmp_path):
    path = tmp_path / "pilot.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(PILOT_SCHEMA)
        db.execute("INSERT INTO admin VALUES(1,?,?)", (b"salt", b"digest"))
        db.execute("INSERT INTO installs(id,key_id,variant,code,state,expires) "
                   "VALUES('rid-1','key-1','phone',NULL,'active',123)")
    store.ensure_database(path)
    version, columns = inspect(path)
    assert version == store.SCHEMA_VERSION and NEW_COLUMNS <= set(columns)
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM installs").fetchone()
        assert (row["id"], row["key_id"], row["state"], row["expires"]) == ("rid-1", "key-1", "active", 123)
        assert row["name"] is None and row["last_seen"] is None
        assert db.execute("SELECT COUNT(*) FROM admin").fetchone()[0] == 1


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "devices.sqlite3"
    store.ensure_database(path)
    store._ready.clear()
    store.ensure_database(path)
    version, columns = inspect(path)
    assert version == store.SCHEMA_VERSION
    assert len(columns) == len(set(columns))


def test_newer_database_is_refused_and_left_untouched(tmp_path, monkeypatch):
    path = tmp_path / "future.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE marker (x)")
        db.execute("PRAGMA user_version=99")
    with pytest.raises(store.SchemaError):
        store.ensure_database(path)
    assert inspect(path) == (99, [])
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    with pytest.raises(HTTPException) as failure:
        with store.transaction():
            pass
    assert failure.value.status_code == 503


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setattr(store, "V1_COLUMNS", store.V1_COLUMNS + (("broken)", "TEXT"),))
    with pytest.raises(sqlite3.OperationalError):
        store.ensure_database(path)
    assert inspect(path) == (0, [])


def test_pilot_password_provisioning_still_works(tmp_path):
    path = tmp_path / "devices.sqlite3"
    store.ensure_database(path)
    store.provision(str(path), "only-for-this-test")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM admin").fetchone()[0] == 1
    assert inspect(path)[0] == store.SCHEMA_VERSION
