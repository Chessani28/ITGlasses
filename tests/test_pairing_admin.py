"""Administration gate (require_admin) and device states. See pairing/admin.py."""
import pytest
from fastapi.testclient import TestClient

from test_device_pairing import ADMIN_HEADERS, LOCAL, ROOT, admin_client, setup

ME = "/devices/me"


def register(phone):
    return phone.post("/pairing/requests", variant="phone").json()["request_id"]


def activate(c, phone):
    rid = register(phone)
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve").status_code == 200
    return rid, phone.post("/pairing/claim", rid=rid).json()["access_token"]


def me(c, phone, token):
    return c.get(ROOT + ME, headers=phone.headers("GET", ME, token))


def listed(c):
    return {row["id"]: row for row in admin_client(c).get(ROOT + "/admin/devices").json()["devices"]}


def act(c, rid, action):
    return admin_client(c).post(f"{ROOT}/admin/devices/{rid}/{action}")


# --- require_admin -----------------------------------------------------------

def test_loopback_browser_with_admin_header_is_allowed(setup):
    c, _, _ = setup
    assert admin_client(c).get(ROOT + "/admin/devices").json() == {"devices": []}
    ipv6 = admin_client(c, address="::1", base_url="http://[::1]:8000")
    assert ipv6.get(ROOT + "/admin/devices").status_code == 200
    named = admin_client(c, base_url="http://localhost:8000")
    assert named.get(ROOT + "/admin/devices", headers={"Origin": "http://localhost:8000"}).status_code == 200


def test_admin_needs_neither_https_nor_pairing_origin(setup, monkeypatch):
    c, _, _ = setup
    monkeypatch.delenv("ITGLASSES_PAIRING_ORIGIN")
    assert admin_client(c).get(ROOT + "/admin/devices").status_code == 200


@pytest.mark.parametrize("address", ["172.16.5.20", "192.168.1.10", "127.0.0.2", "testclient", "::ffff:172.16.5.20"])
def test_non_local_client_is_refused(setup, address):
    c, _, _ = setup
    assert admin_client(c, address=address).get(ROOT + "/admin/devices").status_code == 403


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Via", "Forwarded", "X-Forwarded-Host",
                                    "X-Forwarded-Proto", "X-Real-IP"])
def test_proxied_request_is_refused_even_from_loopback(setup, header):
    c, _, _ = setup
    assert admin_client(c).get(ROOT + "/admin/devices", headers={header: "127.0.0.1"}).status_code == 403


def test_missing_or_wrong_admin_header_is_refused(setup):
    c, a, _ = setup
    rid = register(a)
    plain = admin_client(c, headers={})
    assert plain.get(ROOT + "/admin/devices").status_code == 403
    assert plain.post(f"{ROOT}/admin/devices/{rid}/approve").status_code == 403
    assert plain.post(f"{ROOT}/admin/devices/{rid}/approve", headers={"X-ITGlasses-Admin": "true"}).status_code == 403
    assert listed(c)[rid]["state"] == "pending"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9000", "null",
                                    "https://pilot.example", "http://172.16.5.143:8000"])
def test_foreign_origin_is_refused(setup, origin):
    c, a, _ = setup
    rid = register(a)
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve", headers={"Origin": origin}).status_code == 403
    assert listed(c)[rid]["state"] == "pending"


def test_local_origin_is_allowed(setup):
    c, a, _ = setup
    rid = register(a)
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve", headers={"Origin": LOCAL}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "172.16.5.143:8443", "127.0.0.1:9000"])
def test_foreign_host_is_refused(setup, host):
    """DNS rebinding and requests relayed with the public host name."""
    c, _, _ = setup
    assert admin_client(c).get(ROOT + "/admin/devices", headers={"Host": host}).status_code == 403


