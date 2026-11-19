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

# "¿Qué hago primero?" asks for step 1 even without the word "paso".
_FIRST = re.compile(
    r"\b(?:qu[eé]\s+(?:hago|debo\s+hacer|tengo\s+que\s+hacer|se\s+hace)\s+primero|"
    r"lo\s+primero\s+que\s+(?:hago|debo|tengo|se))\b",
    re.IGNORECASE,
)

# A document or procedure named in the question: "según el procedimiento de reinicio", "el manual de soporte VPN
# Phase1B", "la guía «X»". The name ends at punctuation, an interrogative or the end of the question.
_NOUNS = {"procedimiento": False, "proceso": False, "manual": False, "documento": False, "instructivo": False,
          "protocolo": False, "guía": True}
_NAMED = re.compile(
    r"(?<![\wáéíóúñ])(?P<before>\w+\s+)?(?P<noun>procedimiento|proceso|manual|documento|gu[ií]a|instructivo|protocolo)"
    r"\s+(?:(?:de|del|para|sobre)\s+(?:(?:el|la|los|las)\s+)?|llamad[oa]\s+)"
    r"«?(?P<name>[^,;:?¿!¡.«»]+?)»?"
    r"(?=\s*(?:[,;:?¿!¡.]|$|(?<![\wáéíóúñ])(?:qu[eé]|c[oó]mo|cu[aá]l(?:es)?|d[oó]nde|cu[aá]ndo|qui[eé]n(?:es)?|"
    r"a\s+qui[eé]n|paso\s+a\s+paso|por\s+favor|seg[uú]n)(?![\wáéíóúñ])))",
    re.IGNORECASE,
)
_QUOTED = re.compile(
    r"(?P<noun>procedimiento|proceso|manual|documento|gu[ií]a|instructivo|protocolo)\s+«(?P<name>[^»]{1,80})»",
    re.IGNORECASE,
)
# An English document title without a Spanish noun: "según el Server Rack Guide", "en el Training Setup Guideline".
_TITLED = re.compile(
    r"\b(?:[Ss]eg[uú]n|[Ee]n|[Dd]el?)\s+(?:(?:el|la)\s+)?"
    r"(?P<name>(?:[A-Z0-9][\w-]*\s+){1,7}(?:Guide|Guideline|Manual|Procedure|Standards?|Handbook|Policy|"
    r"GUIDE|GUIDELINE|MANUAL|PROCEDURE|STANDARDS?|HANDBOOK|POLICY))\b"
)
_ANAPHORIC_BEFORE = {"este", "ese", "esta", "esa", "aquel", "aquella", "dicho", "dicha", "mismo", "misma"}
_NOT_A_NAME = {"la empresa", "empresa", "actual", "completo", "completa", "anterior", "siguiente", "este", "ese",
               "esta", "esa", "arriba", "antes", "ahora"}
_NAME_STOPWORDS = {"de", "del", "la", "el", "los", "las", "a", "al", "y", "en", "un", "una"}
MAX_NAME_WORDS = 8


def named_target(message):
    """The document or procedure the question names, or None.

    Returns ``{"noun": "procedimiento", "name": "reinicio", "feminine": False}``. Anaphoric references ("este
    procedimiento", "el procedimiento" with nothing after it) are not names: they keep using the session.
    """
    text = (message or "").strip()
    match = _QUOTED.search(text)
    if not match:
        for candidate in _NAMED.finditer(text):
            before = (candidate.group("before") or "").strip().lower()
            if before in _ANAPHORIC_BEFORE:
                continue
            match = candidate
            break
    if match:
        noun = match.group("noun").lower().replace("guia", "guía")
        name = match.group("name").strip(" «»\"'")
        words = name.split()
        lowered = name.lower()
        if (not words or len(words) > MAX_NAME_WORDS or lowered in _NOT_A_NAME
                or all(word.lower() in _NAME_STOPWORDS for word in words)):
            return None
        return {"noun": noun, "name": name, "feminine": _NOUNS.get(noun, False)}
    titled = _TITLED.search(text)
    if titled:
        return {"noun": "documento", "name": titled.group("name").strip(), "feminine": False}
    return None


CARDINALS = dict(zip("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete dieciocho diecinueve veinte".split(), range(21)))
CARDINALS["dieciseis"] = 16

def detect_reference(message):
    text = (message or "").strip()
    lowered = text.lower().strip("¿?¡!. ")
    values = {**ORDINALS, **CARDINALS}
    words = "|".join(sorted(values, key=len, reverse=True))
    pattern = r"\bpaso\s+(?:(?:número|numero|n[°º])\s*)?(\d{1,2}|" + words + r")\b|\b(" + words + r")\s+paso\b"
    match = re.search(pattern, lowered)
    if not match and _FIRST.search(lowered):
        return {"kind": "ordinal", "number": 1, "text": text}
    if match:
        value = match.group(1) or match.group(2)
        return {"kind": "ordinal", "number": int(value) if value.isdigit() else values[value], "text": text}
    # Relative words only count in a step phrase or a short navigation utterance.
    for kind, expression in [("next", _NEXT), ("previous", _PREVIOUS), ("last", _LAST)]:
        match = expression.search(lowered)
        if match and (re.search(r"\bpaso\b", lowered) or re.fullmatch(
                r"(?:(?:y|el|la|al|explicame|explícame|dime|vamos|ahora)\s+)*" + re.escape(match.group()) , lowered)):
            return {"kind": kind, "number": None, "text": text}
    if _ALL_STEPS.search(lowered) or re.search(r"\b(?:todos los pasos|los pasos)\b", lowered):
        return {"kind": "steps", "number": None, "text": text}
    if _DOCUMENT.search(lowered):
        return {"kind": "document", "number": None, "text": text}
    if _PROCEDURE.search(lowered):
        return {"kind": "procedure", "number": None, "text": text}
    return None
