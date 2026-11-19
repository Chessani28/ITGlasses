"""A general IT question after several manual questions is answered in general, labelled as not coming from the
manuals: the document exchanges no longer reach the general prompt, a refusal for lack of documentation is
generated again once without context, and a second one becomes a question."""
import asyncio
import json
import uuid

import httpx
from fastapi.testclient import TestClient

from backend.app.assistant.memory import MAX_HISTORY, SessionStore
from backend.app.assistant.service import GENERAL_NOTICE, GENERAL_OFFER, UnifiedAssistantService
from backend.app.core.config import Settings
from backend.app.knowledge.models import KnowledgeAnswer
from backend.app.main import create_app
from backend.app.providers.ollama import OllamaProvider
from test_assistant_service import StubWebSearch
from test_named_reference import Knowledge

VPN = "Explícame con detalle cómo funciona una VPN"
MANUAL_QUESTIONS = [
    "Según el procedimiento de reinicio, ¿qué hago primero?",
    "Según el manual de Server Rack Guide, cómo se monta un servidor",
    "Según el manual de Training Setup Guideline, ¿qué dice la sección de contabilidad?",
    "Según el manual de soporte VPN Phase1B, ¿cuánto tarda la conexión?",
]
REFUSAL = "No puedo explicar cómo funciona una VPN porque no tengo acceso a documentación oficial."


class NoEvidenceForConcepts(Knowledge):
    """Manual questions as in test_named_reference; conceptual VPN questions find no evidence (the manual only has
    support steps)."""

    async def query(self, request):
        if "funciona" in request.message.lower() or "firewall" in request.message.lower():
            self.queries.append(request)
            return KnowledgeAnswer(status="insufficient_evidence", answer="No pude verificar ese procedimiento.",
                                   conversation_id=str(uuid.uuid4()), intent="information", options=[], sources=[],
                                   documentation_sufficient=False, retrieval={})
        return await super().query(request)


class ScriptedProvider:
    """Returns the scripted answers in order (the last one repeats) and records every prompt."""

    def __init__(self, *answers):
        self.answers = list(answers) or ["Una VPN crea un túnel cifrado entre tu equipo y otra red."]
        self.calls = []

    async def generate(self, message):
        self.calls.append(message)
        return self.answers[min(len(self.calls), len(self.answers)) - 1]


def chat(service, message, sid):
    return asyncio.run(service.chat(message, session_id=sid))


def build(provider):
    return UnifiedAssistantService(NoEvidenceForConcepts(), provider, web_search=StubWebSearch())


def test_the_phone_sequence_answers_in_general_without_the_document_exchanges():
    provider = ScriptedProvider()
    service = build(provider)
    sid = str(uuid.uuid4())
    for question in MANUAL_QUESTIONS:
        chat(service, question, sid)
    calls = len(provider.calls)
    result = chat(service, VPN, sid)
    assert result["route"] == "general" and result["sources"] == []
    assert result["answer"] == GENERAL_NOTICE + "\nUna VPN crea un túnel cifrado entre tu equipo y otra red."
    prompt = provider.calls[-1]
    assert len(provider.calls) == calls + 1
    for leaked in ("Conversación previa", "No encontré", "no identifiqué", "[Fuente", "Paso 1 de", "Respuesta documentada"):
        assert leaked not in prompt, leaked
    assert "no te niegues por falta de documentación" in prompt


def test_a_new_conversation_gets_the_notice_too():
    result = chat(build(ScriptedProvider()), VPN, str(uuid.uuid4()))
    assert result["route"] == "general" and result["answer"].startswith(GENERAL_NOTICE + "\n")


def test_a_general_follow_up_keeps_its_general_context():
    provider = ScriptedProvider()
    service = build(provider)
    sid = str(uuid.uuid4())
    chat(service, MANUAL_QUESTIONS[0], sid)
    chat(service, VPN, sid)
    chat(service, "¿Y cómo funciona un firewall?", sid)
    prompt = provider.calls[-1]
    assert "Conversación previa" in prompt and VPN in prompt
    assert MANUAL_QUESTIONS[0] not in prompt and "Apagar el servidor" not in prompt


