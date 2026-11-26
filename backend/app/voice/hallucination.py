"""Detect a Whisper transcript that is a hallucination rather than what was said.

Measured with real recordings: with the vocabulary hint, Whisper Small on the CPU answered "Para qué sirve DHCP"
with "Procedimiento de emergencia para VPN" repeated ~25 times. Two signs, either is enough:
* a loop: some run of 3+ words appears 3 or more times;
* more text than a person can say in the audio (over MAX_CHARS_PER_SECOND; normal speech is ~12-18).
Pure; never logs the text.
"""
import re
from collections import Counter

MAX_CHARS_PER_SECOND = 30
LOOP_WORDS = 3
LOOP_REPEATS = 3


def looks_hallucinated(text, seconds):
    words = re.findall(r"\w+", (text or "").casefold())
    if not words:
        return False
    # At least 3 s of allowance: a short clip may hold a whole short sentence (and a loop is caught below anyway).
    if len(text) > MAX_CHARS_PER_SECOND * max(seconds, 3.0):
        return True
    runs = Counter(tuple(words[i:i + LOOP_WORDS]) for i in range(len(words) - LOOP_WORDS + 1))
    return bool(runs) and max(runs.values()) >= LOOP_REPEATS
