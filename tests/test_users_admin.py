"""Stage 5 (usuarios): web administration of users, device owners, "my devices" and the read-only audit log."""
import os
import re
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import access, accounts, audit, passwords, sessions
from test_device_pairing import ROOT, admin_client, setup  # noqa: F401 - setup is a fixture
from test_users_access import ENVIRONMENT, LEGACY_PANEL, LOCAL, PAGE, PW, FakeKnowledge, FakeVoice, sign_in

USERS = ROOT + "/admin/users"


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setattr(store, "_ready", set())
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    ids = {}
    with accounts.connect(path) as db:
        for username, role in (("root", "superadmin"), ("adm", "admin"), ("adm2", "admin"), ("ana", "user"),
                               ("beto", "user")):
            ids[username] = accounts.create_user(db, username, PW, role)
    knowledge = FakeKnowledge()
    app = create_app(MockLLM(), knowledge=knowledge, voice=FakeVoice())
    with TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        yield client, path, ids, knowledge


def add_device(path, device_id, state="active", owner=None, name="Pixel"):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO installs(id,key_id,variant,state,expires,token,name,user_id) VALUES(?,?,?,?,?,?,?,?)",
                   (device_id, "key-" + device_id, "phone", state, 9999999999, "digest-" + device_id, name, owner))


def entries(path):
    with accounts.connect(path) as db:
        return audit.entries(db, limit=500)


def status(response):
    return response.status_code, (response.json().get("error") or {}).get("code")


# --- users ------------------------------------------------------------------------------------------------

def test_an_admin_creates_a_user_with_a_one_time_password(world):
    client, path, ids, _ = world
    admin = sign_in(client, "adm")
    response = client.post(USERS, json={"username": "Carla", "display_name": "Carla R."}, headers=admin)
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    body = response.json()
    temporary = body["temporary_password"]
    assert len(temporary) >= passwords.min_length() and body["user"]["must_change_pw"] is True
    assert (body["user"]["username"], body["user"]["role"]) == ("carla", "user")
    assert temporary.encode() not in path.read_bytes()
    carla = sign_in(client, "carla", temporary)
    assert client.post(ROOT + "/chat", json={"message": "hola"}, headers=carla).status_code == 403  # change it first
    [entry] = [e for e in entries(path) if e["action"] == "user.create"]
    assert (entry["actor_user_id"], entry["detail"]) == (ids["adm"], {"username": "carla", "role": "user"})
    assert temporary not in str(entries(path))


def test_who_may_create_whom(world):
    client, _, _, _ = world
    admin, root, user = sign_in(client, "adm"), sign_in(client, "root"), sign_in(client, "ana")
    assert status(client.post(USERS, json={"username": "jefe", "role": "admin"}, headers=admin)) == (403, "forbidden_role")
    assert client.post(USERS, json={"username": "jefe", "role": "admin"}, headers=root).status_code == 201
    assert client.post(USERS, json={"username": "otro", "role": "superadmin"}, headers=root).status_code == 422
    assert status(client.post(USERS, json={"username": "x1"}, headers=user)) == (403, "forbidden_role")
    assert status(client.post(USERS, json={"username": "ANA"}, headers=admin)) == (409, "username_taken")
    assert status(client.post(USERS, json={"username": "a b"}, headers=admin)) == (422, "username_invalid")


def test_listing_marks_who_each_admin_may_manage(world):
    client, path, ids, _ = world
    add_device(path, "p1", owner=ids["ana"])
    listed = {u["username"]: u for u in client.get(USERS, headers=sign_in(client, "adm")).json()["users"]}
    assert [name for name, u in listed.items() if u["manageable"]] == ["ana", "beto"]
    assert listed["ana"]["devices"] == 1 and "pw_hash" not in listed["ana"] and "pw_salt" not in listed["ana"]
    listed = {u["username"]: u for u in client.get(USERS, headers=sign_in(client, "root")).json()["users"]}
    assert sorted(name for name, u in listed.items() if u["manageable"]) == ["adm", "adm2", "ana", "beto"]
    assert client.get(USERS, headers=sign_in(client, "ana")).status_code == 403


