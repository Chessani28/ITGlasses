"""👍/👎 on the assistant's answers (2026-11-26): what the technicians found useful, to measure and to improve.

Privacy: 👍 is only counted. 👎 keeps a copy of that question and answer (and the optional comment) for the admins,
because the user sends it knowing it is shared ("Al enviarlo, esta pregunta y respuesta se comparten…"). The admin's
list never says who sent it. A deleted or purged conversation takes its feedback with it.
"""
import sqlite3

from backend.app.history import feedback as fb, store as history
from backend.app.pairing.routes import MOBILE
from test_device_pairing import ROOT, setup  # noqa: F401 - setup is a fixture
from test_history_chat import paired, phone_ask
from test_history_manage import CONVERSATIONS, isolated, people, web  # noqa: F401 - fixtures
from test_users_access import sign_in

ADMIN = ROOT + "/admin/feedback"


def chat(client, headers, message="¿Qué es una VPN?", session_id=None):
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    response = client.post(ROOT + "/assistant/chat", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def rate(client, headers, answer, value, comment=None):
    url = f"{CONVERSATIONS}/{answer['session_id']}/messages/{answer['message_id']}/feedback"
    return client.post(url, json={"value": value, **({"comment": comment} if comment else {})}, headers=headers)


def test_the_answer_carries_its_message_id(people):
    with web() as client:
        answer = chat(client, sign_in(client, "ana"))
        assert isinstance(answer["message_id"], int)
        read = client.get(f"{CONVERSATIONS}/{answer['session_id']}", headers=sign_in(client, "ana")).json()
        assert read["conversation"]["messages"][-1]["id"] == answer["message_id"]


def test_thumbs_up_is_counted_without_keeping_the_text(people):
    with web() as client:
        ana = sign_in(client, "ana")
        answer = chat(client, ana)
        assert rate(client, ana, answer, 1).status_code == 200
        with history.transaction() as db:
            row = db.execute("SELECT value, question, answer, comment FROM feedback").fetchone()
        assert row["value"] == 1 and row["question"] is None and row["answer"] is None and row["comment"] is None
        read = client.get(f"{CONVERSATIONS}/{answer['session_id']}", headers=ana).json()
        assert read["conversation"]["messages"][-1]["feedback"] == 1


def test_thumbs_down_shares_the_exchange_and_the_comment_with_the_admin(people):
    with web() as client:
        ana = sign_in(client, "ana")
        answer = chat(client, ana, "¿Qué es una VPN?")
        assert rate(client, ana, answer, -1, "No explicó para qué sirve; mi clave es Secreta123!").status_code == 200
        admin = client.get(ADMIN, headers=sign_in(client, "adm"))
        assert admin.status_code == 200
        data = admin.json()
        assert data["summary"]["down"] == 1 and data["summary"]["up"] == 0
        [item] = data["recent_down"]
        assert item["question"] == "¿Qué es una VPN?" and item["answer"]
        assert "No explicó para qué sirve" in item["comment"]
        assert "ana" not in str(item).lower() and "user" not in item          # never who sent it


def test_changing_and_removing_a_vote(people):
    with web() as client:
        ana = sign_in(client, "ana")
        answer = chat(client, ana)
        rate(client, ana, answer, -1, "mal")
        assert rate(client, ana, answer, 1).status_code == 200                 # changed: the shared copy goes away
        with history.transaction() as db:
            row = db.execute("SELECT value, question, comment FROM feedback").fetchone()
        assert row["value"] == 1 and row["question"] is None and row["comment"] is None
        assert rate(client, ana, answer, 0).status_code == 200                 # removed
        with history.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_only_the_owner_rates_and_only_an_answer(people):
    with web() as client:
        ana = sign_in(client, "ana")
        answer = chat(client, ana)
        assert rate(client, sign_in(client, "beto"), answer, 1).status_code == 404
        question = {**answer, "message_id": answer["message_id"] - 1}         # the user's own message
        assert rate(client, ana, question, 1).status_code == 404
        assert rate(client, ana, answer, 5).status_code == 422
        no_csrf = {k: v for k, v in ana.items() if k.lower() != "x-csrf-token"}
        assert rate(client, no_csrf, answer, 1).status_code == 403


def test_only_admins_see_the_feedback(people):
    with web() as client:
        assert client.get(ADMIN, headers=sign_in(client, "ana")).status_code == 403
        assert client.get(ADMIN, headers=sign_in(client, "root")).status_code == 200


def test_a_deleted_conversation_takes_its_feedback(people):
    with web() as client:
        ana = sign_in(client, "ana")
        answer = chat(client, ana)
        rate(client, ana, answer, -1, "mal")
        assert client.delete(f"{CONVERSATIONS}/{answer['session_id']}", headers=ana).status_code == 200
        assert client.get(ADMIN, headers=sign_in(client, "adm")).json()["recent_down"] == []
        from backend.app.history import manage
        manage.purge(now=10 ** 12)
        with history.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0


def test_the_phone_rates_its_own_answers(setup, people):  # noqa: F811
    c, phone, _ = setup
    _, token = paired(c, phone, owner=people["ana"])
    answer = phone_ask(c, phone, token)
    path = "/devices/me/feedback"
    assert ("POST", ROOT + path) in MOBILE
    response = c.post(ROOT + path, headers=phone.headers("POST", path, token),
                      json={"message_id": answer["message_id"], "value": -1, "comment": "no era eso"})
    assert response.status_code == 200, response.text
    with history.transaction() as db:
        assert db.execute("SELECT source FROM feedback").fetchone()["source"] == "phone"


def test_the_summary_counts_useful_answers():
    summary = fb.summarize([1, 1, 1, -1])
    assert summary == {"up": 3, "down": 1, "total": 4, "useful_percent": 75}
    assert fb.summarize([]) == {"up": 0, "down": 0, "total": 0, "useful_percent": None}
