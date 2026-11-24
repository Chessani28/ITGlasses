"""How a general answer sees the conversation (stage "historial" 3, E4 of the audit).

turns   (default) the previous general exchanges go to the model as real user/assistant turns of /api/chat,
        newest first until the context budget is spent, each clipped
prompt  the previous behaviour: the exchanges pasted as text into the prompt (rollback, ITGLASSES_CHAT_CONTEXT)

Budget: num_ctx (2048) minus the answer's tokens (768) minus a safety margin, counted at a conservative
3 characters per token for Spanish; the system prompt and the current prompt are paid for first.
"""
import os

VARIABLE, MODES, DEFAULT = "ITGLASSES_CHAT_CONTEXT", ("turns", "prompt"), "turns"
CHARS_PER_TOKEN = 3
SAFETY_TOKENS = 128
QUESTION_MAX, ANSWER_MAX = 400, 600
MAX_TURNS = 6


class ContextConfigError(ValueError):
    code = "chat_context_config"


def mode():
    raw = (os.environ.get(VARIABLE) or "").strip().lower()
    if not raw:
        return DEFAULT
    if raw not in MODES:
        raise ContextConfigError(f"{VARIABLE} debe ser turns o prompt (valor actual: {raw[:20]!r}).")
    return raw


def clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def fit_turns(turns, context_tokens, output_tokens, fixed_text=""):
    """The most recent (question, answer) turns that fit, oldest first, each clipped. Stops at the first turn that
    does not fit, so the model never sees a gap in the middle of the conversation."""
    budget = (context_tokens - output_tokens - SAFETY_TOKENS) * CHARS_PER_TOKEN - len(fixed_text or "")
    chosen = []
    for entry in reversed(list(turns)[-MAX_TURNS:]):
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            continue
        question, answer = clip(entry[0], QUESTION_MAX), clip(entry[1], ANSWER_MAX)
        cost = len(question) + len(answer)
        if not question or not answer or cost > budget:
            break
        budget -= cost
        chosen.append((question, answer))
    return list(reversed(chosen))