def test_a_refusal_is_generated_again_once_without_the_conversation():
    provider = ScriptedProvider(REFUSAL, "Una VPN cifra el tráfico y lo envía por un túnel.")
    service = build(provider)
    sid = str(uuid.uuid4())
    chat(service, MANUAL_QUESTIONS[0], sid)
    chat(service, "¿Qué es un firewall?", sid)  # a general exchange, so the first try carries context
    provider.calls.clear()
    provider.answers = [REFUSAL, "Una VPN cifra el tráfico y lo envía por un túnel."]
    result = chat(service, VPN, sid)
    assert result["answer"] == GENERAL_NOTICE + "\nUna VPN cifra el tráfico y lo envía por un túnel."
    assert len(provider.calls) == 2
    assert "Conversación previa" in provider.calls[0] and "Conversación previa" not in provider.calls[1]


def test_a_second_refusal_asks_and_yes_explains_in_general():
    provider = ScriptedProvider(REFUSAL, REFUSAL, "Una VPN crea un túnel cifrado.")
    service = build(provider)
    sid = str(uuid.uuid4())
    asked = chat(service, VPN, sid)
    assert asked["answer"] == GENERAL_OFFER and asked["sources"] == []
    yes = chat(service, "sí", sid)
    assert yes["route"] == "general" and yes["answer"] == GENERAL_NOTICE + "\nUna VPN crea un túnel cifrado."
    assert VPN in provider.calls[-1] and "Conversación previa" not in provider.calls[-1]


def test_no_declines_the_offer_and_other_messages_drop_it():
    provider = ScriptedProvider(REFUSAL)
    service = build(provider)
    sid = str(uuid.uuid4())
    chat(service, VPN, sid)
    assert chat(service, "no", sid)["answer"] == "De acuerdo."
    chat(service, VPN, sid)
    chat(service, "Según el procedimiento de reinicio, ¿qué hago primero?", sid)
    assert "pending_general" not in service.sessions.get(sid)[1]


def test_the_notice_is_only_for_it_questions_the_manuals_did_not_cover():
    provider = ScriptedProvider("La capital de Francia es París.")
    service = build(provider)
    general = chat(service, "¿Cuál es la capital de Francia?", str(uuid.uuid4()))
    assert not general["answer"].startswith(GENERAL_NOTICE)
    documented = chat(service, "Según el procedimiento de reinicio, ¿qué hago primero?", str(uuid.uuid4()))
    assert not documented["answer"].startswith(GENERAL_NOTICE)


def test_history_routes_stay_aligned_with_the_history():
    store = SessionStore()
    _, state = store.get("s")
    state["history"] = [("antes", "respuesta")]  # a state from before the routes existed
    for index in range(MAX_HISTORY + 5):
        state["active_route"] = "general" if index % 2 else "knowledge"
        store.remember(state, f"p{index}", f"r{index}")
    assert len(state["history"]) == len(state["history_routes"]) == MAX_HISTORY
    assert state["history_routes"][-1] == "knowledge" and state["history"][-1] == (f"p{MAX_HISTORY + 4}", f"r{MAX_HISTORY + 4}")
    store.remember(state, "x", "y", route="social")
    assert state["history_routes"][-1] == "social"


def test_ollama_system_prompt_no_longer_says_nothing_is_indexed_also_for_api_v1_chat():
    seen = []

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": "respuesta"}})

    provider = OllamaProvider(Settings(), transport=httpx.MockTransport(handler))
    with TestClient(create_app(provider)) as client:
        response = client.post("/api/v1/chat", json={"message": "¿Qué es una VPN?"})
    assert response.status_code == 200 and response.json()["answer"] == "respuesta"
    system = seen[-1]["messages"][0]["content"]
    assert "No documentation is indexed" not in system
    assert "Never refuse a general question because documentation is missing" in system
