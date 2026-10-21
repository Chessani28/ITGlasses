"""Lightweight deterministic social-intent detection for Knowledge Chat."""
import re

_GREETINGS = re.compile(
    r"^(?:hola+|buenos?\s*d[ií]as?|buenas?\s*tardes?|buenas?\s*noches?|hey+|hi+|hello+|saludos)\s*[!.?]*$",
    re.IGNORECASE,
)

_THANKS = re.compile(
    r"^(?:gracias|muchas?\s*gracias|te\s*agradezco|thanks|thank\s*you)\s*[!.?]*$",
    re.IGNORECASE,
)

_HOW_ARE_YOU = re.compile(
    r"^(?:c[oó]mo\s*est[aá]s?|qu[eé]\s*tal|how\s*are\s*you|c[oó]mo\s*te\s*va)\s*[!.?]*$",
    re.IGNORECASE,
)

_BYE = re.compile(
    r"^(?:adi[oó]s|hasta\s*luego|nos\s*vemos|bye|chao|hasta\s*pronto)\s*[!.?]*$",
    re.IGNORECASE,
)

_SOCIAL_RESPONSES = {
    "greeting": "¡Hola! ¿En qué puedo ayudarte? Puedes consultar procedimientos en los manuales disponibles o usar el Chat general para otros temas.",
    "thanks": "¡Con gusto! ¿Necesitas algo más?",
    "how_are_you": "¡Muy bien, gracias! ¿En qué puedo ayudarte?",
    "bye": "¡Hasta luego! Que tengas un buen día.",
}


def detect_social_intent(text: str) -> str | None:
    """Return a social intent key or None if not a pure social message."""
    text = text.strip()
    if not text:
        return None
    # Must be short to avoid overriding real questions
    if len(text) > 60:
        return None
    if _GREETINGS.match(text):
        return "greeting"
    if _THANKS.match(text):
        return "thanks"
    if _HOW_ARE_YOU.match(text):
        return "how_are_you"
    if _BYE.match(text):
        return "bye"
    return None


def social_response(intent_key: str) -> str:
    return _SOCIAL_RESPONSES.get(intent_key, "¡Hola! ¿En qué puedo ayudarte?")