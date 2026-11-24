"""Stage "historial" 4: a user lists, opens, renames, deletes and restores only their own conversations; the admin
sees metadata only; deleted conversations are purged after the grace and old ones after the retention, with an
audit entry that never holds a title or a message."""
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.history import conversations, manage, store as history
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.users import accounts
from test_device_pairing import ROOT, setup  # noqa: F401 - setup is a fixture
from test_history_chat import ask, paired, phone_ask, rows
from test_users_access import LOCAL, PW, sign_in

CONVERSATIONS = ROOT + "/conversations"
STATS = ROOT + "/admin/conversations/stats"
PRIVATE = "pregunta privada sobre la VPN de Ana"
DAY = 86400


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(history, "_ready", set())
    for name in (conversations.ENABLED_VARIABLE, conversations.GAP_VARIABLE, conversations.RETENTION_VARIABLE,
                 conversations.GRACE_VARIABLE):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def people():
    with accounts.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        return {name: accounts.create_user(db, name, PW, role) for name, role in
                (("root", "superadmin"), ("adm", "admin"), ("adm2", "admin"), ("ana", "user"), ("beto", "user"))}


def web():
    return TestClient(create_app(MockLLM()), base_url=LOCAL, client=("127.0.0.1", 50000))


def audit_rows(action):
    with closing(sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"])) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM audit_log WHERE action=? ORDER BY id", (action,))]


