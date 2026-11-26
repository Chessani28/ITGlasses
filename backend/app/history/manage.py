"""What a user does with their saved conversations, the admin's metadata and the purge (stage "historial" 4).

Privacy:
  a user      sees, opens, renames, deletes and restores only their own conversations
  an admin    sees per-user metadata only (counts, dates, devices): never a title or a message; the superadmin too
  audit       deletes, restores and purges are recorded with ids and counts, never a title or a message

Deleting is logical first: the conversation disappears for its owner at once (and a device stops continuing it)
and is purged, messages included, when the grace (7 days) is over; until then the owner may restore it. Every
conversation is purged when its last message is older than the retention (90 days). The file overwrites what is
purged (secure_delete); the daily backups keep their copies until they rotate out (7 days).
"""
import asyncio
import json
import logging
import time

from . import conversations, store

logger = logging.getLogger(__name__)

LIST_FIELDS = ("id,title,title_source,channel,device_id,device_label,created_at,last_message_at,message_count,"
               "closed_at,deleted_at,purge_after")
LIST_MAX = 200
MESSAGES_MAX = 2000
PURGE_SECONDS = 6 * 3600
DAY = 86400


class NotFound(LookupError):
    """Not the user's, deleted (or past its grace) or missing: the same answer for all of them."""


def _now(now):
    return int(time.time()) if now is None else int(now)


def _exists():
    return store.database_path().is_file()


def _cursor(value):
    """"last_message_at:id" of the last item of the previous page."""
    try:
        stamp, conversation_id = str(value).split(":", 1)
        return int(stamp), conversation_id
    except ValueError:
        raise ValueError("invalid cursor") from None


def list_conversations(user_id, channel=None, device_id=None, deleted=False, limit=50, cursor=None, now=None,
                       with_sources=False):
    """The user's conversations, newest first: (items, next cursor). deleted=True: the ones that can be restored."""
    if not _exists():
        return [], None
    now = _now(now)
    limit = max(1, min(int(limit), LIST_MAX))
    sql = f"SELECT {LIST_FIELDS} FROM conversations WHERE user_id=?"
    parameters = [user_id]
    if deleted:
        sql += " AND deleted_at IS NOT NULL AND purge_after>?"
        parameters.append(now)
    else:
        sql += " AND deleted_at IS NULL"
    if channel:
        sql += " AND channel=?"
        parameters.append(channel)
    if device_id:
        sql += " AND device_id=?"
        parameters.append(device_id)
    if cursor:
        stamp, last_id = _cursor(cursor)
        sql += " AND (last_message_at<? OR (last_message_at=? AND id<?))"
        parameters += [stamp, stamp, last_id]
    sql += " ORDER BY last_message_at DESC, id DESC LIMIT ?"
    parameters.append(limit + 1)
    with store.transaction() as db:
        rows = [dict(row) for row in db.execute(sql, parameters)]
        more = len(rows) > limit
        rows = rows[:limit]
        if with_sources and rows:
            _add_sources(db, rows)
    return rows, (f"{rows[-1]['last_message_at']}:{rows[-1]['id']}" if more else None)


def _add_sources(db, rows):
    """Each conversation's questions per source ({"phone": 3, "glasses": 1}): the phone app shows its icons."""
    marks = ",".join("?" * len(rows))
    counts = {}
    for row in db.execute(f"SELECT conversation_id, source, COUNT(*) AS n FROM messages WHERE role='user' "
                          f"AND conversation_id IN ({marks}) GROUP BY conversation_id, source", [r["id"] for r in rows]):
        counts.setdefault(row["conversation_id"], {})[row["source"]] = row["n"]
    for row in rows:
        row["sources"] = counts.get(row["id"], {})


def _owned(db, conversation_id, user_id, device_id=None):
    row = db.execute(f"SELECT {LIST_FIELDS} FROM conversations WHERE id=? AND user_id=? AND deleted_at IS NULL",
                     (conversation_id, user_id)).fetchone()
    if row is not None and device_id is not None and row["device_id"] != device_id:
        row = None  # held on another device or on the web: the phone app only sees its own
    if row is None:
        raise NotFound(conversation_id)
    return row


