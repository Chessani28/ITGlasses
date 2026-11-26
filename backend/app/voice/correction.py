"""Post-transcription correction against known titles and headings (Voz 2).

Only spans of two or more words are replaced, only by a title or procedure heading that exists in the
vocabulary, and only when the match is clearly better than any other candidate. Nothing is ever added outside the
replaced span. The original transcript stays in ``raw_text``; logs carry the chosen term and score, never the
transcript. Standard library only.

Thresholds are provisional until tuned with real recordings:
* anchored span (after "según el manual de…", "en el documento…", "de la guía…"): similarity >= 0.55;
* anywhere else: similarity >= 0.90;
* in both cases the best candidate must beat the second best by >= 0.10.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import asdict, dataclass

from .vocabulary import Vocabulary

ANCHORED_MIN = 0.55
UNANCHORED_MIN = 0.90
MARGIN = 0.10
MAX_SPAN_WORDS = 8

_ANCHOR = re.compile(
    r"\b(?:seg[uú]n|en|de|del|desde|con)\s+(?:(?:el|la|los|las|este|esta|mi|mis|un|una)\s+)?"
    r"(?P<noun>manual(?:es)?|documentos?|gu[ií]as?|procedimientos?)\b(?:\s+(?:de|del)\b)?\s*",
    re.IGNORECASE,
)
# Where an anchored span ends: an interrogative or a question mark.
_SPAN_END = re.compile(r"[?¿!¡;]|\b(?:cu[aá]l(?:es)?|qu[eé]|c[oó]mo|d[oó]nde|cu[aá]ndo|cu[aá]nto|qui[eé]n)\b",
                       re.IGNORECASE)
_TOKEN = re.compile(r"\S+")
_TRAILING = ",.;:?!¿¡\"'»«)"

_UNITS = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis "
          "diecisiete dieciocho diecinueve veinte").split()
_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}

# How an English term sounds to a Spanish-speaking recognizer, applied to both sides (order matters).
_SOUNDS = (
    (r"tion\b", "shon"), (r"ph", "f"), (r"th", "t"), (r"ck", "k"), (r"qu", "k"), (r"gui", "gi"), (r"gue", "ge"),
    (r"ll", "y"), (r"sh", "s"), (r"ch", "s"), (r"ai", "ei"), (r"ui", "ai"), (r"ee", "i"), (r"oo", "u"),
    (r"w", "u"), (r"y", "i"), (r"z", "s"), (r"v", "b"), (r"c(?=[ei])", "s"), (r"c", "k"), (r"h", ""),
    (r"(\w)\1", r"\1"), (r"e\b", ""),
)


def _number_words(token: str) -> str:
    if not token.isdigit() or len(token) > 2:
        return token
    value = int(token)
    if value <= 20:
        return _UNITS[value]
    if value < 30:
        return "veinti" + _UNITS[value - 20]
    tens, units = divmod(value, 10)
    return _TENS[tens] + ("" if units == 0 else " y " + _UNITS[units])


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def sounds(text: str) -> str:
    """Rough Spanish-ear rendering, numbers spelled out: '30 setos' -> 'treinta setos'."""
    words = [_number_words(w) for w in _fold(text).split()]
    result = " ".join(words)
    for pattern, replacement in _SOUNDS:
        result = re.sub(pattern, replacement, result)
    return " ".join(result.split())


def similarity(spoken: str, term: str) -> float:
    """Best of plain-letter and sound similarity, 0..1."""
    plain = difflib.SequenceMatcher(None, _fold(spoken), _fold(term)).ratio()
    heard = difflib.SequenceMatcher(None, sounds(spoken), sounds(term)).ratio()
    return max(plain, heard)


@dataclass(frozen=True)
class Correction:
    original: str
    replacement: str
    score: float
    anchored: bool

    def log_fields(self) -> dict:
        """What may be logged: the known term and the score, never the transcript."""
        return {"replacement": self.replacement, "score": round(self.score, 2), "anchored": self.anchored}


def _windows(tokens, start, stop, words):
    """(first, last) token index ranges of sizes words-1 .. words+2 starting at ``start`` (anchored)."""
    for size in range(max(1, words - 1), words + 3):
        if start + size <= stop:
            yield start, start + size


def _sliding(tokens, words):
    for size in range(max(2, words - 1), words + 2):
        for first in range(0, len(tokens) - size + 1):
            yield first, first + size


def _span_text(text, tokens, first, last):
    begin, end = tokens[first].start(), tokens[last - 1].end()
    while end > begin and text[end - 1] in _TRAILING:
        end -= 1
    return begin, end, text[begin:end]


def _best(text, tokens, ranges_for, candidates):
    """Best candidate and its window, plus the best score among the other candidates."""
    scored = []
    for term in candidates:
        best = None
        for first, last in ranges_for(term):
            begin, end, spoken = _span_text(text, tokens, first, last)
            if not spoken:
                continue
            score = similarity(spoken, term.text)
            if best is None or score > best[0]:
                best = (score, begin, end, spoken, term)
        if best:
            scored.append(best)
    if not scored:
        return None, 0.0
    scored.sort(key=lambda item: item[0], reverse=True)
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    return scored[0], runner_up


def _best_scored(scores_for, candidates):
    """Like _best, for generators that yield (score, begin, end, term) themselves."""
    scored = []
    for term in candidates:
        best = max(scores_for(term), key=lambda item: item[0], default=None)
        if best:
            scored.append(best)
    if not scored:
        return None, 0.0
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0], (scored[1][0] if len(scored) > 1 else 0.0)


PRODUCT = "ITGlasses"
# The product name right after a greeting, as recognizers heard it in real recordings ("Oye, ¿y qué clases?",
# "porque y clases", "it glasses"). Only there: "¿qué clases de VPN hay?" is ordinary speech.
_PRODUCT = re.compile(
    r"^(?P<greeting>\W*(?:oye|hola|ok|okay|ey|hey|porque)\b[\s,]*)"
    r"(?P<heard>¿?\s*(?:y\s+qu[eé]\s+clases|y\s+clases|i\s*t\s*glas+es|y\s+glas+es|itglasses)\s*\??)(?=\s|,|$)",
    re.IGNORECASE)


def _product_name(text: str) -> tuple[str, list[Correction]]:
    match = _PRODUCT.match(text or "")
    if not match:
        return text, []
    heard = match.group("heard")
    if heard.strip() == PRODUCT:
        return text, []
    greeting = match.group("greeting")
    if _fold(greeting).strip(" ,") == "porque":  # Vosk heard "Oye" as "porque"
        greeting = "Oye "
    fixed = greeting + PRODUCT + text[match.end():]
    return fixed, [Correction(heard.strip(), PRODUCT, 1.0, True)]


# A fast "Qué es…" at the very start, heard as an acronym in real recordings ("AS Active Directory", "CES, una
# dirección IP"). Only at the start, only these non-words ("As" in title case is the Spanish "ace": left alone).
_QUE_ES = re.compile(r"^(?P<lead>[\s¿]*)(?:AS|[Kk][Ss]|[Kk][Ee][Ss]|[Cc][Ee][Ss])\b[,.]?\s+(?P<rest>\w.*)$", re.S)
_ARTICLES = {"un", "una", "el", "la", "los", "las"}


def _que_es(text: str) -> tuple[str, list[Correction]]:
    match = _QUE_ES.match(text or "")
    if not match:
        return text, []
    rest = match.group("rest")
    first, _, tail = rest.partition(" ")
    if first.casefold() in _ARTICLES:
        rest = first.casefold() + (" " + tail if tail else "")
    heard = text[:match.start("rest")].strip()
    return "¿Qué es " + rest, [Correction(heard, "¿Qué es", 1.0, True)]


def correct(text: str, vocabulary: Vocabulary) -> tuple[str, list[Correction]]:
    """Corrected text and the corrections made (empty list: text unchanged)."""
    text, named = _product_name(text)
    text, asked = _que_es(text)
    named = named + asked
    candidates = vocabulary.candidates
    if not text or not candidates:
        return text, named
    rest, made = _correct_titles(text, candidates)
    return rest, named + made


def _correct_titles(text, candidates):
    """Known titles and headings (anchored after "según el manual de…", or near-exact anywhere)."""
    corrections: list[tuple[int, int, Correction]] = []

    def taken(begin, end):
        return any(begin < e and b < end for b, e, _ in corrections)

    tokens = list(_TOKEN.finditer(text))
    # 1. Anchored spans: "según el manual de <span>", up to an interrogative or MAX_SPAN_WORDS.
    for anchor in _ANCHOR.finditer(text):
        noun_start, span_start = anchor.start("noun"), anchor.end()
        stop_match = _SPAN_END.search(text, span_start)
        span_end = stop_match.start() if stop_match else len(text)
        first = next((i for i, t in enumerate(tokens) if t.start() >= span_start), None)
        noun = next((i for i, t in enumerate(tokens) if t.start() >= noun_start), None)
        if first is None or tokens[first].start() >= span_end:
            continue
        stop = sum(1 for t in tokens if t.start() < span_end)
        stop = min(stop, first + MAX_SPAN_WORDS)

        noun_text = _fold(text[noun_start:span_start])  # "manual de", "procedimiento de"…

        def anchored(term, first=first, stop=stop):
            """Compare the span with the term; a term that starts with the anchor ("Procedimiento de
            Reinicio") is compared without that shared start, so "manual de" never inflates the score."""
            folded = _fold(term.text)
            shared = folded.startswith(noun_text + " ") and len(folded) > len(noun_text) + 1
            rest = term.text.split()[len(noun_text.split()):] if shared else term.text.split()
            for f, l in _windows(tokens, first, stop, max(1, len(rest))):
                begin, end, spoken = _span_text(text, tokens, f, l)
                if spoken:
                    score = similarity(spoken, " ".join(rest))
                    yield score, (noun_start if shared else begin), end, term

        best, runner_up = _best_scored(anchored, candidates)
        if best and best[0] >= ANCHORED_MIN and best[0] - runner_up >= MARGIN and not taken(best[1], best[2]):
            score, begin, end, term = best
            spoken = text[begin:end]
            corrections.append((begin, end, Correction(spoken, term.text if _fold(spoken) != _fold(term.text)
                                                       else spoken, score, True)))
    # 2. Anywhere else: only near-exact multi-word matches.
    best, runner_up = _best(text, tokens, lambda term: _sliding(tokens, term.words), candidates)
    while best and best[0] >= UNANCHORED_MIN and best[0] - runner_up >= MARGIN:
        score, begin, end, spoken, term = best
        if taken(begin, end):
            break
        corrections.append((begin, end, Correction(spoken, term.text if _fold(spoken) != _fold(term.text) else spoken,
                                                   score, False)))
        remaining = [c for c in candidates if c is not term]
        best, runner_up = _best(text, tokens, lambda t: _sliding(tokens, t.words), remaining)

    made = [(b, e, c) for b, e, c in corrections if c.replacement != c.original]
    corrected = text
    for begin, end, item in sorted(made, key=lambda entry: entry[0], reverse=True):
        corrected = corrected[:begin] + item.replacement + corrected[end:]
    return corrected, [c for _, _, c in sorted(made, key=lambda entry: entry[0])]


def apply_to_result(result: dict, vocabulary: Vocabulary) -> dict:
    """STT result with ``text`` corrected, ``raw_text`` original and ``corrections`` (log-safe fields)."""
    original = result.get("text") or ""
    corrected, made = correct(original, vocabulary)
    updated = dict(result)
    updated.setdefault("raw_text", original)
    updated["text"] = corrected
    updated["corrections"] = [asdict(c) for c in made]
    return updated
