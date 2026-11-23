"""Stage 1 (usuarios): device database schema v2, automatic backup before migrating and restore (revert)."""
import sqlite3
from contextlib import closing

import pytest

from backend.app.pairing import restore, store
from test_pairing_store import PILOT_SCHEMA, inspect

V1_COLUMNS_SQL = ("ALTER TABLE installs ADD COLUMN name TEXT", "ALTER TABLE installs ADD COLUMN model TEXT",
                  "ALTER TABLE installs ADD COLUMN created_at INTEGER",
                  "ALTER TABLE installs ADD COLUMN approved_at INTEGER",
                  "ALTER TABLE installs ADD COLUMN last_seen INTEGER", "ALTER TABLE installs ADD COLUMN last_ip TEXT")
V2_TABLES = {"users", "web_sessions", "audit_log"}
V2_COLUMNS = {"user_id", "assigned_at", "assigned_by"}


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(store, "_ready", set())


def v1_database(path, state="active"):
    """A database exactly as the v1 code (tag voz-rapida-v3) leaves it, with one approved phone."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(PILOT_SCHEMA)
        for statement in V1_COLUMNS_SQL:
            db.execute(statement)
        db.execute("INSERT INTO installs(id,key_id,variant,code,state,expires,token,name) "
                   "VALUES('rid-1','key-1','phone',NULL,?,123,'digest-1','Pixel')", (state,))
        db.execute("PRAGMA user_version=1")
    return path


def tables(path):
    with closing(sqlite3.connect(path)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def backups(path):
    folder = store.backup_dir(path)
    return sorted(folder.glob("*.sqlite3")) if folder.is_dir() else []


def test_schema_version_is_2():
    assert store.SCHEMA_VERSION == 2


def test_new_database_has_the_v2_tables_and_needs_no_backup(tmp_path):
    path = tmp_path / "devices.sqlite3"
    store.ensure_database(path)
    version, columns = inspect(path)
    assert version == 2 and V2_COLUMNS <= set(columns)
    assert V2_TABLES <= tables(path)
    assert backups(path) == []


def test_v1_database_is_backed_up_then_migrated_without_losing_devices(tmp_path):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    assert inspect(path)[0] == 2 and V2_TABLES <= tables(path)
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM installs").fetchone()
    assert (row["id"], row["key_id"], row["state"], row["token"], row["name"]) == (
        "rid-1", "key-1", "active", "digest-1", "Pixel")
    assert row["user_id"] is None and row["assigned_at"] is None  # assigned later by create-superadmin
    [copy] = backups(path)
    assert copy.name.startswith("devices-v1-")
    assert inspect(copy)[0] == 1 and not (V2_TABLES & tables(copy))
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("SELECT id,token FROM installs").fetchall() == [("rid-1", "digest-1")]


def test_pilot_database_v0_is_backed_up_too(tmp_path):
    path = tmp_path / "pilot.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(PILOT_SCHEMA)
    store.ensure_database(path)
    assert inspect(path)[0] == 2
    assert [c.name.startswith("pilot-v0-") for c in backups(path)] == [True]


def test_migrating_twice_takes_a_single_backup(tmp_path):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    store._ready.clear()
    store.ensure_database(path)
    assert len(backups(path)) == 1
    assert len(inspect(path)[1]) == len(set(inspect(path)[1]))


def test_no_backup_means_no_migration(tmp_path, monkeypatch):
    path = v1_database(tmp_path / "devices.sqlite3")

    def broken(*_args, **_kwargs):
        raise store.BackupError("disk full")

    monkeypatch.setattr(store, "backup_database", broken)
    with pytest.raises(store.BackupError):
        store.ensure_database(path)
    assert inspect(path)[0] == 1 and not (V2_TABLES & tables(path))


def test_backup_failure_makes_the_api_answer_503(tmp_path, monkeypatch):
    path = v1_database(tmp_path / "devices.sqlite3")
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setattr(store, "backup_database",
                        lambda *a, **k: (_ for _ in ()).throw(store.BackupError("x")))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        with store.transaction():
            pass
    assert failure.value.status_code == 503


def test_a_newer_database_is_neither_backed_up_nor_touched(tmp_path):
    path = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(store.SchemaError):
        store.ensure_database(path)
    assert backups(path) == []


def test_v2_constraints(tmp_path):
    path = tmp_path / "devices.sqlite3"
    store.ensure_database(path)
    user = ("u1", "Ana", "Ana", "user", b"s", b"h", "scrypt:n=32768,r=8,p=1", 1)
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", user)
        with pytest.raises(sqlite3.IntegrityError):  # usernames are unique regardless of case
            db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,created_at) "
                       "VALUES('u2','ANA','x','user',x'00',x'00','p',1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,created_at) "
                       "VALUES('u3','root','x','owner',x'00',x'00','p',1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO audit_log(at,actor_kind,action,outcome) VALUES(1,'robot','x','ok')")
        row = db.execute("SELECT status,must_change_pw,failed_count FROM users WHERE id='u1'").fetchone()
    assert row == ("active", 0, 0)


# --- Restore (revert) -------------------------------------------------------------------------------------

@pytest.fixture
def stopped_backend(monkeypatch):
    monkeypatch.setattr(restore, "backend_running", lambda port=None: False)


def test_restore_brings_back_the_v1_database_and_keeps_a_copy_of_v2(tmp_path, stopped_backend):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE installs SET name='after migration'")
    [v1_copy] = backups(path)
    version, safety = restore.restore(v1_copy, path)
    assert version == 1 and inspect(path)[0] == 1 and not (V2_TABLES & tables(path))
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name FROM installs").fetchone()[0] == "Pixel"
    assert "-v2-pre-restore-" in safety.name and inspect(safety)[0] == 2
    # The new code migrates it again (with a new backup) when it next opens it.
    store.ensure_database(path)
    assert inspect(path)[0] == 2 and len(backups(path)) == 3


def test_restore_cli_refuses_while_the_backend_runs(tmp_path, monkeypatch, capsys):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    [copy] = backups(path)
    monkeypatch.setattr(restore, "backend_running", lambda port=None: True)
    assert restore.main([str(copy), "--db", str(path), "--yes"]) == 2
    assert inspect(path)[0] == 2
    assert "Detenlo primero" in capsys.readouterr().out


def test_restore_cli_needs_the_confirmation_word(tmp_path, stopped_backend):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    [copy] = backups(path)
    assert restore.main([str(copy), "--db", str(path)], ask=lambda _prompt: "si") == 1
    assert inspect(path)[0] == 2
    assert restore.main([str(copy), "--db", str(path)], ask=lambda _prompt: "RESTAURAR") == 0
    assert inspect(path)[0] == 1


@pytest.mark.parametrize("content", [b"", b"not a database at all"])
def test_restore_rejects_invalid_files(tmp_path, stopped_backend, content):
    path = v1_database(tmp_path / "devices.sqlite3")
    bogus = tmp_path / "bogus.sqlite3"
    bogus.write_bytes(content)
    assert restore.main([str(bogus), "--db", str(path), "--yes"]) == 3
    assert inspect(path)[0] == 1


def test_restore_rejects_a_foreign_or_newer_database(tmp_path, stopped_backend):
    path = v1_database(tmp_path / "devices.sqlite3")
    foreign = tmp_path / "other.sqlite3"
    with closing(sqlite3.connect(foreign)) as db, db:
        db.execute("CREATE TABLE things (x)")
    with pytest.raises(restore.RestoreError):
        restore.inspect_backup(foreign)
    newer = v1_database(tmp_path / "newer.sqlite3")
    with closing(sqlite3.connect(newer)) as db, db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(restore.RestoreError):
        restore.restore(newer, path)


def test_list_shows_backups_with_their_version(tmp_path, capsys):
    path = v1_database(tmp_path / "devices.sqlite3")
    store.ensure_database(path)
    assert restore.main(["--list", "--db", str(path)]) == 0
    assert "v1  " in capsys.readouterr().out
