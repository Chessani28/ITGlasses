"""Shared test setup.

Starlette's TestClient reports its peer as "testclient", which is not an IP.
A real browser on this PC reaches the backend as 127.0.0.1, and only direct
loopback requests keep credential-free local mode, so tests default to that.
An explicit client=... always wins. Patched at configure time so that clients
created in fixtures of any scope, or at import, get the same default.
"""
import inspect
import os

import pytest

# Tests fake the per-request speech worker; the persistent Piper (on by default) has its own tests with fake
# processes (test_piper_persistent.py) and must never start a real Piper here.
os.environ.setdefault("ITGLASSES_PIPER_PERSISTENT", "0")
# Since stage "historial" 6 the backend defaults to ITGLASSES_LOOPBACK_MODE=off (nothing without a login; "chat"
# from stage "usuarios" 6). The older tests exercise those features credential-free from this PC, so they run in legacy
# mode; the access tests (test_users_*.py) clear this and set every mode they check explicitly.
os.environ.setdefault("ITGLASSES_LOOPBACK_MODE", "legacy")
# Stage "historial" 1: no test starts the daily backup of the real databases; the backup tests turn it on with
# temporary folders.
os.environ.setdefault("ITGLASSES_DAILY_BACKUP", "off")

from starlette.testclient import TestClient

LOCAL_CLIENT = ("127.0.0.1", 50000)
_original_init = TestClient.__init__
_signature = inspect.signature(_original_init)


def _local_by_default(self, *args, **kwargs):
    if "client" not in _signature.bind_partial(self, *args, **kwargs).arguments:
        kwargs["client"] = LOCAL_CLIENT
    _original_init(self, *args, **kwargs)


def pytest_configure(config):
    TestClient.__init__ = _local_by_default


def pytest_unconfigure(config):
    TestClient.__init__ = _original_init


@pytest.fixture(autouse=True)
def _isolated_device_database(tmp_path_factory, monkeypatch):
    """Never write the real storage/pairing/devices.sqlite3 from a test: since stage "usuarios" 5 every manual
    change writes an audit entry there. A test that sets ITGLASSES_PAIRING_DB itself still wins."""
    if not os.environ.get("ITGLASSES_PAIRING_DB"):
        monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(tmp_path_factory.mktemp("pairing") / "devices.sqlite3"))
    # Same for the chat history and the backup sets (stage "historial").
    if not os.environ.get("ITGLASSES_HISTORY_DB"):
        monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(tmp_path_factory.mktemp("history") / "history.sqlite3"))
    if not os.environ.get("ITGLASSES_BACKUP_DIR"):
        monkeypatch.setenv("ITGLASSES_BACKUP_DIR", str(tmp_path_factory.mktemp("backups")))