@pytest.mark.parametrize(("actor", "target", "allowed"), [
    ("adm", "ana", True), ("adm", "adm2", False), ("adm", "root", False), ("adm", "adm", False),
    ("root", "adm", True), ("root", "ana", True), ("root", "root", False),
])
def test_reset_disable_and_unlock_follow_the_hierarchy(world, actor, target, allowed):
    client, _, ids, _ = world
    headers = sign_in(client, actor)
    calls = (client.post(f"{USERS}/{ids[target]}/reset-password", headers=headers),
             client.post(f"{USERS}/{ids[target]}/unlock", headers=headers),
             client.patch(f"{USERS}/{ids[target]}", json={"status": "disabled"}, headers=headers))
    expected = 200 if allowed else 403
    assert [r.status_code for r in calls] == [expected] * 3


def test_reset_gives_a_one_time_password_closes_sessions_and_unlocks(world):
    client, path, ids, _ = world
    victim = sign_in(client, "ana")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE users SET failed_count=5, locked_until=9999999999 WHERE id=?", (ids["ana"],))
    response = client.post(f"{USERS}/{ids['ana']}/reset-password", headers=sign_in(client, "adm"))
    temporary = response.json()["temporary_password"]
    assert client.get(ROOT + "/auth/me", headers=victim).status_code == 401
    renewed = sign_in(client, "ana", temporary)  # unlocked
    assert client.get(ROOT + "/auth/me", headers=renewed).json()["user"]["must_change_pw"] is True
    assert ("user.password_reset", {"username": "ana", "temporary": True}) in [
        (e["action"], e["detail"]) for e in entries(path)]


def test_disable_enable_and_rename(world):
    client, path, ids, _ = world
    victim = sign_in(client, "ana")
    admin = sign_in(client, "adm")
    assert client.patch(f"{USERS}/{ids['ana']}", json={"status": "disabled"}, headers=admin).status_code == 200
    assert client.get(ROOT + "/auth/me", headers=victim).status_code == 401
    assert client.post(ROOT + "/auth/login", json={"username": "ana", "password": PW}, headers=PAGE).status_code == 401
    changed = client.patch(f"{USERS}/{ids['ana']}", json={"status": "active", "display_name": " Ana López "},
                           headers=admin).json()["user"]
    assert (changed["status"], changed["display_name"]) == ("active", "Ana López")
    assert client.patch(f"{USERS}/{ids['ana']}", json={}, headers=admin).status_code == 422
    user_actions = [e["action"] for e in entries(path) if e["action"].startswith("user.")]
    assert user_actions[:3] == ["user.enable", "user.update", "user.disable"]


def test_only_the_superadmin_changes_roles_and_it_closes_sessions(world):
    client, path, ids, _ = world
    promoted = sign_in(client, "ana")
    assert client.post(f"{USERS}/{ids['beto']}/role", json={"role": "admin"},
                       headers=sign_in(client, "adm")).status_code == 403
    root = sign_in(client, "root")
    assert client.post(f"{USERS}/{ids['ana']}/role", json={"role": "admin"}, headers=root).json()["user"]["role"] == "admin"
    assert client.get(ROOT + "/auth/me", headers=promoted).status_code == 401
    assert client.post(f"{USERS}/{ids['root']}/role", json={"role": "user"}, headers=root).status_code == 403
    assert client.post(f"{USERS}/{ids['ana']}/role", json={"role": "superadmin"}, headers=root).status_code == 422
    assert ("user.role", {"username": "ana", "from": "user", "to": "admin"}) in [
        (e["action"], e["detail"]) for e in entries(path)]


