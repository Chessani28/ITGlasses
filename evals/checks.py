"""Revisiones automáticas de un turno del examen. Cada una devuelve motivos de fallo; ninguna opina."""
import re

from backend.app.knowledge.store import normalize

KNOWN_CHECKS = frozenset({
    "route", "contains_all", "contains_any", "not_contains", "max_chars", "min_chars", "max_speech_chars",
    "max_sentences", "ends_with_offer", "has_sources", "source_document", "steps_verbatim", "web_used"})
OFFERS = ("¿Quieres más detalle?", "¿Sigo?")
_SENTENCES = re.compile(r"(?<=[.!?…])\s+")
_STEP = re.compile(r"^\s*\d{1,2}[.)]\s+(.+)$", re.M)


def _flat(text):
    return re.sub(r"\s+", " ", normalize(text or "")).strip()


def sentence_count(text):
    text = (text or "").strip()
    return len([part for part in _SENTENCES.split(text) if part.strip()]) if text else 0


def heard(result):
    """Lo que se oiría: speech si existe, si no la respuesta."""
    speech = result.get("speech")
    return speech if speech else (result.get("answer") or "")


def check_turn(expect, result, document_texts):
    """Motivos de fallo de un turno (lista vacía = pasó)."""
    answer = result.get("answer") or ""
    folded = _flat(answer)
    sources = result.get("sources") or []
    failures = []
    if "route" in expect and result.get("route") != expect["route"]:
        failures.append(f"route: esperaba {expect['route']}, fue {result.get('route')}")
    if "contains_all" in expect:
        missing = [word for word in expect["contains_all"] if _flat(word) not in folded]
        if missing:
            failures.append(f"contains_all: faltan {missing}")
    if "contains_any" in expect and not any(_flat(word) in folded for word in expect["contains_any"]):
        failures.append(f"contains_any: ninguna de {expect['contains_any']}")
    if "not_contains" in expect:
        present = [word for word in expect["not_contains"] if _flat(word) in folded]
        if present:
            failures.append(f"not_contains: aparece {present}")
    if "max_chars" in expect and len(answer) > expect["max_chars"]:
        failures.append(f"max_chars: {len(answer)} > {expect['max_chars']}")
    if "min_chars" in expect and len(answer) < expect["min_chars"]:
        failures.append(f"min_chars: {len(answer)} < {expect['min_chars']}")
    spoken = heard(result)
    if "max_speech_chars" in expect and len(spoken) > expect["max_speech_chars"]:
        failures.append(f"max_speech_chars: {len(spoken)} > {expect['max_speech_chars']}")
    if "max_sentences" in expect:
        answer_part = spoken.strip()
        for offer in OFFERS:  # the offer is the app's question, not a sentence of the answer
            answer_part = answer_part.removesuffix(offer).strip()
        count = sentence_count(answer_part)
        if count > expect["max_sentences"]:
            failures.append(f"max_sentences: {count} > {expect['max_sentences']}")
    if expect.get("ends_with_offer") and not spoken.strip().endswith(OFFERS):
        failures.append("ends_with_offer: no termina con una oferta")
    if expect.get("has_sources") and not sources:
        failures.append("has_sources: sin fuentes")
    if "source_document" in expect and not any(
            expect["source_document"] in (source.get("document"), source.get("title")) for source in sources):
        failures.append(f"source_document: no cita {expect['source_document']}")
    if expect.get("steps_verbatim"):
        corpus = " ".join(_flat(text) for text in document_texts.values())
        invented = [step for step in _STEP.findall(answer)
                    if _flat(step).rstrip(" .:;") not in corpus]
        if invented:
            failures.append(f"steps_verbatim: pasos que no están en el documento: {invented}")
    if expect.get("web_used") and not any(str(source.get("url", "")).startswith("http") for source in sources):
        failures.append("web_used: no usó fuentes web")
    return failures
