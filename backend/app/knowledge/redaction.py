"""Secrets inside manuals (tokens, keys, passwords) never leave the index: not to the model, the chat or the voice.

Applied when a manual is uploaded (the index never stores them) and when chunks are read (manuals indexed before
this existed). Deliberately conservative about what it touches: a value assigned to a secret-looking key, a long
key-like string (32+ characters of letters AND digits, no dots, slashes of paths or underscores of file names), and
the base32 padding left over when such a key wraps to the next line. Ordinary manual text is never changed.
"""
import re

HIDDEN = "[OCULTO]"

_KEYS = (r"access[_ -]?key(?:[_ -]?(?:id|secret))?|secret(?:[_ -]?key)?|client[_ -]?secret|api[_ -]?key"
         r"|password|passwd|pwd|contrase(?:ñ|n)a|token|key")
# "KEY = value" / "Token:\n\nvalue": the key stays, the value goes.
_ASSIGNED = re.compile(rf"(?i)\b({_KEYS})(\s*[:=]\s*)([^\s]+)")
# A long key-like string: letters and digits (base64 may add + / and = padding), 32 or more.
_LONG = re.compile(r"(?<![\w.\\/:-])(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[A-Za-z])[A-Za-z0-9+/]{32,}={0,4}(?![\w.\\/-])")
# What a wrapped base32 key leaves on the next line ("UA====").
_PADDING = re.compile(r"(?m)^[A-Z2-7]{1,8}={2,6}(?=\s|$)")


def redact(text):
    """(text without secrets, how many were hidden)."""
    if not text:
        return text, 0
    count = 0

    def assigned(match):
        nonlocal count
        if match.group(3) == HIDDEN:
            return match.group(0)
        count += 1
        return match.group(1) + match.group(2) + HIDDEN

    text = _ASSIGNED.sub(assigned, text)
    text, long_count = _LONG.subn(HIDDEN, text)
    text, padding_count = _PADDING.subn(HIDDEN, text)
    return text, count + long_count + padding_count


def redact_row(row):
    """A chunk row with its text redacted (other fields untouched)."""
    if row.get("text"):
        row["text"] = redact(row["text"])[0]
    return row