def test_admin_pages_need_a_login_and_the_csrf_token(world, monkeypatch):
    client, _, ids, _ = world
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "legacy")  # even the most open mode: no login, no user admin
    assert client.get(USERS, headers=LEGACY_PANEL).status_code == 401
    assert client.post(USERS, json={"username": "x1"}, headers={**LEGACY_PANEL, **PAGE}).status_code == 401
    admin = sign_in(client, "adm")
    no_csrf = {key: value for key, value in admin.items() if key != "X-CSRF-Token"}
    assert status(client.post(USERS, json={"username": "x1"}, headers=no_csrf)) == (403, "csrf_failed")
    with TestClient(client.app, base_url=LOCAL, client=("172.16.5.20", 50000)) as outside:
        assert outside.get(USERS, headers=admin).status_code == 403


# --- devices: owners --------------------------------------------------------------------------------------

def test_inventory_shows_owners_and_assignment_is_audited(world):
    client, path, ids, _ = world
    add_device(path, "p1")
    admin = sign_in(client, "adm")
    response = client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": ids["ana"]}, headers=admin)
    assert response.json() == {"id": "p1", "user_id": ids["ana"]}
    [row] = [d for d in client.get(ROOT + "/admin/devices", headers=admin).json()["devices"] if d["id"] == "p1"]
    assert (row["user_id"], row["owner_username"]) == (ids["ana"], "ana") and "token" not in row and "key_id" not in row
    assert client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": None}, headers=admin).json()["user_id"] is None
    details = [e["detail"] for e in entries(path) if e["action"] == "device.assign"]
    assert details == [{"name": "Pixel", "owner": None, "previous": "ana"},
                       {"name": "Pixel", "owner": "ana", "previous": None}]


def test_assignment_needs_a_session_and_an_active_user(world):
    client, path, ids, _ = world
    add_device(path, "p1")
    add_device(path, "p2", state="pending")
    assert client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": ids["ana"]},
                       headers=LEGACY_PANEL).status_code == 403  # the old panel without login
    admin = sign_in(client, "adm")
    with accounts.connect(path) as db:
        accounts.set_status(db, ids["beto"], "disabled")
    assert client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": ids["beto"]}, headers=admin).status_code == 422
    assert client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": "nadie"}, headers=admin).status_code == 422
    assert client.post(ROOT + "/admin/devices/p2/assign", json={"user_id": ids["ana"]}, headers=admin).status_code == 409
    assert client.post(ROOT + "/admin/devices/zz/assign", json={"user_id": ids["ana"]}, headers=admin).status_code == 404
    assert client.post(ROOT + "/admin/devices/p1/assign", json={"user_id": ids["ana"]},
                       headers=sign_in(client, "ana")).status_code == 403


def test_approve_with_an_owner_and_the_old_panel_keeps_approving_without_one(setup, monkeypatch):
    c, phone, other = setup
    db_path = os.environ["ITGLASSES_PAIRING_DB"]
    with accounts.connect(db_path) as db:
        admin_id = accounts.create_user(db, "adm", PW, "admin")
        ana = accounts.create_user(db, "ana", PW, "user")
    first = phone.post("/pairing/requests", variant="phone").json()["request_id"]
    second = other.post("/pairing/requests", variant="meta").json()["request_id"]
    with admin_client(c, headers={}) as panel:
        session = sign_in(panel, "adm")
        approved = panel.post(f"{ROOT}/admin/devices/{first}/approve", json={"user_id": ana}, headers=session)
        assert approved.json() == {"id": first, "state": "approved", "user_id": ana}
        assert panel.post(f"{ROOT}/admin/devices/{second}/approve", headers=LEGACY_PANEL).json() == {
            "id": second, "state": "approved"}
    token = phone.post("/pairing/claim", rid=first).json()["access_token"]
    assert c.post(ROOT + "/chat", headers=phone.headers("POST", "/chat", token), json={"message": "hola"}).status_code == 200
    with accounts.connect(db_path) as db:
        log = [(e["action"], e["actor_user_id"], e["detail"]) for e in audit.entries(db)]
    assert ("device.approve", admin_id, {"name": None, "from": "pending", "owner": "ana"}) in log
    assert ("device.approve", None, {"name": None, "from": "pending", "via": "local"}) in log


