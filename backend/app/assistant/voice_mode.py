"""Modo voz: respuestas pensadas para oírse en el celular o los lentes (canal "voice").

Una respuesta general en voz es corta (1-2 frases) y termina con VOICE_OFFER; "sí" da la versión larga. Lo que no
cabe en una lectura (REST_LIMIT) se lee hasta la última frase completa y termina con REST_OFFER; "sigue" lee el resto
ya generado, sin pasar por el modelo. Los procedimientos se leen como siempre (speech.spoken_summary). Puro.
"""
import re

from backend.app.assistant.speech import REST, _clean, spoken_summary
from backend.app.knowledge.store import normalize
from backend.app.providers.ollama import TRUNCATED_NOTE

VOICE_OFFER = "¿Quieres más detalle?"
REST_OFFER = "¿Sigo?"
VOICE_MAX_TOKENS = 160
REST_LIMIT = 1200
VOICE_RULES = (
    "La respuesta se va a escuchar en unos lentes, no se va a leer. Responde en español en una o dos frases "
    "(máximo 40 palabras), con lo esencial primero. No uses listas, viñetas, markdown, enlaces, URLs ni símbolos. "
    "Escribe los números y las siglas como se dicen. No ofrezcas más detalle: eso lo hace la aplicación."
)

_YES = r"(?:si|claro|vale|ok|okay|de acuerdo|dale|por favor|si por favor|si claro|claro que si)"
_DETAIL = re.compile(_YES + r"|(?:si )?(?:explicame|dime|cuentame|dame) mas(?: detalle(?:s)?)?|mas detalles?"
                     r"|ampliar?(?:lo)?|amplia(?:lo)?|con detalle|continua(?: por favor)?")
_REST = re.compile(_YES + r"|sigue(?: leyendo)?|siguele|continua(?: leyendo)?|y luego|que mas|y despues")
_DECLINE = re.compile(r"no|no gracias|ahora no|despues|asi esta bien|asi esta bien gracias|no hace falta")
_ENDS = re.compile(r"[.!?…]\s*$")
_LAST_END = re.compile(r".*[.!?…]", re.S)
_SENTENCE = re.compile(r"(?<=[.!?…:])\s+")


def channel_for(device_id):
    """"voice" para un dispositivo emparejado (celular o lentes), "text" para todo lo demás."""
    return "voice" if device_id else "text"


def _folded(message):
    text = normalize(message or "").replace(",", " ")
    return re.sub(r"\s+", " ", text).strip(" .!?¡¿;:")


def wants_detail(message):
    return bool(_DETAIL.fullmatch(_folded(message)))


def wants_rest(message):
    return bool(_REST.fullmatch(_folded(message)))


def declines(message):
    return bool(_DECLINE.fullmatch(_folded(message)))


def trim_short(answer):
    """La respuesta corta sin la nota de recorte; si quedó a medias, hasta su última frase completa."""
    text = (answer or "").replace(TRUNCATED_NOTE, "").strip()
    if not text or _ENDS.search(text):
        return text
    whole = _LAST_END.match(text)
    if whole:
        return whole.group(0).strip()
    return text.rstrip(",;: ") + "."


def _flat(answer):
    """The answer as it is heard: clean, in one line, without the truncation note (never read aloud)."""
    return re.sub(r"\s+", " ", _clean((answer or "").replace(TRUNCATED_NOTE, ""))).strip()


def split_spoken(text, limit=REST_LIMIT):
    """(lo que se lee ahora, el resto): el texto limpio cortado en la última frase completa antes de limit."""
    flat = _flat(text)
    if len(flat) <= limit:
        return flat, ""
    head = ""
    sentences = _SENTENCE.split(flat)
    for index, sentence in enumerate(sentences):
        candidate = (head + " " + sentence).strip()
        if len(candidate) > limit:
            if not head:  # una sola frase enorme: se corta por palabra
                cut = flat[:limit].rsplit(" ", 1)[0]
                return cut, flat[len(cut):].strip()
            return head, " ".join(sentences[index:]).strip()
        head = candidate
    return head, ""


STEPS_PER_PART = 3
_STEP = re.compile(r"Paso \d+: ")


def _steps(spoken):
    """(encabezado, pasos) de un texto hablado con "Paso N: …"; ([], ) si no tiene pasos."""
    starts = [match.start() for match in _STEP.finditer(spoken or "")]
    if not starts:
        return spoken, []
    head = spoken[:starts[0]].strip()
    steps = [spoken[start:end].strip() for start, end in zip(starts, starts[1:] + [len(spoken)])]
    return head, steps


def voice_speech(answer):
    """(speech, resto) de una respuesta en canal voice. speech nunca es None cuando hay texto: con None la app
    recorta la respuesta a 450 caracteres y dice "El resto está en el chat.". Un procedimiento largo se lee de
    STEPS_PER_PART en STEPS_PER_PART pasos con "¿Sigo?": todos, en orden, sin saltarse ninguno."""
    summary = spoken_summary(answer)
    listed = summary is not None and not summary.endswith(REST)
    head, steps = _steps(summary if listed else _flat(answer))
    if len(steps) > STEPS_PER_PART:
        now = " ".join(part for part in [head, *steps[:STEPS_PER_PART]] if part)
        return f"{now} {REST_OFFER}", " ".join(steps[STEPS_PER_PART:])
    if listed:
        return summary, ""  # procedimientos cortos, listas y secciones: como siempre
    head, rest = split_spoken(answer)
    if rest:
        return f"{head} {REST_OFFER}", rest
    return head or None, ""
