"""Chat history in the phone app: the device owner's own conversations (web, phone and glasses), read them, continue
one (it becomes the device's current conversation, shared with the glasses) or start a new one. Exact POST routes:
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


def test_the_list_has_only_the_owners_conversations_including_the_web(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    mine = phone_ask(c, phone, token)["session_id"]
    with web() as client:
        web_id = ask(client, sign_in(client, "ana"))["session_id"]
        ask(client, sign_in(client, "beto"))                       # someone else's: never listed
    listed = call(c, phone, token, LIST).json()
    assert {item["id"] for item in listed["conversations"]} == {mine, web_id}
    assert all("title" in item for item in listed["conversations"])


def test_reading_a_conversation_and_someone_elses_looks_missing(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    mine = phone_ask(c, phone, token)["session_id"]
    read = call(c, phone, token, READ, {"id": mine})
    assert read.status_code == 200
    roles = [message["role"] for message in read.json()["conversation"]["messages"]]
    assert roles == ["user", "assistant"]
    with web() as client:
        others = ask(client, sign_in(client, "beto"))["session_id"]
    assert call(c, phone, token, READ, {"id": others}).status_code == 404


def test_opening_a_conversation_makes_the_next_message_continue_it(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    with web() as client:
        web_id = ask(client, sign_in(client, "ana"))["session_id"]
    phone_ask(c, phone, token)                                    # the device's own current conversation
    assert call(c, phone, token, OPEN, {"id": web_id}).status_code == 200
    answer = phone_ask(c, phone, token, message="¿Y qué es un router?")
    assert answer["session_id"] == web_id                         # the phone (and the glasses) continue it
    [conversation] = rows("SELECT message_count FROM conversations WHERE id=?", (web_id,))
    assert conversation["message_count"] == 4


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
