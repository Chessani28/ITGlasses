"""The assistant chat with persistent history (stage "historial" 2): who owns the conversation, loading the
assistant state after a restart, one exchange at a time per conversation, and saving it.

Never breaks the chat: if the history cannot be used (ITGLASSES_HISTORY=off, database unavailable) the exchange
runs exactly as before stage "historial", in memory only, and the answer says history_saved=false.
"""
import asyncio
import inspect
import logging
import time
from dataclasses import dataclass

from backend.app.assistant.voice_mode import channel_for
from backend.app.knowledge.models import KnowledgeError
from . import conversations, store

logger = logging.getLogger(__name__)
LOCKS_MAX = 1000


@dataclass(frozen=True)
class Target:
    key: object                 # key of the in-memory assistant state
    conversation_id: str | None  # saved conversation, or None (not saved)
    source: str = "web"
    returned_session: str | None = None


class ConversationLocks:
    """One asyncio lock per conversation: the open app and the glasses never interleave one exchange."""

    def __init__(self):
        self._locks = {}

    def get(self, key):
        if len(self._locks) > LOCKS_MAX:
            for stale in [k for k, lock in self._locks.items() if not lock.locked()]:
                self._locks.pop(stale, None)
        return self._locks.setdefault(key, asyncio.Lock())


def _device(device_id):
    """(owner user id, label, source) of a paired device, from the device database."""
    from backend.app.pairing.store import transaction as device_transaction
    with device_transaction() as db:
        row = db.execute("SELECT user_id,name,model,variant FROM installs WHERE id=?", (device_id,)).fetchone()
    if row is None:
        return None, None, "phone"
    label = row["name"] or row["model"] or ("Ray-Ban Meta" if row["variant"] == "meta" else "Celular")
    return row["user_id"], label, "glasses" if row["variant"] == "meta" else "phone"


def _legacy(device_id, session_id):
    """Exactly the keys used before stage "historial" (in memory, not saved)."""
    if device_id:
        import uuid
        session = session_id or str(uuid.uuid4())
        return Target(key=(device_id, session), conversation_id=None, returned_session=session)
    return Target(key=session_id, conversation_id=None)


def remember_opened(app, device_id, conversation_id):
    """The conversation the phone app opened to continue it (None: forget it). In memory: after a restart the device
    simply goes back to its own conversation."""
    opened = getattr(app.state, "device_opened", None)
    if opened is None:
        opened = app.state.device_opened = {}
    if conversation_id is None:
        opened.pop(device_id, None)
    else:
        opened[device_id] = (conversation_id, time.time())


def _opened(request, db, device_id, owner):
    """The conversation this device opened from the app, while it is still in use (opened or last message within the
    conversation gap, like a device's own conversation); None otherwise."""
    opened = getattr(getattr(request.app, "state", None), "device_opened", None) or {}
    entry = opened.get(device_id)
    if not entry:
        return None
    conversation_id, at = entry
    row = db.execute("SELECT last_message_at FROM conversations WHERE id=? AND user_id=? AND deleted_at IS NULL",
                     (conversation_id, owner)).fetchone()
    now, gap = time.time(), conversations.gap_seconds()
    if row is None or (now - at > gap and (row[0] or 0) < now - gap):
        opened.pop(device_id, None)
        return None
    return conversation_id


def resolve(request, session_id, via=None):
    """Where this exchange goes. KnowledgeError 404 when a web session asks for a conversation that is not its."""
    device_id = getattr(request.state, "device_id", None)
    user_id = getattr(request.state, "user_id", None) if getattr(request.state, "auth_kind", None) == "session" else None
    if not conversations.enabled() or not (device_id or user_id):
        return _legacy(device_id, session_id)
    try:
        if device_id:
            owner, label, source = _device(device_id)
            if via == "glasses":
                source = "glasses"  # the phone app heard it through the glasses
            if not owner:
                return _legacy(device_id, session_id)  # a device without an owner: its chats are not saved
            with store.transaction() as db:
                conversation_id = (_opened(request, db, device_id, owner)
                                   or conversations.device_conversation(db, device_id, owner, label))
            return Target(key=conversation_id, conversation_id=conversation_id, source=source)
        with store.transaction() as db:
            if session_id:
                if conversations.owned_conversation(db, session_id, user_id) is None:
                    raise KnowledgeError("conversation_not_found", "Esa conversación no existe o ya no está disponible.", 404)
                conversation_id = session_id
            else:
                conversation_id = conversations.new_conversation(db, user_id, "web")
        return Target(key=conversation_id, conversation_id=conversation_id, source="web")
    except store.HistoryUnavailable:
        logger.error("History unavailable: this exchange is not saved")
        return _legacy(device_id, session_id)


def _hydrate(assistant, target):
    """Load the saved assistant state once into the in-memory store (after a restart or the 30-min expiry)."""
    if not hasattr(assistant, "sessions"):  # an assistant without in-memory state (e.g. a test double)
        return
    _, state = assistant.sessions.get(target.key)
    if state.get("_hydrated"):
        return
    with store.transaction() as db:
        saved = conversations.load_state(db, target.conversation_id)
    if saved:
        conversations.restore_state(state, saved)
    state["_hydrated"] = True


def _save(assistant, target, message, result):
    state = assistant.sessions.get(target.key)[1] if hasattr(assistant, "sessions") else None
    with store.transaction() as db:
        return conversations.record_exchange(db, target.conversation_id, message, result.get("answer"), target.source,
                                             result.get("route"), result.get("sources"), state)


def _accepts_channel(assistant):
    try:
        return "channel" in inspect.signature(assistant.chat).parameters
    except (TypeError, ValueError):
        return False


async def chat(request, assistant, locks, message, session_id, via=None):
    """The assistant's answer (dict), with session_id = the saved conversation and history_saved."""
    target = await asyncio.to_thread(resolve, request, session_id, via)
    saved = target.conversation_id is not None
    message_id = None
    async with locks.get(target.key):
        if saved:
            try:
                await asyncio.to_thread(_hydrate, assistant, target)
            except store.HistoryUnavailable:
                saved = False
        # A paired phone or the glasses (with or without an owner) get the voice mode; the web and the API token
        # do not. An assistant without the channel parameter (a test double) is asked as before.
        channel = channel_for(getattr(request.state, "device_id", None))
        if _accepts_channel(assistant):
            result = await assistant.chat(message, target.key, channel=channel)
        else:
            result = await assistant.chat(message, target.key)
        if saved:
            try:
                message_id = await asyncio.to_thread(_save, assistant, target, message, result)
            except store.HistoryUnavailable:
                saved = False
            except Exception:  # noqa: BLE001 - the answer is already there; keep it, say it was not saved
                logger.exception("History write failed")
                saved = False
    session = target.conversation_id or target.returned_session or result.get("session_id")
    return {**result, "session_id": session, "history_saved": saved, "message_id": message_id if saved else None}
