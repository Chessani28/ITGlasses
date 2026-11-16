"""Stage 3b: traffic relayed by a proxy (Caddy) never gets credential-free local mode."""
import pytest
from fastapi.testclient import TestClient

from backend.app.core import local_request
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from test_device_pairing import ORIGIN, ROOT, admin_client, setup

CHAT = ("POST", "/api/v1/chat", {"message": "hola"})
FORWARDING = ["X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "Forwarded", "Via"]


@pytest.fixture
def app(monkeypatch):
    # Loopback bind and no pairing origin: the configuration that left the API open behind Caddy.
    for name in ("ITGLASSES_REQUIRE_AUTH", "ITGLASSES_ALLOW_LAN_BINDING", "ITGLASSES_API_TOKENS",
                 "ITGLASSES_PAIRING_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    return create_app(MockLLM())


def call(app, address="127.0.0.1", headers=None, route=CHAT):
    method, path, body = route
    with TestClient(app, client=(address, 50000)) as client:
        return client.request(method, path, json=body, headers=headers or {})


@pytest.mark.parametrize("header", FORWARDING)
def test_forwarded_request_without_token_is_refused(app, header):
    value = "for=172.16.5.20" if header == "Forwarded" else "172.16.5.20"
    assert call(app, headers={header: value}).status_code == 401


@pytest.mark.parametrize("address", ["172.16.5.20", "::ffff:172.16.5.20"])
def test_non_loopback_client_without_token_is_refused(app, address):
    assert call(app, address=address).status_code == 401


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
def test_direct_loopback_keeps_local_mode(app, address):
    assert call(app, address=address).status_code == 200


@pytest.mark.parametrize("header,value", [
    ("X-Forwarded-For", "127.0.0.1"), ("Forwarded", "for=127.0.0.1"), ("X-Real-IP", "127.0.0.1"),
])
def test_spoofed_loopback_forwarding_is_refused(app, header, value):
    assert call(app, headers={header: value}).status_code == 401


def test_shared_token_still_works_through_proxy(app, monkeypatch):
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "transition-test-only")
    relayed = {"X-Forwarded-For": "172.16.5.20"}
    assert call(app, headers={**relayed, "Authorization": "Bearer transition-test-only"}).status_code == 200
    assert call(app, headers={**relayed, "Authorization": "Bearer wrong"}).status_code == 401


def test_approved_phone_authenticates_through_proxy(setup):
    c, phone, _ = setup
    rid = phone.post("/pairing/requests", variant="phone").json()["request_id"]
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve").status_code == 200
    token = phone.post("/pairing/claim", rid=rid).json()["access_token"]
    path = "/assistant/chat"
    headers = {**phone.headers("POST", path, token), "X-Forwarded-For": "172.16.5.20"}
    assert c.post(ROOT + path, headers=headers, json={"message": "hola"}).status_code == 200


@pytest.mark.parametrize("route", [
    ("POST", "/api/v1/assistant/chat", {"message": "hola"}),
    ("GET", "/api/v1/documents", None),
    ("GET", "/api/v1/knowledge/status", None),
    ("GET", "/api/v1/voice/status", None),
])
def test_every_protected_route_family_fails_closed_through_proxy(app, route):
    assert call(app, headers={"X-Forwarded-For": "172.16.5.20"}, route=route).status_code == 401


def test_public_routes_stay_public_through_proxy(app):
    relayed = {"X-Forwarded-For": "172.16.5.20"}
    assert call(app, headers=relayed, route=("GET", "/health", None)).status_code == 200
    assert call(app, headers=relayed, route=("GET", "/api/v1/status", None)).status_code == 200


def test_auth_and_admin_share_one_local_check(setup, monkeypatch):
    c, _, _ = setup
    for name in ("ITGLASSES_PAIRING_ORIGIN", "ITGLASSES_REQUIRE_AUTH", "ITGLASSES_ALLOW_LAN_BINDING"):
        monkeypatch.delenv(name, raising=False)
    assert c.post("/api/v1/chat", json={"message": "hola"}).status_code == 200
    assert admin_client(c).get(ROOT + "/admin/devices").status_code == 200
    monkeypatch.setattr(local_request, "is_direct_local", lambda request: False)
    assert c.post("/api/v1/chat", json={"message": "hola"}).status_code == 401
    assert admin_client(c).get(ROOT + "/admin/devices").status_code == 403
