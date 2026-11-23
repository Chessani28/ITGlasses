"""Stage 6 (usuarios): what the web pages need from the backend, and that the page files fit together."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import access, passwords

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LOCAL = "http://127.0.0.1:8000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(tmp_path / "devices.sqlite3"))
    monkeypatch.setattr(store, "_ready", set())
    for name in (access.LOOPBACK_VARIABLE, passwords.MIN_LENGTH_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(MockLLM()), base_url=LOCAL, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.mark.parametrize(("mode", "minimum"), [(None, None), ("legacy", "8"), ("off", "12")])
def test_login_screen_settings(client, monkeypatch, mode, minimum):
    if mode:
        monkeypatch.setenv(access.LOOPBACK_VARIABLE, mode)
    if minimum:
        monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, minimum)
    response = client.get("/api/v1/auth/config")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"loopback_mode": mode or "chat", "password_min_length": int(minimum or 5)}


def test_login_screen_settings_only_from_this_pc(client):
    with TestClient(client.app, base_url=LOCAL, client=("172.16.5.20", 50000)) as outside:
        assert outside.get("/api/v1/auth/config").status_code == 403


def test_new_page_scripts_are_served_with_the_same_policy(client):
    for name in ("session.mjs", "accounts.mjs"):
        response = client.get("/ui/" + name)
        assert response.status_code == 200 and "javascript" in response.headers["content-type"]
        assert "unsafe-inline" not in response.headers["content-security-policy"]
    page = client.get("/").text
    for element in ("login-screen", "login-form", "user-box", "password-dialog", "secret-dialog", "confirm-dialog",
                    "users-page", "owners-page", "my-devices-page", "audit-page"):
        assert f'id="{element}"' in page, element


def test_every_element_the_scripts_use_exists_in_the_page():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', page))
    used = set(re.findall(r'\$\("([a-z0-9-]+)"\)', (FRONTEND / "accounts.mjs").read_text(encoding="utf-8")))
    assert used and used <= ids, sorted(used - ids)
    titles = re.search(r"PAGE_TITLES = \{(.*?)\};", (FRONTEND / "app.js").read_text(encoding="utf-8"), re.S).group(1)
    for page_name in re.findall(r'"?([a-z-]+)"?:\s*"', titles):
        assert f"{page_name}-page" in ids, page_name
        assert f'data-page="{page_name}"' in page, page_name


def test_no_script_writes_html():
    for name in ("accounts.mjs", "session.mjs"):
        source = (FRONTEND / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source and "outerHTML" not in source
