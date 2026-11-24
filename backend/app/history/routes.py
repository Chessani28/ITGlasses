"""Conversation endpoints (stage "historial" 4). Login session from this PC only (users/web.py): a change also needs
the page Origin and the CSRF token. Never published by Caddy (it publishes only the phone routes).

  GET    /api/v1/conversations                       own conversations (channel, device_id, deleted, cursor)
  GET    /api/v1/conversations/{id}                  one of them with its messages
  PATCH  /api/v1/conversations/{id}                  rename
  DELETE /api/v1/conversations/{id}                  logical delete (restorable during the grace)
  POST   /api/v1/conversations/{id}/restore          undo a delete
  POST   /api/v1/me/devices/{id}/new-conversation    the device's next message starts a new conversation
  GET    /api/v1/admin/conversations/stats           admin: per-user metadata, never titles or messages

Someone else's conversation answers exactly like a missing one (404).
"""
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.knowledge.models import KnowledgeError
from backend.app.pairing.store import transaction as device_transaction
from backend.app.users import accounts, audit_web
from backend.app.users.web import current_session
from . import conversations, manage, store

router = APIRouter(prefix="/api/v1")
ADMIN_ROLES = ("admin", "superadmin")


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


def _session(request, write=False):
    session = current_session(request, write=write)
    request.state.user_id = session.user_id
    return session


def _not_found():
    return KnowledgeError("conversation_not_found", "Esa conversación no existe o ya no está disponible.", 404)


def _unavailable():
    return KnowledgeError("history_unavailable", "El historial no está disponible en este momento.", 503)


def _forget(request, conversation_id):
    assistant = getattr(request.app.state, "assistant", None)
    forget = getattr(getattr(assistant, "sessions", None), "forget", None)
    if forget:
        forget(conversation_id)


@router.get("/conversations")
def list_conversations(request: Request, response: Response, channel: Literal["web", "device"] | None = None,
                       device_id: str | None = Query(None, max_length=128), deleted: bool = False,
                       limit: int = Query(50, ge=1, le=manage.LIST_MAX), cursor: str | None = Query(None, max_length=200)):
    session = _session(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        items, next_cursor = manage.list_conversations(session.user_id, channel, device_id, deleted, limit, cursor)
    except ValueError:
        raise KnowledgeError("invalid_cursor", "El cursor de la lista no es válido.", 422) from None
    except store.HistoryUnavailable:
        raise _unavailable() from None
    return {"conversations": items, "next_cursor": next_cursor, "saving": conversations.enabled(),
            "grace_days": conversations.grace_days(), "retention_days": conversations.retention_days()}


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str, request: Request, response: Response):
    session = _session(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"conversation": manage.conversation(conversation_id, session.user_id)}
    except manage.NotFound:
        raise _not_found() from None
    except store.HistoryUnavailable:
        raise _unavailable() from None


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: Rename, request: Request):
    session = _session(request, write=True)
    try:
        return {"conversation": manage.rename(conversation_id, session.user_id, body.title)}
    except ValueError:
        raise KnowledgeError("title_invalid", "Escribe un título.", 422) from None
    except manage.NotFound:
        raise _not_found() from None
    except store.HistoryUnavailable:
        raise _unavailable() from None


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request):
    session = _session(request, write=True)
    try:
        done = manage.delete(conversation_id, session.user_id)
    except manage.NotFound:
        raise _not_found() from None
    except store.HistoryUnavailable:
        raise _unavailable() from None
    _forget(request, conversation_id)
    audit_web.record_after(request, "conversation.delete", "conversation", conversation_id,
                           {"channel": done["channel"], "messages": done["messages"], "grace_days": done["grace_days"]})
    return {"deleted": True, "purge_after": done["purge_after"], "grace_days": done["grace_days"]}


@router.post("/conversations/{conversation_id}/restore")
def restore_conversation(conversation_id: str, request: Request):
    session = _session(request, write=True)
    try:
        done = manage.restore(conversation_id, session.user_id)
    except manage.NotFound:
        raise _not_found() from None
    except store.HistoryUnavailable:
        raise _unavailable() from None
    audit_web.record_after(request, "conversation.restore", "conversation", conversation_id,
                           {"channel": done["channel"], "messages": done["messages"]})
    return {"restored": True}


@router.post("/me/devices/{device_id}/new-conversation")
def new_device_conversation(device_id: str, request: Request):
    session = _session(request, write=True)
    with device_transaction() as db:
        owned = db.execute("SELECT 1 FROM installs WHERE id=? AND user_id=?", (device_id, session.user_id)).fetchone()
    if owned is None:
        raise KnowledgeError("device_not_found", "Ese dispositivo no existe o no es tuyo.", 404)
    try:
        closed = manage.close_device(device_id, session.user_id)
    except store.HistoryUnavailable:
        raise _unavailable() from None
    return {"closed": closed}


@router.get("/admin/conversations/stats")
def conversation_stats(request: Request, response: Response):
    session = _session(request)
    if session.role not in ADMIN_ROLES:
        raise KnowledgeError("forbidden_role", "Tu rol no permite esta accion.", 403)
    response.headers["Cache-Control"] = "no-store"
    try:
        found = manage.stats()
    except store.HistoryUnavailable:
        raise _unavailable() from None
    empty = {"conversations": 0, "messages": 0, "web": 0, "device": 0, "deleted_pending": 0, "first_at": None,
             "last_at": None, "devices": []}
    with device_transaction() as db:
        people = db.execute("SELECT id,username,display_name,role,status FROM users ORDER BY CASE role WHEN "
                            "'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, username").fetchall()
    # The same people the admin page shows: those the admin manages, and themself.
    users = [{"user_id": row["id"], "username": row["username"], "display_name": row["display_name"],
              "role": row["role"], "status": row["status"], **found.get(row["id"], empty)}
             for row in people if row["id"] == session.user_id or accounts.can_manage(session.role, session.user_id, row)]
    return {"users": users, "retention_days": conversations.retention_days(), "grace_days": conversations.grace_days()}
