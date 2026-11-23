"""Stage "historial" 1: the chat-history database, generalized backup/restore, backup sets and daily backups."""
import json
import logging
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app import backups
from backend.app.history import store as history
from backend.app.pairing import restore, store as devices
from test_pairing_schema_v2 import v1_database

TABLES = {"conversations", "messages"}


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(history, "_ready", set())
    monkeypatch.setattr(devices, "_ready", set())
    for name in (backups.ENABLED_VARIABLE, backups.KEEP_VARIABLE):
        monkeypatch.delenv(name, raising=False)


def tables(path):
    with closing(sqlite3.connect(path)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def version(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def conversation(db, conversation_id="c1", channel="web", device_id=None, user_id="u1"):
    db.execute("INSERT INTO conversations(id,user_id,device_id,channel,created_at,last_message_at) VALUES(?,?,?,?,1,1)",
               (conversation_id, user_id, device_id, channel))


# --- the history database ---------------------------------------------------------------------------------

def test_default_location_is_its_own_file_under_storage(monkeypatch):
    monkeypatch.delenv("ITGLASSES_HISTORY_DB", raising=False)
    assert history.database_path() == history.DEFAULT_DB != devices.DEFAULT_DB
    assert history.DEFAULT_DB.relative_to(history.PROJECT_ROOT).parts[:2] == ("storage", "history")


def test_new_history_database(tmp_path):
    path = tmp_path / "nested" / "history.sqlite3"
    history.ensure_database(path)
    assert version(path) == 1 and TABLES <= tables(path)
    assert not (path.parent / "backups").exists()  # a new database needs no backup


def test_constraints_and_cascading_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    with history.transaction() as db:
        conversation(db, "web-1")
        conversation(db, "dev-1", channel="device", device_id="rid-1")
        for bad in (("x1", "device", None), ("x2", "web", "rid-1"), ("x3", "email", None)):
            with pytest.raises(sqlite3.IntegrityError):
                conversation(db, *bad)
        db.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES('dev-1','user','hola','phone',1)")
        for role, source in (("system", "web"), ("user", "carrier-pigeon")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES('dev-1',?,'x',?,1)",
                           (role, source))
        with pytest.raises(sqlite3.IntegrityError):  # foreign keys are on
            db.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES('nadie','user','x','web',1)")
    with history.transaction() as db:
        db.execute("DELETE FROM conversations WHERE id='dev-1'")
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_migration_is_idempotent_and_a_newer_database_is_refused(tmp_path):
    path = tmp_path / "history.sqlite3"
    history.ensure_database(path)
    history._ready.clear()
    history.ensure_database(path)
    assert version(path) == 1
    future = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(future)) as db, db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(devices.SchemaError):
        history.ensure_database(future)
    with pytest.raises(history.HistoryUnavailable):
        with history.transaction(future):
            pass


def test_an_existing_older_history_database_is_backed_up_before_migrating(tmp_path):
    path = tmp_path / "history.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE notes (x)")  # some content at version 0
    history.ensure_database(path)
    [copy] = list((tmp_path / "backups").glob("*.sqlite3"))
    assert copy.name.startswith("history-v0-") and version(copy) == 0 and version(path) == 1


def test_the_device_database_schema_does_not_change(tmp_path):
    assert devices.SCHEMA_VERSION == 2  # going back to usuarios-v1 never meets a newer device database


# --- restore, generalized ----------------------------------------------------------------------------------

@pytest.fixture
def stopped_backend(monkeypatch):
    monkeypatch.setattr(restore, "backend_running", lambda port=None: False)


def test_restore_history_and_refuse_the_wrong_kind(tmp_path, stopped_backend):
    live = tmp_path / "history.sqlite3"
    with history.transaction(live) as db:
        conversation(db, "old")
    snapshot = tmp_path / "copy.sqlite3"
    with closing(sqlite3.connect(live)) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    with history.transaction(live) as db:
        conversation(db, "new")
    device_db = v1_database(tmp_path / "devices.sqlite3")
    assert restore.main([str(device_db), "--history", "--db", str(live), "--yes"]) == 3   # devices file
    assert restore.main([str(snapshot), "--db", str(device_db), "--yes"]) == 3             # history file
    assert restore.main([str(snapshot), "--history", "--db", str(live), "--yes"]) == 0
    with closing(sqlite3.connect(live)) as db:
        assert [row[0] for row in db.execute("SELECT id FROM conversations")] == ["old"]
    assert any(p.name.startswith("history-v1-pre-restore-") for p in (tmp_path / "backups").glob("*.sqlite3"))


def test_history_list(tmp_path, capsys):
    path = tmp_path / "history.sqlite3"
    history.ensure_database(path)
    assert restore.main(["--history", "--list", "--db", str(path)]) == 0
    assert "No hay respaldos" in capsys.readouterr().out


# --- backup sets ---------------------------------------------------------------------------------------------

