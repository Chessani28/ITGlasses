"""Audit entries for actions done through the API (stage 5): who (the session user, or how the request came in),
what and when. An audit write never undoes or blocks the action it describes: a failure is logged."""
import logging

from backend.app.pairing.routes import peer
from backend.app.pairing.store import transaction
from . import audit

logger = logging.getLogger(__name__)


def actor_of(request):
    """(actor_user_id, via): the login session user, else how require_auth let the request in."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return user_id, None
    return None, getattr(request.state, "auth_kind", None) or "local"


def record(db, request, action, target_type=None, target_id=None, detail=None, outcome="ok"):
    """Inside the caller's transaction (kept or rolled back with the change)."""
    user_id, via = actor_of(request)
    detail = dict(detail or {})
    if via:
        detail["via"] = via
    audit.record(db, action, actor_kind="user", actor_user_id=user_id, target_type=target_type,
                 target_id=target_id, detail=detail or None, ip=peer(request), outcome=outcome)


def record_after(request, action, target_type=None, target_id=None, detail=None):
    """In its own transaction, after an action done elsewhere (e.g. the knowledge base)."""
    try:
        with transaction() as db:
            record(db, request, action, target_type, target_id, detail)
    except Exception:  # noqa: BLE001 - the action already happened; keep it and say so in the log
        logger.exception("Audit write failed action=%s", action)
