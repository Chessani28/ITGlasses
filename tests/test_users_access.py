"""Stage 4 (usuarios): permissions by role, ITGLASSES_LOOPBACK_MODE, ITGLASSES_BEARER_SCOPE and the phone paths."""
import os
import re
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import access, accounts, passwords, sessions
from test_device_pairing import ROOT, admin_client, setup  # noqa: F401 - setup is a fixture

LOCAL = "http://127.0.0.1:8000"
PAGE = {"Origin": LOCAL, "Sec-Fetch-Site": "same-origin"}
LEGACY_PANEL = {"X-ITGlasses-Admin": "1"}
PW = "clave-de-prueba"
ENVIRONMENT = (access.LOOPBACK_VARIABLE, access.BEARER_VARIABLE, "ITGLASSES_REQUIRE_AUTH",
               "ITGLASSES_ALLOW_LAN_BINDING", "ITGLASSES_API_TOKENS", "ITGLASSES_PAIRING_ORIGIN",
               passwords.MIN_LENGTH_VARIABLE, sessions.IDLE_VARIABLE, sessions.MAX_VARIABLE)

CALLS = {
    "chat": ("POST", "/chat", {"message": "hola"}),
    "voice_status": ("GET", "/voice/status", None),
    "knowledge_status": ("GET", "/knowledge/status", None),
    "documents_list": ("GET", "/documents", None),
    "documents_authorize": ("PATCH", "/documents/doc-1/authorization", {"authorized": True}),
    "documents_reindex": ("POST", "/documents/doc-1/reindex", None),
    "settings_read": ("GET", "/voice/diagnostic/settings", None),
    "settings_write": ("PUT", "/voice/diagnostic/settings", {}),
    "devices_panel": ("GET", "/admin/devices", None),
}
EVERYTHING = {name: 200 for name in CALLS}
BY_ROLE = {
    "root": EVERYTHING,
    "adm": {**EVERYTHING, "settings_read": 403, "settings_write": 403},
    "ana": {**{name: 403 for name in CALLS}, "chat": 200, "voice_status": 200, "knowledge_status": 200},
}
WITHOUT_LOGIN = {
    "legacy": EVERYTHING,
    "chat": {**{name: 401 for name in CALLS}, "chat": 200, "voice_status": 200, "knowledge_status": 200,
             "devices_panel": 403},
    "off": {**{name: 401 for name in CALLS}, "devices_panel": 403},
}
BEARER = {
    "mobile": {**{name: 403 for name in CALLS}, "chat": 200, "voice_status": 200},
    "full": {**EVERYTHING, "devices_panel": 403},
}


class FakeStore:
    def documents(self, eligible_only=False):
        return [{"id": "doc-1"}]


class FakeEmbeddings:
    model = "fake"

    async def fingerprint(self):
        return "x"


class FakeKnowledge:
    def __init__(self):
        self.store, self.embeddings, self.changes = FakeStore(), FakeEmbeddings(), []

    async def access(self, document_id, **change):
        self.changes.append((document_id, change))
        return {"id": document_id, **change}

    async def reindex(self, document_id):
        self.changes.append((document_id, "reindex"))
        return {"id": document_id}


