"""Modo voz en el asistente: respuesta general corta con oferta de detalle, "¿Sigo?" para lo largo, y el canal
text (web) exactamente como antes."""
import asyncio
import json

from backend.app.assistant.service import GENERAL_OFFER, GENERAL_TIMEOUT
from backend.app.assistant.speech import REST, spoken_summary
from backend.app.assistant.voice_mode import REST_OFFER, VOICE_MAX_TOKENS, VOICE_OFFER, VOICE_RULES
from backend.app.history.conversations import snapshot
from backend.app.providers.base import ProviderTimeoutError
from test_assistant_service import StubKnowledge, StubProvider, build

NO_EVIDENCE = dict(answer="No encontré evidencia suficiente.", status="insufficient_evidence")
LONG = " ".join(f"Esta es la frase {n} de una explicación larga sobre el DNS." for n in range(60))


class RecordingGeneralProvider:
    """Proveedor con generate_general: registra cada prompt y su límite; corta si se le pide voz."""

    def __init__(self, short="El DNS traduce nombres de dominio a direcciones IP.", long=LONG, error=None):
        self.short, self.long, self.error = short, long, error
        self.calls = []

    async def generate(self, message):
        return await self.generate_general(message)

    async def generate_general(self, message, num_predict=None):
        self.calls.append({"prompt": message, "num_predict": num_predict})
        if self.error:
            raise self.error
        return self.short if num_predict == VOICE_MAX_TOKENS else self.long


def voice(service, message, session_id=None):
    return asyncio.run(service.chat(message, session_id=session_id, channel="voice"))


def text(service, message, session_id=None):
    return asyncio.run(service.chat(message, session_id=session_id))


def general_service(provider=None):
    provider = provider or RecordingGeneralProvider()
    return build(provider=provider, knowledge=StubKnowledge(**NO_EVIDENCE)), provider


def test_voice_general_answer_is_short_and_offers_detail():
    service, provider = general_service()
    result = voice(service, "¿Qué es el DNS?")
    assert result["route"] == "general"
    assert VOICE_RULES in provider.calls[-1]["prompt"]
    assert provider.calls[-1]["num_predict"] == VOICE_MAX_TOKENS
    assert result["answer"].endswith("El DNS traduce nombres de dominio a direcciones IP.\n\n" + VOICE_OFFER)
    assert "speech" in result


def test_text_channel_is_unchanged():
    service, provider = general_service()
    result = text(service, "¿Qué es el DNS?")
    assert VOICE_RULES not in provider.calls[-1]["prompt"]
    assert provider.calls[-1]["num_predict"] is None
    assert VOICE_OFFER not in result["answer"]
    assert "speech" not in result


def test_yes_after_the_offer_gives_the_long_version():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    second = voice(service, "sí", first["session_id"])
    assert second["route"] == "general"
    prompt = provider.calls[-1]["prompt"]
    assert VOICE_RULES not in prompt and "¿Qué es el DNS?" in prompt
    assert provider.calls[-1]["num_predict"] is None
    assert VOICE_OFFER not in second["answer"]
    assert "frase 0" in second["answer"]


def test_the_detail_expands_what_was_heard_instead_of_repeating_it():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    voice(service, "sí", first["session_id"])
    prompt = provider.calls[-1]["prompt"]
    assert "El DNS traduce nombres de dominio a direcciones IP." in prompt
    assert "no repitas" in prompt.lower() and "más detalle" in prompt.lower()


def test_no_after_the_offer_closes_it():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    closed = voice(service, "no, gracias", first["session_id"])
    assert closed["answer"] == "De acuerdo."
    calls = len(provider.calls)
    voice(service, "sí", first["session_id"])
    assert all(VOICE_RULES in call["prompt"] or "¿Qué es el DNS?" not in call["prompt"]
               for call in provider.calls[calls:])


def test_another_message_drops_the_offer():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    voice(service, "¿Qué es un firewall?", first["session_id"])
    _, state = service.sessions.get(first["session_id"])
    assert state["pending_detail"]["question"] == "¿Qué es un firewall?"


