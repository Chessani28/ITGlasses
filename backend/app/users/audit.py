"""Audit log of administration actions: who, what, when (table audit_log of the device database).

Secrets never get in: ``detail`` is a small JSON object of scalar values whose keys may not name a password,
token, hash, salt, key, cookie or CSRF value."""
import json
import time

ACTIONS = frozenset({
    "bootstrap.superadmin",
    "user.create", "user.update", "user.disable", "user.enable", "user.unlock", "user.role",
    "user.password_reset", "user.password_change",
    "device.approve", "device.deny", "device.block", "device.unblock", "device.delete", "device.assign",
    "document.upload", "document.replace", "document.authorize", "document.withdraw", "document.delete",
    "document.reindex",
    "settings.update",
    "auth.login", "auth.logout", "auth.locked",
})
KINDS = ("user", "script", "system")
OUTCOMES = ("ok", "denied", "error")
FORBIDDEN_KEY_PARTS = ("password", "passwd", "secret", "token", "hash", "salt", "key", "cookie", "csrf")
DETAIL_MAX = 2048
TEXT_MAX = 128


def _detail_text(detail):
    if detail is None:
        return None
    if not isinstance(detail, dict):
        raise ValueError("audit detail must be a dict")
    for key, value in detail.items():
        if not isinstance(key, str) or any(part in key.lower() for part in FORBIDDEN_KEY_PARTS):
            raise ValueError(f"audit detail key not allowed: {key!r}")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("audit detail values must be scalars")
    text = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) > DETAIL_MAX:
        raise ValueError("audit detail too long")
    return text


def _short(value):
    if value is None:
        return None
    value = str(value)
    if len(value) > TEXT_MAX:
        raise ValueError("audit field too long")
    return value


def record(db, action, *, actor_kind, actor_user_id=None, target_type=None, target_id=None, detail=None,
           ip=None, outcome="ok", now=None):
    """Insert one entry in the caller's transaction (it is kept or rolled back with the change it describes)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action: {action!r}")
    if actor_kind not in KINDS or outcome not in OUTCOMES:
        raise ValueError("invalid audit actor kind or outcome")
    at = int(time.time()) if now is None else int(now)
    cursor = db.execute(
        "INSERT INTO audit_log(at,actor_user_id,actor_kind,action,target_type,target_id,detail,ip,outcome) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (at, _short(actor_user_id), actor_kind, action, _short(target_type), _short(target_id),
         _detail_text(detail), _short(ip), outcome))
    return cursor.lastrowid


def entries(db, limit=100, before_id=None, actions=None):
    """Newest entries first (with the actor's username), for the read-only audit page."""
    limit = max(1, min(int(limit), 500))
    sql = ("SELECT a.id,a.at,a.actor_user_id,u.username AS actor_username,a.actor_kind,a.action,a.target_type,"
           "a.target_id,a.detail,a.ip,a.outcome FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id")
    clauses, parameters = [], []
    if before_id is not None:
        clauses.append("a.id<?")
        parameters.append(int(before_id))
    if actions:
        clauses.append("a.action IN (" + ",".join("?" * len(actions)) + ")")
        parameters.extend(actions)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY a.id DESC LIMIT ?"
    parameters.append(limit)
    rows = []
    for row in db.execute(sql, parameters):
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else None
        rows.append(item)
    return rows
