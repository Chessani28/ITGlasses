"""Conversations and messages of the chat history (stage "historial" 2).

Who owns what:
  web with a login session   the session user; the web may only continue its own conversations
  phone or glasses (DPoP)    the device's owner (installs.user_id) at the time of the message; one conversation per
                             device continues while its last message is younger than the gap (30 min), so the open
                             app and the glasses mode share it whatever session id each of them sends
  anything else              not saved (this PC without a login, shared tokens, devices without an owner)

The assistant's working state (history, active procedure, step...) is saved with every exchange as JSON
(state_json) and loaded back after a restart; the messages table is what the pages show. Text only, never audio.
"""
import json
import os
import re
import time
import uuid

GAP_VARIABLE, GAP_DEFAULT, GAP_RANGE = "ITGLASSES_DEVICE_CONVERSATION_GAP_MINUTES", 30, (5, 1440)
# Stage "historial" 4: conversations older than the retention (0 = no limit) and deleted ones once their grace is
# over are purged (with their messages) at startup and every 6 hours.
RETENTION_VARIABLE, RETENTION_DEFAULT, RETENTION_RANGE = "ITGLASSES_HISTORY_RETENTION_DAYS", 90, (0, 3650)
GRACE_VARIABLE, GRACE_DEFAULT, GRACE_RANGE = "ITGLASSES_HISTORY_DELETED_GRACE_DAYS", 7, (0, 90)
ENABLED_VARIABLE = "ITGLASSES_HISTORY"
ON, OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}
ANSWER_MAX = 32000
TITLE_MAX = 80
STATE_MAX = 256 * 1024
# Keys of the assistant state that are never saved: bookkeeping of the in-memory store.
TRANSIENT_KEYS = {"time"}
# Dropped first when a state is too large: they are rebuilt when the user names the procedure again.
BULKY_KEYS = ("procedures", "document_steps", "active_steps", "active_sources", "web_sources")
SOURCE_FIELDS = ("citation_id", "document_id", "title", "page", "section", "url")


class HistoryConfigError(ValueError):
    code = "history_config"


def enabled():
    raw = (os.environ.get(ENABLED_VARIABLE) or "").strip().lower()
    if not raw or raw in ON:
        return True
    if raw in OFF:
        return False
    raise HistoryConfigError(f"{ENABLED_VARIABLE} debe ser on u off (valor actual: {raw[:20]!r}).")


def gap_seconds():
    raw = (os.environ.get(GAP_VARIABLE) or "").strip()
    if not raw:
        return GAP_DEFAULT * 60
    low, high = GAP_RANGE
    if not (raw.isascii() and raw.isdecimal()) or not low <= int(raw) <= high:
        raise HistoryConfigError(f"{GAP_VARIABLE} debe ser un numero entero entre {low} y {high} "
                                 f"(valor actual: {raw[:20]!r}).")
    return int(raw) * 60


def _days(variable, default, bounds):
    raw = (os.environ.get(variable) or "").strip()
    if not raw:
        return default
    low, high = bounds
    if not (raw.isascii() and raw.isdecimal()) or not low <= int(raw) <= high:
        raise HistoryConfigError(f"{variable} debe ser un numero entero de dias entre {low} y {high} "
                                 f"(valor actual: {raw[:20]!r}).")
    return int(raw)


def retention_days():
    return _days(RETENTION_VARIABLE, RETENTION_DEFAULT, RETENTION_RANGE)


def grace_days():
    return _days(GRACE_VARIABLE, GRACE_DEFAULT, GRACE_RANGE)


def check_settings():
    return enabled(), gap_seconds()


def retention_settings():
    """(retention days, grace days after a delete); HistoryConfigError if invalid, so the backend refuses to start."""
    return retention_days(), grace_days()


def _now(now):
    return int(time.time()) if now is None else int(now)


def title_from(message):
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX - 1].rstrip() + "…"


