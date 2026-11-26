"""Chat history in the phone app: only this phone's conversations (its app and its glasses, with the questions per
source), read them, continue one (shared with the glasses) or start a new one. Exact POST routes:
Caddy never publishes path parameters and DPoP proofs reject query strings."""
import pytest

from backend.app.pairing.routes import MOBILE
from test_device_pairing import ROOT, admin_client, setup  # noqa: F401 - setup is a fixture
from test_history_chat import CHAT, ask, isolated, paired, people, phone_ask, rows, web  # noqa: F401
from test_users_access import sign_in

LIST, READ, OPEN, NEW = ("/devices/me/conversations/" + name for name in ("list", "read", "open", "new"))


def call(c, phone, token, path, body=None):
    return c.post(ROOT + path, headers=phone.headers("POST", path, token), json=body or {})


def test_the_routes_are_exact_phone_routes():
    for path in (LIST, READ, OPEN, NEW):
        assert ("POST", ROOT + path) in MOBILE


def test_the_list_has_only_this_phones_conversations(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    mine = phone_ask(c, phone, token)["session_id"]
    with web() as client:
        ask(client, sign_in(client, "ana"))                        # the owner's web conversation: not listed
        ask(client, sign_in(client, "beto"))                       # someone else's: never listed
    listed = call(c, phone, token, LIST).json()["conversations"]
    assert [item["id"] for item in listed] == [mine]
    assert listed[0]["title"] and listed[0]["sources"] == {"phone": 1}


def test_another_phone_of_the_same_owner_is_not_listed(setup, people):  # noqa: F811
    from backend.app.history import conversations, store
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    phone_ask(c, phone, token)
    with store.transaction() as db:                                # the owner's other phone
        other_id = conversations.device_conversation(db, "otro-celular", people["ana"], "Otro")
        conversations.record_exchange(db, other_id, "Hola", "Hola", "phone")
    listed = call(c, phone, token, LIST).json()["conversations"]
    assert other_id not in {item["id"] for item in listed}
    assert call(c, phone, token, READ, {"id": other_id}).status_code == 404
    assert call(c, phone, token, OPEN, {"id": other_id}).status_code == 404


def test_questions_through_the_glasses_are_marked(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    phone_ask(c, phone, token)                                    # typed on the phone
    body = {"message": "¿Y qué es un router?", "via": "glasses"}
    response = c.post(CHAT, headers=phone.headers("POST", "/assistant/chat", token), json=body)
    assert response.status_code == 200, response.text
    [item] = call(c, phone, token, LIST).json()["conversations"]
    assert item["sources"] == {"phone": 1, "glasses": 1}
    messages = call(c, phone, token, READ, {"id": item["id"]}).json()["conversation"]["messages"]
    assert [m["source"] for m in messages] == ["phone", "phone", "glasses", "glasses"]


def test_reading_a_conversation_and_the_web_or_someone_elses_looks_missing(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    mine = phone_ask(c, phone, token)["session_id"]
    read = call(c, phone, token, READ, {"id": mine})
    assert read.status_code == 200
    roles = [message["role"] for message in read.json()["conversation"]["messages"]]
    assert roles == ["user", "assistant"]
    with web() as client:
        web_id = ask(client, sign_in(client, "ana"))["session_id"]
        others = ask(client, sign_in(client, "beto"))["session_id"]
    assert call(c, phone, token, READ, {"id": web_id}).status_code == 404
    assert call(c, phone, token, READ, {"id": others}).status_code == 404


def test_opening_an_older_conversation_makes_the_next_message_continue_it(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    older = phone_ask(c, phone, token)["session_id"]
    call(c, phone, token, NEW)
    assert phone_ask(c, phone, token)["session_id"] != older       # the device's new conversation
    assert call(c, phone, token, OPEN, {"id": older}).status_code == 200
    answer = phone_ask(c, phone, token, message="¿Y qué es un router?")
    assert answer["session_id"] == older                          # the phone (and the glasses) continue it
    [conversation] = rows("SELECT message_count FROM conversations WHERE id=?", (older,))
    assert conversation["message_count"] == 4


def test_a_web_conversation_cannot_be_opened_from_the_phone(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    with web() as client:
        web_id = ask(client, sign_in(client, "ana"))["session_id"]
    assert call(c, phone, token, OPEN, {"id": web_id}).status_code == 404


def test_new_starts_a_fresh_conversation(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    first = phone_ask(c, phone, token)["session_id"]
    assert call(c, phone, token, NEW).status_code == 200
    assert phone_ask(c, phone, token)["session_id"] != first


def test_someone_elses_conversation_cannot_be_opened(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    with web() as client:
        others = ask(client, sign_in(client, "beto"))["session_id"]
    assert call(c, phone, token, OPEN, {"id": others}).status_code == 404


def test_a_device_without_owner_has_no_history(setup):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=None)
    response = call(c, phone, token, LIST)
    assert response.status_code == 403
    assert "dueño" in response.json()["error"]["message"]


def test_without_a_device_token_nothing_is_listed(setup):  # noqa: F811
    c, phone, _ = setup
    assert c.post(ROOT + LIST, json={}).status_code == 401
