"""Cerebro: historial como turnos, narración de procedimientos, redacción de manuales y muestreo."""
import asyncio
import json

import httpx
import pytest

from backend.app.assistant.service import UnifiedAssistantService
from backend.app.core.config import Settings
from backend.app.providers.ollama import OllamaProvider

from tests.test_assistant_service import StubKnowledge, StubProvider, StubWebSearch, ask
from tests.test_general_after_manuals import NoEvidenceForConcepts


# ----------------------------------------------------------------- proveedor
def fake_ollama(reply="hola.", done_reason="stop"):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}})
        return httpx.Response(200, json={"done": True, "done_reason": done_reason,
                                         "message": {"content": reply}})
    return calls, httpx.MockTransport(handler)


def chats(calls):
    return [body for path, body in calls if path == "/api/chat"]


def test_sends_sampling_recommended_for_qwen():
    calls, transport = fake_ollama()
    asyncio.run(OllamaProvider(Settings(), transport=transport).generate_general("hola"))
    options = chats(calls)[0]["options"]
    assert (options["top_p"], options["top_k"], options["min_p"]) == (0.8, 20, 0.0)
    assert options["temperature"] == 0.6


def test_model_check_is_reused_between_generations():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(), transport=transport)

    async def run():
        for _ in range(3):
            await provider.generate_general("hola")
    asyncio.run(run())
    assert [path for path, _ in calls].count("/api/show") == 1


def test_history_goes_as_turns_and_the_question_is_last():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(), transport=transport)
    asyncio.run(provider.generate_general("¿Y DHCP?", history=[("¿Qué es DNS?", "Un sistema de nombres.")]))
    sent = chats(calls)[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[-1]["content"] == "¿Y DHCP?"


def test_old_history_is_dropped_but_the_question_is_kept():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(context_length=1024, max_output_tokens=256), transport=transport)
    history = [(f"pregunta {i} " + "x" * 600, "respuesta " + "y" * 600) for i in range(6)]
    asyncio.run(provider.generate_general("PREGUNTA ACTUAL", history=history))
    sent = chats(calls)[0]["messages"]
    assert sent[-1]["content"] == "PREGUNTA ACTUAL"
    assert sent[1]["role"] == "user"
    assert len(sent) < 2 + 2 * len(history)


def test_narration_sends_every_step_with_the_spoken_prompt():
    calls, transport = fake_ollama("Primero conecta. Después apaga.")
    provider = OllamaProvider(Settings(), transport=transport)
    asyncio.run(provider.narrate_procedure("PROC", ["Connect.", "Turn off."]))
    body = chats(calls)[0]
    assert "TODOS los pasos" in body["messages"][0]["content"]
    assert "1. Connect." in body["messages"][1]["content"] and "2. Turn off." in body["messages"][1]["content"]
    assert body["options"]["temperature"] == 0.2


def test_grounded_answer_is_spoken_style_and_low_temperature():
    calls, transport = fake_ollama("Primero conecta el equipo.")
    provider = OllamaProvider(Settings(), transport=transport)
    answer = asyncio.run(provider.generate_grounded_answer("¿Cómo?", [{"title": "Manual", "text": "Connect."}]))
    body = chats(calls)[0]
    assert answer == "Primero conecta el equipo."
    assert "lenguaje natural y hablado" in body["messages"][0]["content"]
    assert body["options"]["temperature"] == 0.2


@pytest.mark.parametrize("field,value", [
    ("general_temperature", 3), ("top_p", 1.5), ("top_k", -1), ("keep_alive", "mucho"),
])
def test_invalid_settings_are_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


# ----------------------------------------------------------------- asistente
class NarratingProvider(StubProvider):
    def __init__(self, narration="Primero conecta el equipo. Después apágalo. Luego enciéndelo.", **kwargs):
        super().__init__(**kwargs)
        self.narration, self.narrations = narration, []

    async def narrate_procedure(self, title, steps):
        self.narrations.append((title, steps))
        return self.narration


class HistoryProvider(StubProvider):
    supports_history = True

    def __init__(self, **kwargs):
        super().__init__(answer="RESPUESTA", **kwargs)
        self.general_calls = []

    async def generate_general(self, message, history=None):
        self.general_calls.append((message, history))
        return "RESPUESTA GENERAL"


def test_full_procedure_is_narrated_with_every_step():
    provider = NarratingProvider()
    result = ask(UnifiedAssistantService(StubKnowledge(), provider), "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    title, steps = provider.narrations[0]
    assert title == "WINDOWS DEPLOYMENT PROCEDURE" and len(steps) == 3
    assert "Primero conecta el equipo." in result["answer"]


def test_narration_that_skips_steps_falls_back_to_literal_list():
    provider = NarratingProvider(narration="Primero conecta y luego todo lo demás.")
    result = ask(UnifiedAssistantService(StubKnowledge(), provider), "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    assert "Connect the computer to Centris Network by ethernet cable." in result["answer"]


def test_general_follow_up_sends_previous_general_turns_as_history():
    provider = HistoryProvider()
    service = UnifiedAssistantService(NoEvidenceForConcepts(), provider, web_search=StubWebSearch())
    first = ask(service, "Explícame cómo funciona una VPN")
    assert first["route"] == "general", first
    ask(service, "¿Y cómo funciona un firewall?", first["session_id"])
    message, history = provider.general_calls[-1]
    assert history and history[0][0] == "Explícame cómo funciona una VPN"
    assert "¿Y cómo funciona un firewall?" in message
    assert "Conversación previa" not in message


def test_providers_without_history_support_keep_the_text_prompt():
    provider = StubProvider(answer="Una VPN cifra el tráfico.")
    service = UnifiedAssistantService(NoEvidenceForConcepts(), provider, web_search=StubWebSearch())
    first = ask(service, "Explícame cómo funciona una VPN")
    ask(service, "¿Y cómo funciona un firewall?", first["session_id"])
    assert "Conversación previa" in provider.calls[-1]
