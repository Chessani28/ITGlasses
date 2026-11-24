"""Stage "historial" 2: the assistant chat saved per user and device, surviving restarts; app and glasses share a
device's conversation; nothing else is saved; the chat never fails because of the history."""
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.history import conversations, store as history
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.users import accounts
from test_device_pairing import ROOT, admin_client, setup  # noqa: F401 - setup is a fixture
from test_users_access import LOCAL, PW, sign_in

CHAT = ROOT + "/assistant/chat"
GENERAL = "¿Qué es un switch de red?"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "_ready", set())
    for name in (conversations.ENABLED_VARIABLE, conversations.GAP_VARIABLE):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def people(monkeypatch):
    with accounts.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        return {name: accounts.create_user(db, name, PW, "user") for name in ("ana", "beto")}


def web():
    return TestClient(create_app(MockLLM()), base_url=LOCAL, client=("127.0.0.1", 50000))


def rows(sql, parameters=()):
    """Rows of the history database, read-only. "Nothing saved" may mean no file or no tables yet (the database is
    only created when the first conversation is saved), so both count as no rows; this never creates the file."""
    path = os.environ["ITGLASSES_HISTORY_DB"]
    if not os.path.exists(path):
        return []
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone():
            return []
        return [dict(row) for row in db.execute(sql, parameters)]


def ask(client, headers, message=GENERAL, session_id=None):
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    response = client.post(CHAT, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- web ------------------------------------------------------------------------------------------------------

def test_a_web_conversation_is_saved_and_continued(people):
    with web() as client:
        ana = sign_in(client, "ana")
        first = ask(client, ana)
        assert first["history_saved"] is True
        second = ask(client, ana, "¿Y qué es un router?", first["session_id"])
        assert second["session_id"] == first["session_id"]
    [conversation] = rows("SELECT * FROM conversations")
    assert (conversation["id"], conversation["user_id"], conversation["channel"], conversation["device_id"],
            conversation["message_count"], conversation["title"]) == (
        first["session_id"], people["ana"], "web", None, 4, GENERAL)
    messages = rows("SELECT role,content,source,route FROM messages ORDER BY id")
    assert [(m["role"], m["source"]) for m in messages] == [("user", "web"), ("assistant", "web")] * 2
    assert messages[0]["content"] == GENERAL and messages[1]["route"] == "general"


def test_it_survives_a_restart_with_the_assistant_state(people):
    with web() as client:
        ana = sign_in(client, "ana")
        first = ask(client, ana)
    with web() as restarted:  # a new backend process: empty memory
        ana = sign_in(restarted, "ana")
        ask(restarted, ana, "¿Y qué es un router?", first["session_id"])
        state = restarted.app.state.assistant.sessions._sessions[first["session_id"]]
        assert [entry[0] for entry in state["history"]] == [GENERAL, "¿Y qué es un router?"]
        assert all(isinstance(entry, tuple) for entry in state["history"])


def test_nobody_continues_someone_elses_or_a_deleted_conversation(people):
    with web() as client:
        mine = ask(client, sign_in(client, "ana"))["session_id"]
        beto = sign_in(client, "beto")
        for session_id in (mine, "no-existe"):
            refused = client.post(CHAT, json={"message": "hola", "session_id": session_id}, headers=beto)
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "conversation_not_found"
        with history.transaction() as db:
            db.execute("UPDATE conversations SET deleted_at=1")
        assert client.post(CHAT, json={"message": "hola", "session_id": mine},
                           headers=sign_in(client, "ana")).status_code == 404
    assert rows("SELECT COUNT(*) AS n FROM messages")[0]["n"] == 2


def test_without_a_login_nothing_is_saved(people, monkeypatch):
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "token-v1")
    with web() as client:
        anonymous = ask(client, {})
        assert anonymous["history_saved"] is False and anonymous["session_id"]
        assert ask(client, {}, "¿Y un router?", anonymous["session_id"])["session_id"] == anonymous["session_id"]
        with TestClient(client.app, base_url=LOCAL, client=("172.16.5.20", 50000)) as outside:
            assert ask(outside, {"Authorization": "Bearer token-v1"})["history_saved"] is False
    assert rows("SELECT id FROM conversations") == []


def test_history_off_keeps_the_previous_behaviour(people, monkeypatch):
    monkeypatch.setenv(conversations.ENABLED_VARIABLE, "off")
    with web() as client:
        answer = ask(client, sign_in(client, "ana"))
        assert answer["history_saved"] is False and answer["session_id"]
    assert not os.path.exists(os.environ["ITGLASSES_HISTORY_DB"])


def test_an_unusable_history_never_breaks_the_chat(people, monkeypatch, tmp_path, caplog):
    future = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(future)) as db, db:
        db.execute("PRAGMA user_version=99")
    monkeypatch.setenv("ITGLASSES_HISTORY_DB", str(future))
    with web() as client:
        answer = ask(client, sign_in(client, "ana"))
    assert answer["history_saved"] is False and answer["answer"]
    assert "History unavailable" in caplog.text


def test_one_assistant_for_the_whole_backend(people):
    with web() as client:
        ana = sign_in(client, "ana")
        ask(client, ana)
        first = client.app.state.assistant
        ask(client, ana)
        assert client.app.state.assistant is first


# --- phones and glasses (DPoP) --------------------------------------------------------------------------------