@pytest.fixture
def databases(tmp_path, monkeypatch):
    (tmp_path / "live").mkdir()
    device_db = v1_database(tmp_path / "live" / "devices.sqlite3")
    devices.ensure_database(device_db)
    history_db = tmp_path / "live" / "history.sqlite3"
    with history.transaction(history_db) as db:
        conversation(db, "c1")
        db.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES('c1','user','hola','web',1)")
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(device_db))
    monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(history_db))
    root = tmp_path / "sets"
    monkeypatch.setenv("ITGLASSES_BACKUP_DIR", str(root))
    return device_db, history_db, root


def test_a_backup_set_holds_both_databases_verified(databases):
    _, _, root = databases
    folder = backups.backup_all("manual")
    assert folder.parent == root and "-manual-" in folder.name
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert [(item["name"], item["schema_version"]) for item in manifest["files"]] == [("devices.sqlite3", 2),
                                                                                        ("history.sqlite3", 1)]
    with closing(sqlite3.connect(folder / "history.sqlite3")) as db:
        assert db.execute("SELECT content FROM messages").fetchone() == ("hola",)
    assert "hola" not in (folder / "manifest.json").read_text(encoding="utf-8")


def test_a_set_skips_missing_databases_and_never_stays_half_made(databases, monkeypatch, tmp_path):
    _, history_db, root = databases
    monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(tmp_path / "missing.sqlite3"))
    folder = backups.backup_all("manual")
    assert [item["name"] for item in json.loads((folder / "manifest.json").read_text())["files"]] == ["devices.sqlite3"]
    monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(history_db))

    def broken(source, target):
        raise devices.BackupError("verification failed")

    monkeypatch.setattr(backups, "_copy", broken)
    with pytest.raises(devices.BackupError):
        backups.backup_all("manual")
    assert sorted(p.name for p in root.iterdir()) == [folder.name]


def test_daily_backups_are_made_once_a_day_and_only_seven_are_kept(databases):
    _, _, root = databases
    start = 1_800_000_000
    assert backups.daily_backup(now=start) is not None
    assert backups.daily_backup(now=start + 3600) is None            # less than 24 h later
    for day in range(1, 10):
        assert backups.daily_backup(now=start + day * backups.DAILY_SECONDS) is not None
    daily = [p for p in root.iterdir() if "-daily-" in p.name]
    assert len(daily) == 7
    assert backups.daily_backup(now=start + 9 * backups.DAILY_SECONDS + 60, keep=3) is None
    backups.daily_backup(now=start + 11 * backups.DAILY_SECONDS, keep=3)
    assert len([p for p in root.iterdir() if "-daily-" in p.name]) == 3


def test_manual_sets_are_never_pruned_by_the_daily_ones(databases):
    _, _, root = databases
    manual = backups.backup_all("manual")
    for day in range(12):
        backups.daily_backup(now=1_800_000_000 + day * backups.DAILY_SECONDS, keep=2)
    assert manual.is_dir()


def test_cli_makes_and_lists_sets(databases, capsys):
    assert backups.main([]) == 0
    assert "Respaldo creado" in capsys.readouterr().out
    assert backups.main(["--list"]) == 0
    assert "devices.sqlite3, history.sqlite3" in capsys.readouterr().out


@pytest.mark.parametrize(("variable", "value"), [(backups.ENABLED_VARIABLE, "a veces"), (backups.KEEP_VARIABLE, "0"),
                                                 (backups.KEEP_VARIABLE, "61"), (backups.KEEP_VARIABLE, "siete")])
def test_invalid_backup_settings_stop_the_backend(monkeypatch, variable, value):
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    monkeypatch.setenv(variable, value)
    with pytest.raises(backups.BackupConfigError):
        with TestClient(create_app(MockLLM())):
            pass


def test_the_backend_starts_the_daily_backup_without_waiting_for_it(databases, monkeypatch):
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    _, _, root = databases
    monkeypatch.setenv(backups.ENABLED_VARIABLE, "on")
    with TestClient(create_app(MockLLM())) as client:
        assert client.get("/health").status_code == 200
        for _ in range(200):
            if root.is_dir() and any("-daily-" in p.name for p in root.iterdir()):
                break
            time.sleep(0.02)
    assert [p for p in root.iterdir() if "-daily-" in p.name]


def test_a_failing_daily_backup_is_logged_and_never_stops_the_backend(databases, monkeypatch, caplog):
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    monkeypatch.setenv(backups.ENABLED_VARIABLE, "on")

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backups, "daily_backup", broken)
    with caplog.at_level(logging.ERROR, logger="backend.app.backups"):
        with TestClient(create_app(MockLLM())) as client:
            for _ in range(200):
                if "Daily backup failed" in caplog.text:
                    break
                time.sleep(0.02)
            assert client.get("/health").status_code == 200
    assert "Daily backup failed" in caplog.text


def test_tests_never_touch_the_real_history_or_backups():
    assert history.database_path() != history.DEFAULT_DB
    assert backups.backup_root() != backups.DEFAULT_DIR