class FakeVoice:
    def status(self):
        return {"stt_engine": "vosk"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setattr(store, "_ready", set())
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    with accounts.connect(path) as db:
        for username, role in (("root", "superadmin"), ("adm", "admin"), ("ana", "user")):
            accounts.create_user(db, username, PW, role)
    knowledge = FakeKnowledge()
    app = create_app(MockLLM(), knowledge=knowledge, voice=FakeVoice())
    with TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        yield client, knowledge, path


def sign_in(client, username, password=PW):
    response = client.post(ROOT + "/auth/login", json={"username": username, "password": password}, headers=PAGE)
    client.cookies.clear()
    assert response.status_code == 200, response.text
    token = re.search(r"itg_session=([^;]*)", response.headers["set-cookie"]).group(1)
    return {"Cookie": f"itg_session={token}", **PAGE, "X-CSRF-Token": response.json()["csrf_token"]}


def call(client, name, headers=None):
    method, path, body = CALLS[name]
    return client.request(method, ROOT + path, json=body, headers=headers or {})


def results(client, headers=None):
    return {name: call(client, name, headers).status_code for name in CALLS}


# --- defaults and settings --------------------------------------------------------------------------------

def test_defaults_need_a_login_for_administration_and_limit_shared_tokens(monkeypatch):
    for name in (access.LOOPBACK_VARIABLE, access.BEARER_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    assert (access.loopback_mode(), access.bearer_scope()) == ("off", "mobile")   # "off" since stage "historial" 6


@pytest.mark.parametrize(("variable", "value"), [(access.LOOPBACK_VARIABLE, "abierto"),
                                                 (access.BEARER_VARIABLE, "todo")])
def test_invalid_access_settings_stop_the_backend(world, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(access.AccessConfigError) as error:
        with TestClient(create_app(MockLLM())):
            pass
    assert variable in str(error.value)


# Routes with their own gate, not require_auth: public, web login, device panel and phone pairing (DPoP).
OWN_GATE = ("/health", "/api/v1/status", "/api/v1/auth/", "/api/v1/admin/", "/api/v1/pairing/", "/api/v1/devices/",
            "/api/v1/me/", "/api/v1/conversations")


def registered_routes(app):
    """(method, path template) of every API route, from the OpenAPI schema. FastAPI 0.149 keeps included routers
    behind internal wrappers (app.routes holds no APIRoute), so the public schema is the stable inventory."""
    return {(method.upper(), path) for path, operations in app.openapi()["paths"].items() for method in operations}


def test_every_protected_route_has_a_permission_and_require_auth_runs_on_it(world, monkeypatch):
    client, _, _ = world
    routes = registered_routes(client.app)
    protected = {(method, path) for method, path in routes if not path.startswith(OWN_GATE)}
    # Exactly the table: no protected route without a permission, no stale entry in the table.
    assert protected == set(access.ROUTES)
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "token-v1")
    with TestClient(client.app, base_url=LOCAL, client=("192.168.50.20", 50000)) as outside:
        for method, path in sorted(protected):
            url = re.sub(r"{[^}]+}", "x", path)
            # require_auth runs before the body is validated: no credentials from outside is always 401 ...
            assert outside.request(method, url).status_code == 401, (method, path)
            # ... and a shared token reaches only its scope (403 elsewhere, decided by require_auth + access).
            if (method, path) not in access.BEARER_MOBILE:
                denied = outside.request(method, url, headers={"Authorization": "Bearer token-v1"})
                assert denied.status_code == 403, (method, path)


def test_the_route_inventory_sees_the_gated_families(world):
    client, _, _ = world
    routes = registered_routes(client.app)
    for expected in (("POST", "/api/v1/auth/login"), ("GET", "/api/v1/admin/devices"),
                     ("POST", "/api/v1/pairing/requests"), ("GET", "/api/v1/devices/me"), ("POST", "/api/v1/chat"),
                     ("PATCH", "/api/v1/documents/{document_id}/authorization")):
        assert expected in routes


# --- web sessions: the role decides, whatever the loopback mode -------------------------------------------

@pytest.mark.parametrize("mode", ["legacy", "chat", "off"])
@pytest.mark.parametrize("username", ["root", "adm", "ana"])
def test_permissions_by_role(world, monkeypatch, mode, username):
    client, knowledge, _ = world
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, mode)
    assert results(client, sign_in(client, username)) == BY_ROLE[username]
    if username == "ana":
        assert knowledge.changes == []  # S1: a user never changes a manual


def test_a_session_change_needs_the_csrf_token_and_the_page_origin(world, monkeypatch):
    client, _, _ = world
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "chat")
    headers = sign_in(client, "adm")
    no_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    for name in ("chat", "documents_authorize"):
        refused = call(client, name, no_csrf)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "csrf_failed"
    foreign = {**headers, "Origin": "http://evil.test"}
    assert call(client, "documents_authorize", foreign).status_code == 403
    assert call(client, "documents_list", no_csrf).status_code == 200  # reading needs no CSRF token