def test_long_voice_answer_offers_to_continue_and_reads_the_stored_rest():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    detail = voice(service, "sí", first["session_id"])
    assert detail["speech"].endswith(REST_OFFER)
    assert REST not in detail["speech"]
    calls = len(provider.calls)
    heard = [detail["speech"]]
    for _ in range(10):  # the rest is read in parts too, each one ending with "¿Sigo?" until the last
        more = voice(service, "sigue", first["session_id"])
        assert more["route"] == "general"
        heard.append(more["speech"] or more["answer"])
        if not heard[-1].endswith(REST_OFFER):
            break
    assert len(provider.calls) == calls
    spoken = " ".join(part.removesuffix(REST_OFFER) for part in heard)
    assert spoken.split()[-3:] == LONG.split()[-3:]
    assert all(len(part) <= 1200 + len(REST_OFFER) + 1 for part in heard)


def test_voice_procedure_is_read_as_today():
    service = build()
    result = voice(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    assert result["route"] == "knowledge"
    assert result["speech"] == spoken_summary(result["answer"])
    assert VOICE_OFFER not in result["answer"]


def test_failure_messages_get_no_offer():
    service, _ = general_service(RecordingGeneralProvider(error=ProviderTimeoutError()))
    result = voice(service, "¿Qué es el DNS?")
    assert result["answer"] == GENERAL_TIMEOUT
    _, state = service.sessions.get(result["session_id"])
    assert not state.get("pending_detail")


def test_only_one_offer_is_active():
    refusal = "No tengo acceso a la documentación de tu empresa."
    provider = RecordingGeneralProvider(short=refusal, long=refusal)
    service = build(provider=provider, knowledge=StubKnowledge(**NO_EVIDENCE))
    first = voice(service, "¿Cómo configuro la impresora de la oficina?")
    _, state = service.sessions.get(first["session_id"])
    assert first["answer"] == GENERAL_OFFER
    assert state.get("pending_general") and not state.get("pending_detail")
    voice(service, "sí", first["session_id"])  # answers the pending_general offer, as today
    assert not state.get("pending_general")


def test_the_saved_state_has_no_transient_channel():
    service, _ = general_service()
    result = voice(service, "¿Qué es el DNS?")
    _, state = service.sessions.get(result["session_id"])
    saved = json.loads(snapshot(state))
    assert "_channel" not in saved and "_voice_short" not in saved
    assert saved["pending_detail"]["question"] == "¿Qué es el DNS?"
    assert "_voice_missing" not in saved


def test_the_offer_is_not_kept_in_the_model_history():
    service, _ = general_service()
    result = voice(service, "¿Qué es el DNS?")
    _, state = service.sessions.get(result["session_id"])
    assert VOICE_OFFER not in state["history"][-1][1]


MISSING = "Los manuales autorizados no cubren esta pregunta"


def test_detail_after_a_missing_manual_keeps_the_missing_manual_instruction():
    service, provider = general_service()
    first = voice(service, "¿Cómo configuro el proxy en Windows?")
    assert MISSING in provider.calls[-1]["prompt"]
    assert first["answer"].endswith(VOICE_OFFER)
    voice(service, "sí", first["session_id"])
    assert MISSING in provider.calls[-1]["prompt"]
    assert provider.calls[-1]["num_predict"] is None


def test_a_web_turn_drops_pending_voice_offers():
    service, _ = general_service()
    first = voice(service, "¿Qué es el DNS?")
    text(service, "¿Qué es un firewall?", first["session_id"])
    _, state = service.sessions.get(first["session_id"])
    assert not state.get("pending_detail") and not state.get("pending_rest")


def test_a_refusal_gets_no_detail_offer():
    refusal = "No tengo acceso a la documentación de tu empresa."
    service, _ = general_service(RecordingGeneralProvider(short=refusal, long=refusal))
    first = voice(service, "¿Cómo configuro la impresora de la oficina?")
    second = voice(service, "sí", first["session_id"])  # answers GENERAL_OFFER: the second refusal is returned
    assert VOICE_OFFER not in second["answer"]


def test_continua_asks_for_detail():
    service, provider = general_service()
    first = voice(service, "¿Qué es el DNS?")
    voice(service, "continúa", first["session_id"])
    assert provider.calls[-1]["num_predict"] is None and "¿Qué es el DNS?" in provider.calls[-1]["prompt"]


def test_stub_provider_without_generate_general_still_works_in_voice():
    service = build(provider=StubProvider("El DNS traduce nombres."), knowledge=StubKnowledge(**NO_EVIDENCE))
    result = voice(service, "¿Qué es el DNS?")
    assert result["answer"].endswith(VOICE_OFFER)
