"""Stage 3: device labels, 6-digit visual code, last_seen/last_ip and open-request limits."""
import os
import re
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app.pairing import routes
from test_device_pairing import ORIGIN, ROOT, Phone, admin_client, setup

ME = "/devices/me"


def phone_at(c, address):
    return Phone(TestClient(c.app, base_url=ORIGIN, client=(address, 4321)))


def register(phone, **labels):
    return phone.post("/pairing/requests", variant="phone", **labels)


def listed(c):
    return {row["id"]: row for row in admin_client(c).get(ROOT + "/admin/devices").json()["devices"]}


def activate(c, phone):
    rid = register(phone).json()["request_id"]
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve").status_code == 200
    return rid, phone.post("/pairing/claim", rid=rid).json()["access_token"]


def me(c, phone, token):
    return phone.client.get(ROOT + ME, headers=phone.headers("GET", ME, token))


def database(sql, *args):
    with closing(sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"])) as db, db:
        return db.execute(sql, args).fetchone()


# --- six-digit code ------------------------------------------------------------

def test_code_is_six_digits_and_unique_among_open_requests(setup):
    _, a, b = setup
    first, second = register(a).json()["user_code"], register(b).json()["user_code"]
    assert re.fullmatch(r"\d{6}", first) and re.fullmatch(r"\d{6}", second)
    assert first != second


def test_code_collision_is_retried(setup, monkeypatch):
    _, a, b = setup
    draws = iter([111111, 111111, 222222])
    monkeypatch.setattr(routes.secrets, "randbelow", lambda _: next(draws))
    assert register(a).json()["user_code"] == "111111"
    assert register(b).json()["user_code"] == "222222"


def test_code_is_only_visual_confirmation(setup):
    c, a, b = setup
    request = register(a).json()
    admin_client(c).post(f"{ROOT}/admin/devices/{request['request_id']}/approve")
    # Knowing the code is useless without the Keystore key that registered.
    stolen = b.post("/pairing/claim", rid=request["request_id"], code=request["user_code"])
    assert stolen.status_code == 401
    assert a.post("/pairing/claim", rid=request["request_id"]).status_code == 200


# --- name and model --------------------------------------------------------------

@pytest.mark.parametrize("sent,expected", [
    ("Pixel de Ana", "Pixel de Ana"),
    ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),
    ("<script>alert(1)</script>", "<script>alert(1)</script>"),
    ("  Teléfono de Ñandú  ", "Teléfono de Ñandú"),
])
def test_labels_are_stored_verbatim(setup, sent, expected):
    c, a, _ = setup
    rid = register(a, name=sent, model="Google Pixel 8").json()["request_id"]
    row = listed(c)[rid]
    assert row["name"] == expected and row["model"] == "Google Pixel 8"


@pytest.mark.parametrize("field,value", [
    ("name", "x" * 65),
    ("name", "Pixel\x00"),
    ("model", "Pixel\nfalso"),
    ("name", "Pixel‮gnp.exe"),
    ("name", "Pixel de Ana"),
    ("name", 123),
    ("name", ""),
])
def test_invalid_labels_are_rejected(setup, field, value):
    c, a, _ = setup
    assert register(a, **{field: value}).status_code == 422
    assert listed(c) == {}


def test_labels_are_optional_for_older_apps(setup):
    c, a, _ = setup
    reply = register(a)
    assert reply.status_code == 201
    row = listed(c)[reply.json()["request_id"]]
    assert row["name"] is None and row["model"] is None


# --- last_ip and last_seen ---------------------------------------------------------

def test_last_ip_comes_from_the_connection(setup):
    c, _, _ = setup
    phone = phone_at(c, "192.168.50.20")
    before = int(time.time())
    rid = register(phone).json()["request_id"]
    row = listed(c)[rid]
    assert row["last_ip"] == "192.168.50.20"
    assert row["created_at"] >= before and row["last_seen"] >= before


def test_last_seen_updates_at_most_once_per_minute(setup):
    c, a, _ = setup
    rid, token = activate(c, a)
    recent = int(time.time()) - 30
    database("UPDATE installs SET last_seen=?,last_ip='10.0.0.9' WHERE id=?", recent, rid)
    assert me(c, a, token).status_code == 200
    assert database("SELECT last_seen,last_ip FROM installs WHERE id=?", rid) == (recent, "10.0.0.9")
    stale = int(time.time()) - 61
    database("UPDATE installs SET last_seen=? WHERE id=?", stale, rid)
    assert me(c, a, token).status_code == 200
    seen, ip = database("SELECT last_seen,last_ip FROM installs WHERE id=?", rid)
    assert seen > stale + 60 and ip == "127.0.0.1"


def test_ip_is_not_identity(setup):
    c, _, _ = setup
    office, home = phone_at(c, "192.168.50.20"), phone_at(c, "192.168.50.21")
    home.key = office.key  # Same phone, new address.
    rid, token = activate(c, office)
    assert me(c, home, token).json() == {"device_id": rid}
    intruder = phone_at(c, "192.168.50.20")  # Same address, other key.
    assert me(c, intruder, token).status_code == 401


# --- open-request limits ---------------------------------------------------------------

def test_pending_limit_per_ip(setup):
    c, _, _ = setup
    for _ in range(routes.PENDING_PER_IP):
        assert register(phone_at(c, "192.168.50.30")).status_code == 201
    refused = register(phone_at(c, "192.168.50.30"))
    assert refused.status_code == 429 and 1 <= int(refused.headers["retry-after"]) <= 300
    assert len(listed(c)) == routes.PENDING_PER_IP
    assert register(phone_at(c, "192.168.50.31")).status_code == 201


def test_pending_limit_global(setup, monkeypatch):
    c, _, _ = setup
    monkeypatch.setattr(routes, "PENDING_TOTAL", 2)
    assert register(phone_at(c, "192.168.50.40")).status_code == 201
    assert register(phone_at(c, "192.168.50.41")).status_code == 201
    refused = register(phone_at(c, "192.168.50.42"))
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1


def test_expired_requests_free_the_limit(setup):
    c, _, _ = setup
    rids = [register(phone_at(c, "192.168.50.50")).json()["request_id"] for _ in range(routes.PENDING_PER_IP)]
    assert register(phone_at(c, "192.168.50.50")).status_code == 429
    database("UPDATE installs SET expires=0 WHERE id=?", rids[0])
    assert register(phone_at(c, "192.168.50.50")).status_code == 201


def test_reregistering_does_not_consume_quota(setup):
    c, _, _ = setup
    phone = phone_at(c, "192.168.50.60")
    first = register(phone).json()["request_id"]
    for _ in range(4):
        assert register(phone).json()["request_id"] == first
    for _ in range(routes.PENDING_PER_IP - 1):
        assert register(phone_at(c, "192.168.50.60")).status_code == 201