def test_a_pending_password_change_blocks_everything_else(world, monkeypatch):
    client, _, path = world
    with accounts.connect(path) as db:
        accounts.set_password(db, accounts.find(db, "adm")["id"], PW, must_change=True)
    headers = sign_in(client, "adm")
    response = call(client, "documents_list", headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "password_change_required"
    assert client.get(ROOT + "/auth/me", headers=headers).status_code == 200


def test_an_expired_cookie_counts_as_no_login(world, monkeypatch):
    client, _, path = world
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "chat")
    headers = sign_in(client, "root")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE web_sessions SET idle_expires=1")
    assert call(client, "chat", headers).status_code == 200            # loopback chat mode
    assert call(client, "documents_list", headers).status_code == 401  # needs a valid login


# --- this PC without a login ------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["legacy", "chat", "off"])
def test_this_pc_without_login(world, monkeypatch, mode):
    client, knowledge, _ = world
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, mode)
    assert results(client, LEGACY_PANEL) == WITHOUT_LOGIN[mode]
    if mode != "legacy":
        assert knowledge.changes == []
        assert call(client, "documents_list").json()["error"]["code"] == "login_required"


# --- shared Bearer tokens (app v1) ------------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["mobile", "full"])
def test_shared_token_through_caddy(world, monkeypatch, scope):
    client, _, _ = world
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "token-v1")
    monkeypatch.setenv(access.BEARER_VARIABLE, scope)
    with TestClient(client.app, base_url=LOCAL, client=("192.168.50.20", 50000)) as phone:
        assert results(phone, {"Authorization": "Bearer token-v1"}) == BEARER[scope]
        assert call(phone, "chat", {"Authorization": "Bearer otro"}).status_code == 401
        assert call(phone, "chat").status_code == 401


def test_a_shared_token_on_this_pc_follows_the_loopback_mode(world, monkeypatch):
    client, _, _ = world
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "token-v1")
    bearer = {"Authorization": "Bearer token-v1"}
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "legacy")
    assert call(client, "documents_list", bearer).status_code == 200   # as before stage 4
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "chat")
    assert call(client, "documents_list", bearer).status_code == 403   # the token's own scope
    assert call(client, "chat", bearer).status_code == 200


# --- phones with DPoP (v3) --------------------------------------------------------------------------------

def test_dpop_phone_is_unchanged_in_the_strictest_modes(setup, monkeypatch):
    c, phone, _ = setup
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "off")
    monkeypatch.setenv(access.BEARER_VARIABLE, "mobile")
    with accounts.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        accounts.create_user(db, "adm", PW, "admin")
    rid = phone.post("/pairing/requests", variant="phone").json()["request_id"]
    with admin_client(c, headers={}) as panel:
        assert panel.post(f"{ROOT}/admin/devices/{rid}/approve", headers=LEGACY_PANEL).status_code == 403
        session = sign_in(panel, "adm")
        no_csrf = {key: value for key, value in session.items() if key != "X-CSRF-Token"}
        assert panel.post(f"{ROOT}/admin/devices/{rid}/approve", headers=no_csrf).status_code == 403
        assert panel.post(f"{ROOT}/admin/devices/{rid}/approve", headers=session).status_code == 200
    token = phone.post("/pairing/claim", rid=rid).json()["access_token"]
    for path, body in (("/chat", {"message": "hola"}), ("/assistant/chat", {"message": "hola"})):
        assert c.post(ROOT + path, headers=phone.headers("POST", path, token), json=body).status_code == 200
    assert c.get(ROOT + "/devices/me", headers=phone.headers("GET", "/devices/me", token)).status_code == 200
    assert c.get(ROOT + "/documents", headers=phone.headers("GET", "/documents", token)).status_code == 403


def test_a_user_session_cannot_use_the_device_panel(setup, monkeypatch):
    c, phone, _ = setup
    with accounts.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        accounts.create_user(db, "ana", PW, "user")
    rid = phone.post("/pairing/requests", variant="phone").json()["request_id"]
    with admin_client(c, headers={}) as panel:
        session = sign_in(panel, "ana")
        assert panel.get(f"{ROOT}/admin/devices", headers=session).status_code == 403
        assert panel.post(f"{ROOT}/admin/devices/{rid}/approve", headers=session).status_code == 403
        # With legacy mode the old panel (no cookie) keeps working, as before stage 4.
        assert panel.post(f"{ROOT}/admin/devices/{rid}/approve", headers=LEGACY_PANEL).status_code == 200