def paired(c, phone, owner=None, name="Z Flip6", variant="phone"):
    rid = phone.post("/pairing/requests", variant=variant).json()["request_id"]
    assert admin_client(c).post(f"{ROOT}/admin/devices/{rid}/approve").status_code == 200
    token = phone.post("/pairing/claim", rid=rid).json()["access_token"]
    with closing(sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"])) as db, db:
        db.execute("UPDATE installs SET user_id=?, name=? WHERE id=?", (owner, name, rid))
    return rid, token


def phone_ask(c, phone, token, session_id=None, message=GENERAL):
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    response = c.post(CHAT, headers=phone.headers("POST", "/assistant/chat", token), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_app_and_glasses_share_the_device_conversation(setup, people):
    c, phone, _ = setup
    rid, token = paired(c, phone, owner=people["ana"])
    app_open = phone_ask(c, phone, token)                                   # the open app: no session yet
    glasses = phone_ask(c, phone, token, session_id="otra-sesion-del-modo-lentes")
    again = phone_ask(c, phone, token, session_id=app_open["session_id"])
    assert app_open["session_id"] == glasses["session_id"] == again["session_id"]
    assert all(r["history_saved"] for r in (app_open, glasses, again))
    [conversation] = rows("SELECT * FROM conversations")
    assert (conversation["user_id"], conversation["device_id"], conversation["channel"], conversation["device_label"],
            conversation["message_count"]) == (people["ana"], rid, "device", "Z Flip6", 6)
    assert {m["source"] for m in rows("SELECT source FROM messages")} == {"phone"}


def test_a_new_device_conversation_after_the_gap_or_when_closed(setup, people):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    with history.transaction() as db:
        db.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (int(time.time()) - 31 * 60, first))
    second = phone_ask(c, phone, token)["session_id"]
    assert second != first
    with history.transaction() as db:
        db.execute("UPDATE conversations SET closed_at=? WHERE id=?", (int(time.time()), second))
    assert phone_ask(c, phone, token)["session_id"] not in (first, second)


def test_the_gap_is_configurable(setup, people, monkeypatch):
    monkeypatch.setenv(conversations.GAP_VARIABLE, "5")
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    with history.transaction() as db:
        db.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (int(time.time()) - 6 * 60, first))
    assert phone_ask(c, phone, token)["session_id"] != first


def test_a_device_without_owner_is_not_saved_and_works_as_before(setup):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=None)
    answer = phone_ask(c, phone, token, session_id="shared")
    assert answer["session_id"] == "shared" and answer["history_saved"] is False
    assert rows("SELECT * FROM conversations") == []


def test_a_reassigned_device_starts_a_conversation_for_its_new_owner(setup, people):
    c, phone, _ = setup
    rid, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    with closing(sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"])) as db, db:
        db.execute("UPDATE installs SET user_id=? WHERE id=?", (people["beto"], rid))
    second = phone_ask(c, phone, token)["session_id"]
    assert second != first
    owners = {row["id"]: row["user_id"] for row in rows("SELECT id,user_id FROM conversations")}
    assert owners == {first: people["ana"], second: people["beto"]}


def test_glasses_registered_as_meta_are_marked_as_glasses(setup, people):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"], name=None, variant="meta")
    phone_ask(c, phone, token)
    assert {m["source"] for m in rows("SELECT source FROM messages")} == {"glasses"}
    assert rows("SELECT device_label FROM conversations")[0]["device_label"] == "Ray-Ban Meta"


def test_the_owner_continues_a_device_conversation_from_the_web(setup, people):
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    device_conversation = phone_ask(c, phone, token)["session_id"]
    with admin_client(c, headers={}) as panel:
        answer = ask(panel, sign_in(panel, "ana"), "¿Y qué es un router?", device_conversation)
    assert answer["session_id"] == device_conversation
    assert [m["source"] for m in rows("SELECT source FROM messages ORDER BY id")] == ["phone", "phone", "web", "web"]


# --- state snapshot ---------------------------------------------------------------------------------------------

def test_snapshot_round_trip_skips_what_cannot_be_saved():
    state = {"time": 5.0, "_hydrated": True, "history": [("p", "r")], "history_routes": ["general"],
             "active_document": {"title": "Manual"}, "weird": {1, 2}, "current_step_index": 3}
    text = conversations.snapshot(state)
    saved = json.loads(text)
    assert "time" not in saved and "_hydrated" not in saved and "weird" not in saved
    restored = {}
    assert conversations.restore_state(restored, text)
    assert restored["history"] == [("p", "r")] and restored["current_step_index"] == 3


def test_a_huge_state_drops_bulky_parts_then_oldest_history(monkeypatch):
    monkeypatch.setattr(conversations, "STATE_MAX", 2000)
    state = {"procedures": ["x" * 3000], "history": [(f"p{i}", "r" * 300) for i in range(10)],
             "history_routes": ["general"] * 10, "active_route": "general"}
    saved = json.loads(conversations.snapshot(state))
    assert "procedures" not in saved and saved["active_route"] == "general"
    assert 0 < len(saved["history"]) < 10 and saved["history"][-1][0] == "p9"


@pytest.mark.parametrize(("variable", "value"), [(conversations.ENABLED_VARIABLE, "quizas"),
                                                 (conversations.GAP_VARIABLE, "4"), (conversations.GAP_VARIABLE, "1441")])
def test_invalid_history_settings_stop_the_backend(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(conversations.HistoryConfigError):
        with web():
            pass


def test_summary_shows_metadata_only(setup, people, capsys):
    from backend.app.history import summary
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    phone_ask(c, phone, token, message="mi pregunta privada sobre DNS")
    assert summary.main([]) == 0
    out = capsys.readouterr().out
    assert "Z Flip6" in out and "phone=2" in out and "abierta" in out
    assert "privada" not in out