def test_every_device_action_is_audited(world):
    client, path, _, _ = world
    add_device(path, "p1")
    admin = sign_in(client, "adm")
    for action in ("block", "unblock"):
        assert client.post(f"{ROOT}/admin/devices/p1/{action}", headers=admin).status_code == 200
    assert client.delete(f"{ROOT}/admin/devices/p1", headers=admin).status_code == 200
    assert [e["action"] for e in entries(path)][:3] == ["device.delete", "device.unblock", "device.block"]


# --- my devices -------------------------------------------------------------------------------------------

def test_a_user_sees_only_their_own_devices_without_secrets(world):
    client, path, ids, _ = world
    add_device(path, "mine", owner=ids["ana"], name="Mi Pixel")
    add_device(path, "theirs", owner=ids["beto"])
    add_device(path, "gone", state="denied", owner=ids["ana"])
    devices = client.get(ROOT + "/me/devices", headers=sign_in(client, "ana")).json()["devices"]
    assert [d["id"] for d in devices] == ["mine"] and devices[0]["name"] == "Mi Pixel"
    assert set(devices[0]) == {"id", "variant", "name", "model", "state", "created_at", "approved_at", "last_seen"}
    assert client.get(ROOT + "/me/devices").status_code == 401


# --- audit log --------------------------------------------------------------------------------------------

def test_the_superadmin_sees_everything_and_an_admin_only_administration(world):
    client, path, ids, _ = world
    client.post(ROOT + "/auth/login", json={"username": "ana", "password": "mal"}, headers=PAGE)
    admin = sign_in(client, "adm")
    client.post(f"{USERS}/{ids['ana']}/unlock", headers=admin)
    full = client.get(ROOT + "/admin/audit", headers=sign_in(client, "root")).json()
    seen = client.get(ROOT + "/admin/audit", headers=admin).json()
    assert full["complete"] is True and seen["complete"] is False
    assert {"auth.login", "user.unlock"} <= {e["action"] for e in full["entries"]}
    assert {e["action"] for e in seen["entries"]} == {"user.unlock"}
    assert client.get(ROOT + "/admin/audit", headers=sign_in(client, "ana")).status_code == 403
    assert client.get(ROOT + "/admin/audit").status_code == 401


def test_audit_paging_and_it_is_read_only(world):
    client, path, ids, _ = world
    root = sign_in(client, "root")
    for _ in range(3):
        client.post(f"{USERS}/{ids['ana']}/unlock", headers=root)
    first = client.get(ROOT + "/admin/audit?limit=2", headers=root).json()
    rest = client.get(f"{ROOT}/admin/audit?limit=2&before_id={first['next_before_id']}", headers=root).json()
    ids_seen = [e["id"] for e in first["entries"] + rest["entries"]]
    assert len(ids_seen) == len(set(ids_seen)) == 4 and ids_seen == sorted(ids_seen, reverse=True)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, ROOT + "/admin/audit", headers=root).status_code == 405


def test_manual_changes_are_audited_with_their_author(world, monkeypatch):
    client, path, ids, knowledge = world
    admin = sign_in(client, "adm")
    assert client.patch(ROOT + "/documents/doc-1/authorization", json={"authorized": True}, headers=admin).status_code == 200
    monkeypatch.setenv(access.LOOPBACK_VARIABLE, "legacy")
    assert client.post(ROOT + "/documents/doc-1/reindex").status_code == 200  # the PC without login (legacy)
    log = [(e["action"], e["actor_user_id"], e["detail"]) for e in entries(path)][:2]
    assert log == [("document.reindex", None, {"via": "local"}),
                   ("document.authorize", ids["adm"], {"authorized": True})]


def test_tests_never_touch_the_real_device_database():
    assert os.environ["ITGLASSES_PAIRING_DB"] and store.database_path() != store.DEFAULT_DB
