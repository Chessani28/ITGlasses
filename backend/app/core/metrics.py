"""In-memory timing of each stage (speech to text, assistant, text to speech) for the admin monitoring card.

Codes and milliseconds only: never a question, an answer or a transcript. Bounded (MAX_EVENTS) and lost on restart:
it answers "what is failing and how slow is it now", not long-term history.
"""
import math
import re
import statistics
import threading
import time
from collections import Counter, deque

MAX_EVENTS = 500
WINDOW_SECONDS = 3600
STAGES = ("stt", "chat", "tts")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

_events = deque(maxlen=MAX_EVENTS)
_lock = threading.Lock()


def _code(value):
    """A short code (route, engine, error class) or None: free text is never kept."""
    value = None if value is None else str(value)
    return value if value and _SAFE.match(value) else None


def record(stage, ms, outcome="ok", detail=None, now=None):
    if stage not in STAGES:
        return
    event = {"stage": stage, "ms": max(0, int(ms)), "outcome": _code(outcome) or "error", "detail": _code(detail),
             "at": time.time() if now is None else now}
    with _lock:
        _events.append(event)


def reset():
    with _lock:
        _events.clear()


def _p90(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def summary(now=None, window=WINDOW_SECONDS):
    now = time.time() if now is None else now
    with _lock:
        recent = [event for event in _events if now - event["at"] <= window]
    stages = {}
    for stage in STAGES:
        mine = [event for event in recent if event["stage"] == stage]
        times = [event["ms"] for event in mine if event["outcome"] == "ok"]
        stages[stage] = {"count": len(mine), "errors": sum(event["outcome"] != "ok" for event in mine),
                         "median_ms": round(statistics.median(times)) if times else None,
                         "p90_ms": _p90(times) if times else None}
    stt = [event for event in recent if event["stage"] == "stt"]
    return {
        "window_seconds": window,
        "stages": stages,
        "stt_engines": dict(Counter(event["detail"] for event in stt if event["outcome"] == "ok" and event["detail"])),
        "whisper_failures": dict(Counter(event["outcome"] for event in stt
                                         if event["outcome"] != "ok" and event["detail"] == "whisper-small")),
        "recent_errors": [{"stage": event["stage"], "outcome": event["outcome"], "detail": event["detail"],
                           "seconds_ago": round(now - event["at"])}
                          for event in [event for event in recent if event["outcome"] != "ok"][-20:]],
    }
