"""Pruebas del "cerebro": proveedor Ollama y cómo lo usa el asistente."""
import asyncio
import json

import httpx
import pytest

from backend.app.assistant.service import UnifiedAssistantService
from backend.app.assistant.web_search import WebSearchResult
from backend.app.core.config import Settings
from backend.app.providers.base import ProviderOutputLimitError
from backend.app.providers.ollama import OllamaProvider

from tests.test_assistant_service import StubKnowledge, StubProvider, ask


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


def test_sends_configured_sampling_and_keep_alive():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(), transport=transport)
    asyncio.run(provider.generate("hola"))
    body = chats(calls)[0]
    assert body["keep_alive"] == "10m"
    assert body["options"]["top_p"] == 0.8
    assert body["options"]["top_k"] == 20
    assert body["options"]["min_p"] == 0.0
    assert body["options"]["temperature"] == 0.6


def test_model_check_is_reused_between_generations():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(), transport=transport)

    async def run():
        for _ in range(3):
            await provider.generate("hola")
    asyncio.run(run())
    assert [path for path, _ in calls].count("/api/show") == 1
    assert len(chats(calls)) == 3


def test_truncated_answer_is_cut_at_last_full_sentence():
    calls, transport = fake_ollama("Primero abre el menú. Después entra a Config", "length")
    provider = OllamaProvider(Settings(), transport=transport)
    assert asyncio.run(provider.generate("x")) == "Primero abre el menú."


def test_truncated_answer_without_sentence_still_fails():
    calls, transport = fake_ollama("Primero abre el", "length")
    provider = OllamaProvider(Settings(), transport=transport)
    with pytest.raises(ProviderOutputLimitError):
        asyncio.run(provider.generate("x"))


def test_old_history_is_dropped_but_the_question_is_kept():
    calls, transport = fake_ollama()
    provider = OllamaProvider(Settings(context_length=1024, max_output_tokens=256), transport=transport)
    messages = []
    for i in range(6):
        messages += [{"role": "user", "content": f"pregunta {i} " + "x" * 600},
                     {"role": "assistant", "content": "respuesta " + "y" * 600}]
    messages.append({"role": "user", "content": "PREGUNTA ACTUAL"})
    asyncio.run(provider.generate_chat(messages))
    sent = chats(calls)[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[-1]["content"] == "PREGUNTA ACTUAL"
    assert sent[1]["role"] == "user"
    assert len(sent) < len(messages) + 1


def test_narration_lists_every_step_to_the_model():
    calls, transport = fake_ollama("Primero conecta. Después apaga.")
    provider = OllamaProvider(Settings(), transport=transport)
    asyncio.run(provider.narrate_procedure("PROC", ["Connect.", "Turn off."]))
    content = chats(calls)[0]["messages"][1]["content"]
    assert "Numero de pasos: 2" in content
    assert "1. Connect." in content and "2. Turn off." in content


@pytest.mark.parametrize("field,value", [
    ("general_temperature", 3), ("top_p", 1.5), ("top_k", -1), ("keep_alive", "mucho"),
])
def test_invalid_settings_are_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


# ----------------------------------------------------------------- asistente
class SmartProvider(StubProvider):
    """Stub con las capacidades nuevas del proveedor Ollama."""

    def __init__(self, narration="Primero conecta el equipo. Después apágalo. Luego enciéndelo.",
                 web="Según Python.org, la última versión es la 3.14.", **kwargs):
        super().__init__(**kwargs)
        self.narration, self.web = narration, web
        self.chat_calls, self.narrations = [], []

    async def generate_chat(self, messages):
        self.chat_calls.append(messages)
        return "RESPUESTA CHAT"

    async def narrate_procedure(self, title, steps):
        self.narrations.append((title, steps))
        return self.narration

    async def summarize_web(self, question, results):
        return self.web


class StubWeb:
    async def search(self, message, max_results=5):
        return [WebSearchResult(title="Python.org", url="https://python.org", snippet="Python 3.14")]


def build(provider, knowledge="stub"):
    knowledge = StubKnowledge() if knowledge == "stub" else knowledge
    return UnifiedAssistantService(knowledge, provider, web_search=StubWeb())


def test_full_procedure_is_narrated_with_every_step():
    provider = SmartProvider()
    result = ask(build(provider), "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    title, steps = provider.narrations[0]
    assert title == "WINDOWS DEPLOYMENT PROCEDURE"
    assert len(steps) == 3
    assert "Primero conecta el equipo." in result["answer"]
    assert "[Fuente:" in result["answer"] or "Training Setup Guideline" in result["answer"]


def test_narration_that_skips_steps_falls_back_to_literal_list():
    provider = SmartProvider(narration="Primero conecta y luego todo lo demás.")
    result = ask(build(provider), "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    assert "Connect the computer to Centris Network by ethernet cable." in result["answer"]


def test_general_question_sends_history_as_turns_with_question_last():
    provider = SmartProvider()
    service = build(provider)
    first = ask(service, "¿Qué es una VPN?")
    ask(service, "¿Qué es un firewall?", first["session_id"])
    messages = provider.chat_calls[-1]
    assert messages[0] == {"role": "user", "content": "¿Qué es una VPN?"}
    assert messages[1]["role"] == "assistant"
    assert messages[-1]["content"].startswith("¿Qué es un firewall?")


def test_web_results_are_summarized_for_voice():
    service = build(SmartProvider(), knowledge=None)
    result = ask(service, "¿Cuál es la última versión de Python?")
    assert result["answer"] == "Según Python.org, la última versión es la 3.14."
    assert result["sources"][0]["url"] == "https://python.org"


def test_web_results_fall_back_to_list_when_summary_fails():
    service = build(SmartProvider(web=""), knowledge=None)
    result = ask(service, "¿Cuál es la última versión de Python?")
    assert "python.org" in result["answer"]