def new_conversation(db, user_id, channel, device_id=None, device_label=None, now=None):
    now = _now(now)
    conversation_id = str(uuid.uuid4())
    db.execute("INSERT INTO conversations(id,user_id,device_id,device_label,channel,created_at,last_message_at) "
               "VALUES(?,?,?,?,?,?,?)", (conversation_id, user_id, device_id, device_label, channel, now, now))
    return conversation_id


def device_conversation(db, device_id, user_id, label=None, now=None, gap=None):
    """The device's conversation to continue (same owner, open, last message within the gap) or a new one."""
    now = _now(now)
    gap = gap_seconds() if gap is None else gap
    row = db.execute("SELECT id FROM conversations WHERE device_id=? AND user_id=? AND deleted_at IS NULL "
                     "AND closed_at IS NULL AND last_message_at>=? ORDER BY last_message_at DESC LIMIT 1",
                     (device_id, user_id, now - gap)).fetchone()
    return row[0] if row else new_conversation(db, user_id, "device", device_id, label, now)


def owned_conversation(db, conversation_id, user_id):
    """The user's own, not deleted conversation, or None (someone else's looks exactly like a missing one)."""
    return db.execute("SELECT id,channel,device_id FROM conversations WHERE id=? AND user_id=? AND deleted_at IS NULL",
                      (conversation_id, user_id)).fetchone()


def snapshot(state):
    """JSON of the assistant state: keys that cannot be saved are skipped, bulky ones dropped if needed."""
    saved = {}
    for key, value in (state or {}).items():
        if key in TRANSIENT_KEYS or key.startswith("_"):
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        saved[key] = value
    text = json.dumps(saved, ensure_ascii=False)
    for key in BULKY_KEYS:
        if len(text.encode("utf-8")) <= STATE_MAX:
            break
        saved.pop(key, None)
        text = json.dumps(saved, ensure_ascii=False)
    while len(text.encode("utf-8")) > STATE_MAX and saved.get("history"):
        saved["history"] = saved["history"][1:]
        saved["history_routes"] = (saved.get("history_routes") or [])[1:]
        text = json.dumps(saved, ensure_ascii=False)
    return text if len(text.encode("utf-8")) <= STATE_MAX else None


def restore_state(state, text):
    """Put a saved state back into an (empty) in-memory state. History entries come back as tuples."""
    try:
        saved = json.loads(text) if text else {}
    except ValueError:
        return False
    if not isinstance(saved, dict):
        return False
    if isinstance(saved.get("history"), list):
        saved["history"] = [tuple(entry) for entry in saved["history"] if isinstance(entry, list) and len(entry) == 2]
    state.update(saved)
    return True


def load_state(db, conversation_id):
    row = db.execute("SELECT state_json FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return row[0] if row else None


def _sources(sources):
    kept = []
    for source in (sources or [])[:20]:
        if isinstance(source, dict):
            kept.append({key: source[key] for key in SOURCE_FIELDS if source.get(key) is not None})
    return json.dumps(kept, ensure_ascii=False) if kept else None


def record_exchange(db, conversation_id, question, answer, source, route=None, sources=None, state=None, now=None):
    """The question and the answer, the conversation's counters and title, and the assistant state. The id of the
    answer's message (what 👍/👎 refers to)."""
    now = _now(now)
    db.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
               (conversation_id, "user", str(question), source, now))
    saved = db.execute("INSERT INTO messages(conversation_id,role,content,source,route,sources_json,created_at) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (conversation_id, "assistant", str(answer or "")[:ANSWER_MAX], source, route, _sources(sources),
                        now))
    answer_id = saved.lastrowid
    db.execute("UPDATE conversations SET last_message_at=?, message_count=message_count+2, "
               "title=COALESCE(title,?), state_json=COALESCE(?,state_json) WHERE id=?",
               (now, title_from(question), snapshot(state) if state is not None else None, conversation_id))
    return answer_id  # the answer's message id: what 👍/👎 refers to