def listed(client, headers, **params):
    response = client.get(CONVERSATIONS, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- own conversations ---------------------------------------------------------------------------------------------

def test_each_user_lists_and_opens_only_their_own(people):
    with web() as client:
        ana, beto = sign_in(client, "ana"), sign_in(client, "beto")
        first = ask(client, ana, PRIVATE)["session_id"]
        second = ask(client, ana, "¿Qué es DNS?")["session_id"]
        ask(client, beto, "¿Qué es un router?")
        items = listed(client, ana)["conversations"]
        assert sorted(item["id"] for item in items) == sorted([first, second])
        assert all(item["channel"] == "web" for item in items)
        opened = client.get(f"{CONVERSATIONS}/{first}", headers=ana).json()["conversation"]
        assert [m["role"] for m in opened["messages"]] == ["user", "assistant"]
        assert opened["messages"][0]["content"] == PRIVATE and opened["title"] == PRIVATE
        for who, conversation_id in ((beto, first), (ana, "no-existe")):
            refused = client.get(f"{CONVERSATIONS}/{conversation_id}", headers=who)
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "conversation_not_found"
        assert PRIVATE not in client.get(CONVERSATIONS, headers=beto).text


def test_the_list_pages_and_filters(setup, people):
    c, phone, _ = setup
    rid, token = paired(c, phone, owner=people["ana"])
    device = phone_ask(c, phone, token)["session_id"]
    with history.transaction() as db:
        db.execute("UPDATE conversations SET last_message_at=last_message_at-100")
    with TestClient(c.app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        ana = sign_in(client, "ana")
        webs = [ask(client, ana, f"pregunta {n}")["session_id"] for n in range(3)]
        page = listed(client, ana, limit=2)
        assert len(page["conversations"]) == 2 and page["next_cursor"]
        rest = listed(client, ana, limit=2, cursor=page["next_cursor"])
        assert rest["next_cursor"] is None
        seen = [item["id"] for item in page["conversations"] + rest["conversations"]]
        assert sorted(seen) == sorted(webs + [device]) and seen[-1] == device
        assert [i["id"] for i in listed(client, ana, channel="device")["conversations"]] == [device]
        assert [i["id"] for i in listed(client, ana, device_id=rid)["conversations"]] == [device]
        only = listed(client, ana, channel="device")["conversations"][0]
        assert only["device_label"] == "Z Flip6" and only["message_count"] == 2
        assert client.get(CONVERSATIONS, params={"cursor": "basura"}, headers=ana).status_code == 422


def test_rename_keeps_the_users_title(people):
    with web() as client:
        ana = sign_in(client, "ana")
        conversation_id = ask(client, ana)["session_id"]
        url = f"{CONVERSATIONS}/{conversation_id}"
        renamed = client.patch(url, json={"title": "  Dudas de red  "}, headers=ana)
        assert renamed.status_code == 200 and renamed.json()["conversation"]["title"] == "Dudas de red"
        ask(client, ana, "¿Y un router?", conversation_id)
        item = client.get(url, headers=ana).json()["conversation"]
        assert (item["title"], item["title_source"]) == ("Dudas de red", "user")
        assert client.patch(url, json={"title": "   "}, headers=ana).status_code == 422
        assert client.patch(url, json={"title": "x", "extra": 1}, headers=ana).status_code == 422
        no_csrf = {k: v for k, v in ana.items() if k != "X-CSRF-Token"}
        assert client.patch(url, json={"title": "y"}, headers=no_csrf).status_code == 403
        assert client.patch(url, json={"title": "y"}, headers=sign_in(client, "beto")).status_code == 404


# --- delete, restore, purge ----------------------------------------------------------------------------------------

def test_delete_hides_it_at_once_and_restore_brings_it_back(people):
    with web() as client:
        ana = sign_in(client, "ana")
        conversation_id = ask(client, ana, PRIVATE)["session_id"]
        assert conversation_id in client.app.state.assistant.sessions._sessions
        url = f"{CONVERSATIONS}/{conversation_id}"
        assert client.delete(url, headers=sign_in(client, "beto")).status_code == 404
        deleted = client.delete(url, headers=ana)
        assert deleted.status_code == 200 and deleted.json()["grace_days"] == 7
        assert abs(deleted.json()["purge_after"] - (time.time() + 7 * DAY)) < 60
        assert conversation_id not in client.app.state.assistant.sessions._sessions   # not in memory either
        assert listed(client, ana)["conversations"] == []
        assert [i["id"] for i in listed(client, ana, deleted=True)["conversations"]] == [conversation_id]
        assert client.get(url, headers=ana).status_code == 404
        refused = client.post(ROOT + "/assistant/chat", json={"message": "hola", "session_id": conversation_id},
                              headers=ana)
        assert refused.status_code == 404
        assert client.post(f"{url}/restore", headers=ana).status_code == 200
        assert [i["id"] for i in listed(client, ana)["conversations"]] == [conversation_id]
        assert ask(client, ana, "¿Y un router?", conversation_id)["session_id"] == conversation_id
        assert client.post(f"{url}/restore", headers=ana).status_code == 404          # not deleted any more
    [delete], [restore] = audit_rows("conversation.delete"), audit_rows("conversation.restore")
    assert (delete["actor_user_id"], delete["target_type"], delete["target_id"]) == (
        people["ana"], "conversation", conversation_id)
    assert '"messages":2' in delete["detail"] and '"channel":"web"' in delete["detail"]
    assert PRIVATE not in delete["detail"] + restore["detail"]


def test_after_the_grace_it_cannot_be_restored_and_is_purged(people):
    with web() as client:
        ana = sign_in(client, "ana")
        conversation_id = ask(client, ana, PRIVATE)["session_id"]
        client.delete(f"{CONVERSATIONS}/{conversation_id}", headers=ana)
        with history.transaction() as db:
            db.execute("UPDATE conversations SET purge_after=?", (int(time.time()) - 1,))
        assert client.post(f"{CONVERSATIONS}/{conversation_id}/restore", headers=ana).status_code == 404
        assert listed(client, ana, deleted=True)["conversations"] == []
    assert manage.purge_and_audit() == (1, 2)
    assert rows("SELECT * FROM conversations") == [] and rows("SELECT * FROM messages") == []
    [purge] = audit_rows("conversation.purge")
    assert (purge["actor_kind"], purge["target_id"]) == ("system", people["ana"])
    assert '"reason":"deleted"' in purge["detail"] and '"conversations":1' in purge["detail"]
    data = Path(os.environ["ITGLASSES_HISTORY_DB"]).read_bytes()
    assert PRIVATE.encode() not in data                                              # secure_delete


def test_the_retention_purges_old_conversations(people, monkeypatch):
    with web() as client:
        ana = sign_in(client, "ana")
        old = ask(client, ana, "vieja")["session_id"]
        recent = ask(client, ana, "reciente")["session_id"]
    with history.transaction() as db:
        db.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (int(time.time()) - 91 * DAY, old))
    monkeypatch.setenv(conversations.RETENTION_VARIABLE, "0")              # 0: no age limit
    assert manage.purge_and_audit() == (0, 0)
    monkeypatch.setenv(conversations.RETENTION_VARIABLE, "90")
    assert manage.purge_and_audit() == (1, 2)
    assert [row["id"] for row in rows("SELECT id FROM conversations")] == [recent]
    assert '"reason":"retention"' in audit_rows("conversation.purge")[0]["detail"]


def test_the_purge_runs_at_startup(people):
    with web() as client:
        ana = sign_in(client, "ana")
        conversation_id = ask(client, ana)["session_id"]
        client.delete(f"{CONVERSATIONS}/{conversation_id}", headers=ana)
    with history.transaction() as db:
        db.execute("UPDATE conversations SET purge_after=1")
    with web():
        for _ in range(50):
            if not rows("SELECT id FROM conversations"):
                break
            time.sleep(0.05)
    assert rows("SELECT id FROM conversations") == []


def test_a_grace_of_zero_days_purges_at_the_next_run(people, monkeypatch):
    monkeypatch.setenv(conversations.GRACE_VARIABLE, "0")
    with web() as client:
        ana = sign_in(client, "ana")
        conversation_id = ask(client, ana)["session_id"]
        assert client.delete(f"{CONVERSATIONS}/{conversation_id}", headers=ana).json()["grace_days"] == 0
    assert manage.purge_and_audit() == (1, 2)


# --- devices ---------------------------------------------------------------------------------------------------------

def test_deleting_a_device_conversation_makes_the_device_start_a_new_one(setup, people):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    with TestClient(c.app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        ana = sign_in(client, "ana")
        assert client.delete(f"{CONVERSATIONS}/{first}", headers=ana).status_code == 200
        second = phone_ask(c, phone, token)["session_id"]
        assert second != first
        client.post(f"{CONVERSATIONS}/{first}/restore", headers=ana)
        assert phone_ask(c, phone, token)["session_id"] == second                     # the restored one stays closed


def test_new_conversation_on_a_device(setup, people):
    c, phone, _ = setup
    rid, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    with TestClient(c.app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        url = f"{ROOT}/me/devices/{rid}/new-conversation"
        assert client.post(url, headers=sign_in(client, "beto")).status_code == 404   # not beto's device
        assert client.post(f"{ROOT}/me/devices/otro/new-conversation", headers=sign_in(client, "ana")).status_code == 404
        done = client.post(url, headers=sign_in(client, "ana"))
        assert done.status_code == 200 and done.json() == {"closed": 1}
    assert phone_ask(c, phone, token)["session_id"] != first


# --- the admin: metadata only ----------------------------------------------------------------------------------------

def test_the_admin_sees_metadata_only(setup, people):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    phone_ask(c, phone, token, message=PRIVATE)
    with TestClient(c.app, base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        ask(client, sign_in(client, "ana"), "otra pregunta privada")
        response = client.get(STATS, headers=sign_in(client, "adm"))
        assert response.status_code == 200
        users = {user["username"]: user for user in response.json()["users"]}
        assert set(users) == {"adm", "ana", "beto"}                     # manages users; not other admins or root
        ana = users["ana"]
        assert (ana["conversations"], ana["messages"], ana["web"], ana["device"]) == (2, 4, 1, 1)
        assert [d["label"] for d in ana["devices"]] == ["Z Flip6"] and users["beto"]["conversations"] == 0
        assert "privada" not in response.text and "title" not in response.text
        root = {user["username"] for user in client.get(STATS, headers=sign_in(client, "root")).json()["users"]}
        assert root == {"root", "adm", "adm2", "ana", "beto"}
        refused = client.get(STATS, headers=sign_in(client, "ana"))
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden_role"


def test_the_admin_audit_page_shows_conversation_deletes(people):
    with web() as client:
        ana = sign_in(client, "ana")
        client.delete(f"{CONVERSATIONS}/{ask(client, ana)['session_id']}", headers=ana)
        entries = client.get(ROOT + "/admin/audit", headers=sign_in(client, "adm")).json()["entries"]
    assert [e["action"] for e in entries if e["action"].startswith("conversation.")] == ["conversation.delete"]


# --- access ----------------------------------------------------------------------------------------------------------

def test_only_a_login_session_from_this_pc(people, monkeypatch):
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "token-v1")
    with web() as client:
        assert client.get(CONVERSATIONS).status_code == 401
        assert client.get(STATS).status_code == 401
        with TestClient(client.app, base_url=LOCAL, client=("172.16.5.20", 50000)) as outside:
            for method, url in (("GET", CONVERSATIONS), ("GET", STATS), ("DELETE", f"{CONVERSATIONS}/x")):
                assert outside.request(method, url, headers={"Authorization": "Bearer token-v1"}).status_code == 403


def test_without_any_history_the_list_is_empty_and_nothing_is_created(people):
    with web() as client:
        assert listed(client, sign_in(client, "ana"))["conversations"] == []
    assert not os.path.exists(os.environ["ITGLASSES_HISTORY_DB"])


@pytest.mark.parametrize(("variable", "value"), [(conversations.RETENTION_VARIABLE, "-1"),
                                                 (conversations.RETENTION_VARIABLE, "3651"),
                                                 (conversations.GRACE_VARIABLE, "91"),
                                                 (conversations.GRACE_VARIABLE, "siete")])
def test_invalid_retention_settings_stop_the_backend(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(conversations.HistoryConfigError):
        with web():
            pass