def conversation(conversation_id, user_id, device_id=None):
    """The user's conversation with its messages (the newest MESSAGES_MAX). device_id: only one held on that device."""
    if not _exists():
        raise NotFound(conversation_id)
    with store.transaction() as db:
        item = dict(_owned(db, conversation_id, user_id, device_id))
        rows = db.execute("SELECT id,role,content,source,route,sources_json,created_at FROM messages "
                          "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                          (conversation_id, MESSAGES_MAX + 1)).fetchall()
    truncated = len(rows) > MESSAGES_MAX
    messages = []
    for row in reversed(rows[:MESSAGES_MAX]):
        message = dict(row)
        sources = message.pop("sources_json")
        message["sources"] = json.loads(sources) if sources else []
        messages.append(message)
    item["messages"] = messages
    item["truncated"] = truncated
    return item


def rename(conversation_id, user_id, title):
    """The user's own title (kept from now on; an exchange never replaces it)."""
    title = conversations.title_from(title)
    if not title:
        raise ValueError("empty title")
    if not _exists():
        raise NotFound(conversation_id)
    with store.transaction() as db:
        _owned(db, conversation_id, user_id)
        db.execute("UPDATE conversations SET title=?, title_source='user' WHERE id=?", (title, conversation_id))
        return dict(_owned(db, conversation_id, user_id))


def delete(conversation_id, user_id, now=None):
    """Logical delete: hidden at once, closed (a device starts a new one), purged after the grace."""
    if not _exists():
        raise NotFound(conversation_id)
    now = _now(now)
    grace = conversations.grace_days()
    with store.transaction() as db:
        row = _owned(db, conversation_id, user_id)
        db.execute("UPDATE conversations SET deleted_at=?, purge_after=?, closed_at=COALESCE(closed_at,?) WHERE id=?",
                   (now, now + grace * DAY, now, conversation_id))
    return {"channel": row["channel"], "messages": row["message_count"], "grace_days": grace,
            "purge_after": now + grace * DAY}


def restore(conversation_id, user_id, now=None):
    """Undo a delete while its grace lasts (a device conversation stays closed: the device starts a new one)."""
    if not _exists():
        raise NotFound(conversation_id)
    now = _now(now)
    with store.transaction() as db:
        row = db.execute("SELECT channel,message_count FROM conversations WHERE id=? AND user_id=? "
                         "AND deleted_at IS NOT NULL AND purge_after>?", (conversation_id, user_id, now)).fetchone()
        if row is None:
            raise NotFound(conversation_id)
        db.execute("UPDATE conversations SET deleted_at=NULL, purge_after=NULL WHERE id=?", (conversation_id,))
        return {"channel": row["channel"], "messages": row["message_count"]}


def open_for_device(conversation_id, user_id, device_id, now=None):
    """Check that the user may continue this conversation of this device (phone app: "continue") and close the
    device's other open conversations. The chat layer then routes the device's messages to it (see chat.resolve)."""
    if not _exists():
        raise NotFound(conversation_id)
    with store.transaction() as db:
        _owned(db, conversation_id, user_id, device_id)
        db.execute("UPDATE conversations SET closed_at=? WHERE device_id=? AND user_id=? AND closed_at IS NULL "
                   "AND deleted_at IS NULL AND id<>?", (_now(now), device_id, user_id, conversation_id))
    return {"id": conversation_id}


def close_device(device_id, user_id, now=None):
    """Close the device's open conversations: its next message starts a new one. Number closed."""
    if not _exists():
        return 0
    with store.transaction() as db:
        return db.execute("UPDATE conversations SET closed_at=? WHERE device_id=? AND user_id=? AND closed_at IS NULL "
                          "AND deleted_at IS NULL", (_now(now), device_id, user_id)).rowcount


def stats():
    """{user_id: metadata} of every user with saved conversations: counts, dates and devices, never titles."""
    if not _exists():
        return {}
    with store.transaction() as db:
        users = {row["user_id"]: dict(row) for row in db.execute(
            "SELECT user_id, SUM(deleted_at IS NULL) AS conversations, "
            "SUM(CASE WHEN deleted_at IS NULL THEN message_count ELSE 0 END) AS messages, "
            "SUM(deleted_at IS NULL AND channel='web') AS web, SUM(deleted_at IS NULL AND channel='device') AS device, "
            "SUM(deleted_at IS NOT NULL) AS deleted_pending, "
            "MIN(CASE WHEN deleted_at IS NULL THEN created_at END) AS first_at, "
            "MAX(CASE WHEN deleted_at IS NULL THEN last_message_at END) AS last_at "
            "FROM conversations GROUP BY user_id")}
        for row in db.execute("SELECT user_id, device_id, MAX(device_label) AS label, COUNT(*) AS conversations, "
                              "MAX(last_message_at) AS last_at FROM conversations "
                              "WHERE deleted_at IS NULL AND device_id IS NOT NULL "
                              "GROUP BY user_id, device_id ORDER BY last_at DESC"):
            users[row["user_id"]].setdefault("devices", []).append(
                {"device_id": row["device_id"], "label": row["label"], "conversations": row["conversations"],
                 "last_at": row["last_at"]})
    for item in users.values():
        item.pop("user_id")
        item.setdefault("devices", [])
    return users


def purge(now=None):
    """Purge the deleted conversations past their grace and those older than the retention, with their messages.
    [{user_id, reason, conversations, messages}] for the audit (counts only)."""
    if not _exists():
        return []
    now = _now(now)
    retention, _ = conversations.retention_settings()
    oldest = now - retention * DAY if retention else None
    with store.transaction() as db:
        rows = db.execute(
            "SELECT id,user_id,message_count, CASE WHEN deleted_at IS NOT NULL THEN 'deleted' ELSE 'retention' END "
            "AS reason FROM conversations WHERE (deleted_at IS NOT NULL AND purge_after<=?) "
            "OR (deleted_at IS NULL AND ? IS NOT NULL AND last_message_at<?)", (now, oldest, oldest)).fetchall()
        for row in rows:
            db.execute("DELETE FROM conversations WHERE id=?", (row["id"],))  # messages: ON DELETE CASCADE
    summary = {}
    for row in rows:
        item = summary.setdefault((row["user_id"], row["reason"]), {"user_id": row["user_id"], "reason": row["reason"],
                                                                    "conversations": 0, "messages": 0})
        item["conversations"] += 1
        item["messages"] += row["message_count"]
    return list(summary.values())


def purge_and_audit(now=None):
    """purge(), then one audit entry per user and reason (system). Totals (conversations, messages)."""
    from backend.app.pairing.store import transaction as device_transaction
    from backend.app.users import audit
    groups = purge(now)
    if groups:
        try:
            with device_transaction() as db:
                for group in groups:
                    audit.record(db, "conversation.purge", actor_kind="system", target_type="user",
                                 target_id=group["user_id"],
                                 detail={"reason": group["reason"], "conversations": group["conversations"],
                                         "messages": group["messages"]}, now=now)
        except Exception:  # noqa: BLE001 - the purge already happened; keep it and say so in the log
            logger.exception("Audit write failed action=conversation.purge")
    totals = (sum(g["conversations"] for g in groups), sum(g["messages"] for g in groups))
    if groups:
        logger.info("history_purge conversations=%d messages=%d", *totals)
    return totals


async def run_purges(interval=PURGE_SECONDS):
    """Background task of the backend: at startup and every 6 hours; never raises, cancelled at shutdown."""
    while True:
        try:
            await asyncio.to_thread(purge_and_audit)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - logged, retried at the next run
            logger.exception("History purge failed")
        await asyncio.sleep(interval)
