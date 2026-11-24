"""Deterministic detection of conversational references to steps and documents.

Reference resolution never calls a model: it maps a Spanish utterance to a
canonical reference kind so the assistant can answer from session state.
"""
import re

ORDINALS = {
    "primer": 1, "primero": 1, "primera": 1,
    "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5,
    "sexto": 6, "sexta": 6,
    "septimo": 7, "séptimo": 7, "septima": 7, "séptima": 7,
    "octavo": 8, "octava": 8,
    "noveno": 9, "novena": 9,
    "decimo": 10, "décimo": 10, "decima": 10, "décima": 10,
    "once": 11, "undecimo": 11, "undécimo": 11, "undecima": 11, "undécima": 11,
    "duodecimo": 12, "duodécimo": 12, "duodecima": 12, "duodécima": 12,
}

_DOCUMENT = re.compile(
    r"\b(?:en\s+qu[eé]\s+documento|en\s+que\s+documento|de\s+qu[eé]\s+documento|"
    r"d[oó]nde\s+aparece|d[oó]nde\s+est[aá]|qu[eé]\s+documento|"
    r"documento\s+actual|este\s+documento|fuente\s+documental)\b",
    re.IGNORECASE,
)

_PROCEDURE = re.compile(
    r"\b(?:este\s+procedimiento|el\s+procedimiento|procedimiento\s+actual|"
    r"este\s+proceso|el\s+proceso|[A-Z][A-Z\s]+PROCEDURE)\b",
    re.IGNORECASE,
)

_NEXT = re.compile(r"\b(?:siguiente|seguiente|pr[oó]ximo|continua|contin[uú]a|sigue)\b", re.IGNORECASE)
_PREVIOUS = re.compile(r"\b(?:anterior|precedente|previo|retroceder|atras|atr[aá]s)\b", re.IGNORECASE)
_LAST = re.compile(r"\b(?:[uú]ltimo|ultimo|final)\b", re.IGNORECASE)

_NUMBERED_STEP = re.compile(r"\bpaso\s*(?:n[°º]\s*)?(\d{1,2})\b", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"\b(?:n[°º]\s*)?(\d{1,2})\b")
_ALL_STEPS = re.compile(
    r"\b(?:los\s+\w*\s+pasos|todos\s+los\s+\w*\s+pasos|pasos\s+del?\s+procedimiento|"
    r"pasos\s+para|instrucciones|el\s+procedimiento\s+completo|procedimiento\s+completo"
    r"|pasos\s+de\s+[A-Z][A-Z\s]+"
    r"|(?:esos|esas|aquellos|aquellas)\s+\w*\s+pasos)\b",
    re.IGNORECASE,
)

_ORDINAL_WORDS = sorted(ORDINALS, key=len, reverse=True)


def _ordinal_near_step(text):
    """Find an ordinal word used as a step reference (``el cuarto paso``)."""
    for match in re.finditer(r"\bpaso\b", text, re.IGNORECASE):
        window = text[max(0, match.start() - 40):match.end() + 40]
        for word in _ORDINAL_WORDS:
            if re.search(r"\b" + word + r"\b", window, re.IGNORECASE):
                return ORDINALS[word]
    return None


def _bare_ordinal(text):
    """Find a standalone ordinal word (``explícame el cuarto``)."""
    for word in _ORDINAL_WORDS:
        if re.search(r"\b" + word + r"\b", text, re.IGNORECASE):
            return ORDINALS[word]
    return None


def detect_reference(message):
    """Return a reference descriptor for ``message`` or ``None``.

    The descriptor has ``kind`` (one of ``document``, ``procedure``, ``ordinal``,
    ``next``, ``previous``, ``last``, ``steps``) and, for ``ordinal``, the
    resolved step ``number``.
    """
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()

    if _DOCUMENT.search(lowered):
        return {"kind": "document", "number": None, "text": text}
    if _PROCEDURE.search(lowered):
        return {"kind": "procedure", "number": None, "text": text}

    numbered = _NUMBERED_STEP.search(lowered)
    if numbered:
        return {"kind": "ordinal", "number": int(numbered.group(1)), "text": text}

    ordinal = _ordinal_near_step(lowered)
    if ordinal is None and re.search(r"\bpaso\b", lowered):
        ordinal = _bare_ordinal(lowered)
    if ordinal is not None:
        return {"kind": "ordinal", "number": ordinal, "text": text}

    if _NEXT.search(lowered):
        return {"kind": "next", "number": None, "text": text}
    if _PREVIOUS.search(lowered):
        return {"kind": "previous", "number": None, "text": text}
    if _LAST.search(lowered):
        return {"kind": "last", "number": None, "text": text}
    if _ALL_STEPS.search(lowered):
        return {"kind": "steps", "number": None, "text": text}

    bare = _BARE_NUMBER.search(lowered)
    if bare and re.search(r"\b(?:paso|step)\b", lowered):
        return {"kind": "ordinal", "number": int(bare.group(1)), "text": text}
    return None
