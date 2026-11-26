"""👍/👎 on the assistant's answers (2026-11-26): what the technicians found useful, to measure and to improve.

Privacy:
  👍      only counted: nothing of the conversation is kept with it.
  👎      a copy of that question and answer and the optional comment, for the admins: the user sends it knowing it
          is shared ("Al enviarlo, esta pregunta y respuesta se comparten con el administrador…"). Secrets in them
          (passwords, tokens) are hidden like in the manuals.
  admins  see the counts and the recent 👎, never who sent them.
A deleted or purged conversation takes its feedback with it (ON DELETE CASCADE); a deleted one stops being listed at
once.
"""
import time

from . import store

COMMENT_MAX = 500
COPY_MAX = 4000
RECENT_MAX = 50


class NotFound(LookupError):
    """Not the user's conversation, not an answer of it, or deleted: the same answer for all of them."""


def _redact(text):
    from backend.app.knowledge.redaction import redact
    return redact(text or "")[0]


def give(user_id, conversation_id, message_id, value, comment=None, now=None):
    """Record (value 1 or -1), change or remove (value 0) the user's vote on an answer of their conversation."""
    if value not in (-1, 0, 1):
        raise ValueError("value")
    now = int(time.time()) if now is None else int(now)
    with store.transaction() as db:
        answer = db.execute(
            "SELECT m.id, m.content, m.route, m.source FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.id=? AND m.conversation_id=? AND m.role='assistant' AND c.user_id=? AND c.deleted_at IS NULL",
            (message_id, conversation_id, user_id)).fetchone()
        if answer is None:
            raise NotFound(message_id)
        if value == 0:
            db.execute("DELETE FROM feedback WHERE message_id=?", (message_id,))
            return None
        question = comment_text = answer_text = None
        if value == -1:
            asked = db.execute("SELECT content FROM messages WHERE conversation_id=? AND role='user' AND id<? "
                               "ORDER BY id DESC LIMIT 1", (conversation_id, message_id)).fetchone()
            question = _redact(asked["content"] if asked else "")[:COPY_MAX]
            answer_text = _redact(answer["content"])[:COPY_MAX]
            comment_text = _redact((comment or "").strip())[:COMMENT_MAX] or None
        db.execute("INSERT INTO feedback(message_id,conversation_id,user_id,value,comment,question,answer,route,source,"
                   "created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET value=excluded.value, "
                   "comment=excluded.comment, question=excluded.question, answer=excluded.answer, "
                   "created_at=excluded.created_at",
                   (message_id, conversation_id, user_id, value, comment_text, question, answer_text, answer["route"],
                    answer["source"], now))
        return value


def summarize(values):
    """{up, down, total, useful_percent} of a list of votes (useful_percent None without votes)."""
    up = sum(1 for value in values if value == 1)
    down = sum(1 for value in values if value == -1)
    total = up + down
    return {"up": up, "down": down, "total": total, "useful_percent": round(100 * up / total) if total else None}


def report(limit=RECENT_MAX):
    """The admins' view: the summary (all and per route) and the recent 👎 without who sent them."""
    if not store.database_path().is_file():
        return {"summary": summarize([]), "by_route": {}, "recent_down": []}
    with store.transaction() as db:
        rows = db.execute("SELECT f.value, f.route FROM feedback f JOIN conversations c ON c.id = f.conversation_id "
                          "WHERE c.deleted_at IS NULL").fetchall()
        recent = db.execute(
            "SELECT f.question, f.answer, f.comment, f.route, f.source, f.created_at FROM feedback f "
            "JOIN conversations c ON c.id = f.conversation_id WHERE f.value=-1 AND c.deleted_at IS NULL "
            "ORDER BY f.created_at DESC, f.message_id DESC LIMIT ?", (limit,)).fetchall()
    routes = {}
    for row in rows:
        routes.setdefault(row["route"] or "otro", []).append(row["value"])
    return {"summary": summarize([row["value"] for row in rows]),
            "by_route": {route: summarize(values) for route, values in sorted(routes.items())},
            "recent_down": [dict(row) for row in recent]}


def values_of(db, message_ids):
    """{message_id: vote} of the given messages (for the conversation the user opens)."""
    ids = list(message_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {row["message_id"]: row["value"]
            for row in db.execute(f"SELECT message_id, value FROM feedback WHERE message_id IN ({marks})", ids)}
