"""Stage 7 (usuarios): end-to-end acceptance on one database, as on the real system.

A v1 device database with an approved phone is migrated (with backup); the first superadmin is created with the
local script and receives that phone; the web creates an admin and a user; the user changes the temporary
password; a new phone is approved and assigned; every role sees exactly its part; DPoP phones keep working in
the strictest modes; the audit log tells the whole story without a single secret.
"""
import re
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend.app.pairing import store
from backend.app.users import access, cli
from test_device_pairing import ROOT, Phone, admin_client, setup  # noqa: F401 - setup is a fixture
from test_pairing_schema_v2 import v1_database

LOCAL = "http://127.0.0.1:8000"
PAGE = {"Origin": LOCAL, "Sec-Fetch-Site": "same-origin"}
ROOT_PW, NEW_PW = "clave-del-superadmin", "clave-propia-de-ana"


def answers(*values):
    queue = list(values)
    return lambda _prompt="": queue.pop(0)


def sign_in(panel, username, password):
    response = panel.post(ROOT + "/auth/login", json={"username": username, "password": password}, headers=PAGE)
    panel.cookies.clear()
    assert response.status_code == 200, response.text
    token = re.search(r"itg_session=([^;]*)", response.headers["set-cookie"]).group(1)
    return {"Cookie": f"itg_session={token}", **PAGE, "X-CSRF-Token": response.json()["csrf_token"]}, response.json()


def test_users_and_roles_end_to_end(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    v1_database(path)  # one approved phone ("rid-1") from before the users stage
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setenv("ITGLASSES_PAIRING_ORIGIN", "https://pilot.example")
    monkeypatch.setattr(store, "_ready", set())
    for name in (access.LOOPBACK_VARIABLE, access.BEARER_VARIABLE, "ITGLASSES_API_TOKENS"):
        monkeypatch.delenv(name, raising=False)

    # 1. Local script: migration with backup, first superadmin, existing phone assigned to it.
    assert cli.main(["create-superadmin", "--username", "laura", "--name", "Laura"],
                    read=answers(ROOT_PW, ROOT_PW)) == 0
    assert [p.name.startswith("devices-v1-") for p in store.backup_dir(path).glob("*.sqlite3")] == [True]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        owner = db.execute("SELECT u.username FROM installs i JOIN users u ON u.id=i.user_id WHERE i.id='rid-1'")
        assert owner.fetchone() == ("laura",)

    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    with TestClient(create_app(MockLLM()), base_url="https://pilot.example") as phones:
        with admin_client(phones, headers={}) as web:
            root, _ = sign_in(web, "laura", ROOT_PW)

            # 2. The superadmin creates an admin; the admin creates a user.
            created = web.post(ROOT + "/admin/users", json={"username": "jefa", "role": "admin"}, headers=root).json()
            admin, me = sign_in(web, "jefa", created["temporary_password"])
            assert me["user"]["must_change_pw"] is True
            assert web.post(ROOT + "/auth/password", json={"current_password": created["temporary_password"],
                                                           "new_password": "clave-de-la-jefa"},
                            headers=admin).status_code == 200
            web.cookies.clear()  # every request below sends the session it means explicitly
            admin, _ = sign_in(web, "jefa", "clave-de-la-jefa")
            created = web.post(ROOT + "/admin/users", json={"username": "ana", "display_name": "Ana"},
                               headers=admin).json()
            ana_id, temporary = created["user"]["id"], created["temporary_password"]

            # 3. The user must change the temporary password before anything else.
            user, _ = sign_in(web, "ana", temporary)
            assert web.post(ROOT + "/chat", json={"message": "hola"}, headers=user).status_code == 403
            changed = web.post(ROOT + "/auth/password", json={"current_password": temporary, "new_password": NEW_PW},
                               headers=user)
            web.cookies.clear()
            user = {**user, "Cookie": "itg_session=" + re.search(r"itg_session=([^;]*)",
                                                                 changed.headers["set-cookie"]).group(1),
                    "X-CSRF-Token": changed.json()["csrf_token"]}
            assert web.post(ROOT + "/chat", json={"message": "hola"}, headers=user).status_code == 200

            # 4. A new phone pairs; the admin approves it straight to the user.
            phone = Phone(phones)
            rid = phone.post("/pairing/requests", variant="phone").json()["request_id"]
            assert web.post(f"{ROOT}/admin/devices/{rid}/approve", json={"user_id": ana_id},
                            headers=admin).status_code == 200
            token = phone.post("/pairing/claim", rid=rid).json()["access_token"]

            # 5. Each role sees its part, in the strictest modes for this PC and for shared tokens.
            monkeypatch.setenv(access.LOOPBACK_VARIABLE, "off")
            monkeypatch.setenv(access.BEARER_VARIABLE, "mobile")
            assert phones.post(ROOT + "/assistant/chat", headers=phone.headers("POST", "/assistant/chat", token),
                               json={"message": "hola"}).status_code == 200
            assert [d["id"] for d in web.get(ROOT + "/me/devices", headers=user).json()["devices"]] == [rid]
            assert [d["id"] for d in web.get(ROOT + "/me/devices", headers=root).json()["devices"]] == ["rid-1"]
            for path_, expected in (("/documents", 403), ("/admin/users", 403), ("/admin/audit", 403)):
                assert web.get(ROOT + path_, headers=user).status_code == expected, path_
            assert web.get(ROOT + "/admin/devices", headers=admin).status_code == 200
            assert web.get(ROOT + "/voice/diagnostic/settings", headers=admin).status_code == 403
            assert web.post(ROOT + "/chat", json={"message": "hola"}).status_code == 401  # no login, mode off
            assert web.get(ROOT + "/admin/devices", headers={"X-ITGlasses-Admin": "1"}).status_code == 403

            # 6. The audit log tells the story; the admin sees administration only.
            full = web.get(ROOT + "/admin/audit?limit=500", headers=root).json()["entries"]
            limited = web.get(ROOT + "/admin/audit?limit=500", headers=admin).json()["entries"]
    story = [entry["action"] for entry in reversed(full)
             if entry["action"] in ("bootstrap.superadmin", "user.create", "user.password_change", "device.approve")]
    assert story == ["bootstrap.superadmin", "user.create", "user.password_change", "user.create",
                     "user.password_change", "device.approve"]
    assert {entry["action"].split(".")[0] for entry in limited} <= {"user", "device", "document"}
    data, log = path.read_bytes(), str(full)
    for secret in (ROOT_PW, NEW_PW, temporary, "clave-de-la-jefa", token):
        assert secret.encode() not in data and secret not in log