def test_cross_site_fetch_metadata_is_refused(setup):
    c, _, _ = setup
    local = admin_client(c)
    assert local.get(ROOT + "/admin/devices", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert local.get(ROOT + "/admin/devices", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert local.get(ROOT + "/admin/devices", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_admin_routes_grant_no_cors_preflight(setup):
    c, _, _ = setup
    preflight = admin_client(c, headers={}).options(ROOT + "/admin/devices", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-itglasses-admin"})
    assert preflight.status_code >= 400
    assert not [name for name in preflight.headers if name.lower().startswith("access-control-")]


def test_phone_origin_cannot_administer(setup):
    c, a, _ = setup
    rid = register(a)
    assert c.post(f"{ROOT}/admin/devices/{rid}/approve", headers=ADMIN_HEADERS).status_code == 403


# --- states ------------------------------------------------------------------

def test_approve_block_unblock_delete_lifecycle(setup):
    c, a, _ = setup
    rid = register(a)
    row = listed(c)[rid]
    assert row["state"] == "pending" and row["code"] and row["approved_at"] is None
    assert act(c, rid, "approve").json() == {"id": rid, "state": "approved"}
    assert listed(c)[rid]["approved_at"] is not None
    token = a.post("/pairing/claim", rid=rid).json()["access_token"]
    assert listed(c)[rid]["state"] == "active"
    assert act(c, rid, "block").json()["state"] == "blocked"
    assert listed(c)[rid]["state"] == "blocked"
    assert a.post("/pairing/status", rid=rid).json() == {"state": "blocked"}
    assert act(c, rid, "unblock").json()["state"] == "active"
    assert me(c, a, token).json() == {"device_id": rid}
    assert admin_client(c).delete(f"{ROOT}/admin/devices/{rid}").json() == {"id": rid, "deleted": True}
    assert rid not in listed(c)


def test_deny_rejects_a_pending_request(setup):
    c, a, _ = setup
    rid = register(a)
    assert act(c, rid, "deny").json()["state"] == "denied"
    assert a.post("/pairing/status", rid=rid).json() == {"state": "denied"}
    assert a.post("/pairing/claim", rid=rid).status_code == 409
    assert rid not in listed(c)


def test_transitions_require_the_right_state(setup):
    c, a, _ = setup
    rid, _ = activate(c, a)
    for action in ("approve", "deny", "unblock"):
        assert act(c, rid, action).status_code == 409
    assert act(c, rid, "block").status_code == 200
    assert act(c, rid, "block").status_code == 409
    for action in ("approve", "block", "unblock", "deny"):
        assert act(c, "unknown-device", action).status_code == 404
    assert admin_client(c).delete(f"{ROOT}/admin/devices/unknown-device").status_code == 404
    assert act(c, "x" * 65, "approve").status_code == 422


def test_blocked_device_gets_403_everywhere_until_unblocked(setup):
    c, a, _ = setup
    rid, token = activate(c, a)
    act(c, rid, "block")
    assert me(c, a, token).status_code == 403
    path = "/assistant/chat"
    assert c.post(ROOT + path, headers=a.headers("POST", path, token), json={"message": "hola"}).status_code == 403
    assert a.post("/devices/challenge", rid=rid).status_code == 403
    assert a.post("/pairing/requests", variant="phone").status_code == 403
    act(c, rid, "unblock")
    nonce = a.post("/devices/challenge", rid=rid).json()["nonce"]
    renewed = a.post("/devices/token", rid=rid, nonce=nonce).json()["access_token"]
    assert me(c, a, renewed).status_code == 200


def test_deleted_device_gets_410_and_can_pair_again(setup):
    c, a, _ = setup
    rid, token = activate(c, a)
    assert admin_client(c).delete(f"{ROOT}/admin/devices/{rid}").status_code == 200
    assert me(c, a, token).status_code == 410
    assert a.post("/pairing/status", rid=rid).status_code == 410
    assert a.post("/devices/challenge", rid=rid).status_code == 410
    again = register(a)
    assert again != rid and listed(c)[again]["state"] == "pending"


def test_blocking_or_deleting_one_device_does_not_affect_another(setup):
    c, a, b = setup
    rid_a, token_a = activate(c, a)
    rid_b, token_b = activate(c, b)
    act(c, rid_a, "block")
    assert me(c, a, token_a).status_code == 403
    assert me(c, b, token_b).json() == {"device_id": rid_b}
    assert admin_client(c).delete(f"{ROOT}/admin/devices/{rid_a}").status_code == 200
    assert me(c, a, token_a).status_code == 410
    assert me(c, b, token_b).json() == {"device_id": rid_b}
    assert {rid: row["state"] for rid, row in listed(c).items()} == {rid_b: "active"}
