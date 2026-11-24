"""Stage "historial" 6: the chat history from start to end with the defaults of this stage (nothing without a
login on this PC, ITGLASSES_LOOPBACK_MODE=off): web and phone conversations saved per user, app and glasses in
one conversation, privacy between users, metadata only for the admin, delete/restore, a new device conversation,
a restart, the purge, the backup set and an audit log without content."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import backups
from backend.app.history import conversations, manage, store as history
from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import access, accounts
from test_device_pairing import ROOT, Phone, admin_client
from test_users_access import PW, sign_in

ORIGIN = "https://pilot.example"
PRIVATE = "pregunta privada de Ana sobre su VPN"
FOLLOWUP = "¿Y para qué sirve?"


def chat(web, headers, message, session_id=None):
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    response = web.post(ROOT + "/assistant/chat", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_chat_history_end_to_end(tmp_path, monkeypatch):
    for name, value in (("ITGLASSES_PAIRING_DB", "devices.sqlite3"), ("ITGLASSES_HISTORY_DB", "history.sqlite3"),
                        ("ITGLASSES_BACKUP_DIR", "backups")):
        monkeypatch.setenv(name, str(tmp_path / value))
    monkeypatch.setenv("ITGLASSES_PAIRING_ORIGIN", ORIGIN)
    monkeypatch.setattr(store, "_ready", set())
    monkeypatch.setattr(history, "_ready", set())
    for name in (access.LOOPBACK_VARIABLE, access.BEARER_VARIABLE, "ITGLASSES_API_TOKENS", conversations.ENABLED_VARIABLE,
                 conversations.GAP_VARIABLE, conversations.RETENTION_VARIABLE, conversations.GRACE_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    assert access.loopback_mode() == "off"
    with accounts.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        ids = {name: accounts.create_user(db, name, PW, role) for name, role in
               (("root", "superadmin"), ("adm", "admin"), ("ana", "user"), ("beto", "user"))}

    with TestClient(create_app(MockLLM()), base_url=ORIGIN) as phones:
        with admin_client(phones, headers={}) as web:
            # 1. Without a login this PC does nothing, and the login page knows it.
            assert web.get(ROOT + "/auth/config").json()["loopback_mode"] == "off"
            assert web.post(ROOT + "/assistant/chat", json={"message": "hola"}).status_code == 401
            assert web.get(ROOT + "/conversations").status_code == 401

            # 2. Ana's web conversation is saved and continued.
            ana = sign_in(web, "ana")
            first = chat(web, ana, PRIVATE)
            web_id = first["session_id"]
            assert first["history_saved"] is True
            assert chat(web, ana, FOLLOWUP, web_id)["session_id"] == web_id

            # 3. Her phone, approved straight to her: the open app and the glasses share one conversation.
            admin = sign_in(web, "adm")
            phone = Phone(phones)
            rid = phone.post("/pairing/requests", variant="phone").json()["request_id"]
            assert web.post(f"{ROOT}/admin/devices/{rid}/approve", json={"user_id": ids["ana"]},
                            headers=admin).status_code == 200
            token = phone.post("/pairing/claim", rid=rid).json()["access_token"]

            def phone_chat(session_id=None):
                body = {"message": "¿Qué es DNS?", **({"session_id": session_id} if session_id else {})}
                response = phones.post(ROOT + "/assistant/chat", json=body,
                                       headers=phone.headers("POST", "/assistant/chat", token))
                assert response.status_code == 200, response.text
                return response.json()

            device_id = phone_chat()["session_id"]
            assert phone_chat("sesion-del-modo-lentes")["session_id"] == device_id

            # 4. Ana sees both; Beto sees nothing of hers; the admin sees counts, never content.
            mine = web.get(ROOT + "/conversations", headers=ana).json()["conversations"]
            assert {item["id"] for item in mine} == {web_id, device_id}
            assert [i["id"] for i in web.get(ROOT + "/conversations?channel=device", headers=ana)
                    .json()["conversations"]] == [device_id]
            beto = sign_in(web, "beto")
            assert web.get(ROOT + "/conversations", headers=beto).json()["conversations"] == []
            assert web.get(f"{ROOT}/conversations/{web_id}", headers=beto).status_code == 404
            assert web.post(ROOT + "/assistant/chat", json={"message": "hola", "session_id": web_id},
                            headers=beto).status_code == 404
            stats = web.get(ROOT + "/admin/conversations/stats", headers=admin)
            row = next(user for user in stats.json()["users"] if user["username"] == "ana")
            assert (row["conversations"], row["messages"], row["web"], row["device"]) == (2, 8, 1, 1)
            assert PRIVATE not in stats.text and "title" not in stats.text
            assert web.get(ROOT + "/admin/conversations/stats", headers=ana).status_code == 403

            # 5. Deleting the device conversation: the phone starts a new one; the delete can be undone.
            assert web.delete(f"{ROOT}/conversations/{device_id}", headers=ana).status_code == 200
            assert phone_chat()["session_id"] != device_id
            assert web.post(f"{ROOT}/conversations/{device_id}/restore", headers=ana).status_code == 200

            # 6. "New conversation" on her phone, from the web.
            assert web.post(f"{ROOT}/me/devices/{rid}/new-conversation", headers=ana).json() == {"closed": 1}

    # 7. A restart: the conversation and the assistant's context come back.
    with TestClient(create_app(MockLLM()), base_url=ORIGIN) as again:
        with admin_client(again, headers={}) as web:
            ana = sign_in(web, "ana")
            opened = web.get(f"{ROOT}/conversations/{web_id}", headers=ana).json()["conversation"]
            assert [m["content"] for m in opened["messages"] if m["role"] == "user"] == [PRIVATE, FOLLOWUP]
            chat(web, ana, "¿Me lo resumes?", web_id)
            state = again.app.state.assistant.sessions._sessions[web_id]
            assert [entry[0] for entry in state["history"]][:2] == [PRIVATE, FOLLOWUP]

            # 8. Deleted, then purged when the grace is over.
            assert web.delete(f"{ROOT}/conversations/{web_id}", headers=ana).status_code == 200
    with history.transaction() as db:
        db.execute("UPDATE conversations SET purge_after=1 WHERE id=?", (web_id,))
    assert manage.purge_and_audit() == (1, 6)
    assert PRIVATE.encode() not in Path(os.environ["ITGLASSES_HISTORY_DB"]).read_bytes()

    # 9. A backup set holds both databases; the audit tells the story without any content.
    folder = backups.backup_all("aceptacion")
    assert {f["name"] for f in json.loads((folder / "manifest.json").read_text())["files"]} == {
        "devices.sqlite3", "history.sqlite3"}
    with closing(sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"])) as db:
        story = db.execute("SELECT action, detail FROM audit_log WHERE action LIKE 'conversation.%' ORDER BY id").fetchall()
    assert [action for action, _ in story] == ["conversation.delete", "conversation.restore", "conversation.delete",
                                               "conversation.purge"]
    assert all(PRIVATE not in (detail or "") and FOLLOWUP not in (detail or "") for _, detail in story)
