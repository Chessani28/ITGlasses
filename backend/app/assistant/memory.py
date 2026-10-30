"""Per-session conversation state for the unified assistant.

State is kept in the assistant layer (never in the retrieval layer) and is
strictly isolated per ``session_id`` so that two conversations can never see
each other's procedures, current step or citations.
"""
import time
import uuid

MAX_SESSIONS = 200
SESSION_TTL_SECONDS = 1800
MAX_HISTORY = 20


class SessionStore:
    """Bounded, TTL-expiring store of conversation states."""

    def __init__(self, ttl=SESSION_TTL_SECONDS, max_sessions=MAX_SESSIONS):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._sessions = {}

    def _expire(self, now):
        stale = [key for key, value in self._sessions.items() if now - value["time"] > self.ttl]
        for key in stale:
            self._sessions.pop(key, None)

    def get(self, session_id):
        """Return the state for ``session_id``, creating an isolated one if needed."""
        now = time.monotonic()
        self._expire(now)
        session_id = session_id or str(uuid.uuid4())
        state = self._sessions.get(session_id)
        if state is None:
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions, key=lambda key: self._sessions[key]["time"])
                self._sessions.pop(oldest, None)
            state = {
                "time": now,
                "history": [],
                "active_route": None,
                "active_document": None,
                "active_document_id": None,
                "active_procedure": None,
                "procedures": [],
                "active_steps": [],
                "current_step_index": 0,
                "knowledge_conversation_id": None,
                "active_sources": [],
            }
            self._sessions[session_id] = state
        state["time"] = now
        return session_id, state

    def remember(self, state, message, answer):
        state["history"] = (state.get("history") or []) + [(message, answer)]
        state["history"] = state["history"][-MAX_HISTORY:]

    def clear_procedure(self, state):
        state["document_steps"] = []
        state["procedures"] = []
        state["active_steps"] = []
        state["current_step_index"] = 0
        state["active_procedure"] = None
