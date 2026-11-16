"""Shared test setup.

Starlette's TestClient reports its peer as "testclient", which is not an IP.
A real browser on this PC reaches the backend as 127.0.0.1, and only direct
loopback requests keep credential-free local mode, so tests default to that.
An explicit client=... always wins. Patched at configure time so that clients
created in fixtures of any scope, or at import, get the same default.
"""
import inspect

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
