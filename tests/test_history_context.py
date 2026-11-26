"""Stage "historial" 3: the conversation reaches the model as real turns sized to num_ctx (E4), and a follow-up of a
general exchange ("¿Y para qué sirve en una empresa?") stays general when the manuals have no evidence."""
import asyncio
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.assistant import context
from backend.app.assistant.service import GENERAL_NOTICE, UnifiedAssistantService
from backend.app.core.config import Settings
from backend.app.knowledge.models import KnowledgeAnswer
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.providers.ollama import GENERAL_MAX_OUTPUT_TOKENS, SYSTEM_PROMPT, OllamaProvider
from test_assistant_service import StubWebSearch
from test_named_reference import Knowledge
from test_ollama_general import LOCAL

UNVERIFIED = ("No pude verificar ese procedimiento en los manuales y documentos autorizados disponibles. "
              "No puedo afirmar un procedimiento de la empresa sin esa evidencia.")


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv(context.VARIABLE, raising=False)


class NoEvidence(Knowledge):
    """The real refusal of the knowledge service when no manual covers the question."""

    async def query(self, request):
        self.queries.append(request)
        return KnowledgeAnswer(status="insufficient_evidence", answer=UNVERIFIED, conversation_id=str(uuid.uuid4()),
                               intent="information", options=[], sources=[], documentation_sufficient=False,
                               retrieval={})


class TurnsProvider:
    """A provider that takes turns: records the prompt and the turns of every general answer."""

    def __init__(self):
        self.calls = []

    async def generate(self, message):
        self.calls.append((message, None))
        return "Respuesta general."

    async def generate_general_turns(self, message, turns):
        self.calls.append((message, list(turns)))
        return f"Respuesta general {len(self.calls)}."


class TextProvider:
    """A provider without turns: the previous behaviour (history pasted in the prompt)."""

    def __init__(self):
        self.calls = []

    async def generate_general(self, message):
        self.calls.append(message)
        return "Respuesta general."


def service(knowledge=None, provider=None):
    return UnifiedAssistantService(knowledge or NoEvidence(), provider or TurnsProvider(), web_search=StubWebSearch())


def chat(svc, message, sid="s"):
    return asyncio.run(svc.chat(message, session_id=sid))


# --- the follow-up of a general exchange --------------------------------------------------------------------------

@pytest.mark.parametrize("followup", ["¿Y para qué sirve en una empresa?", "¿y cuáles son los pasos?",
                                      "Pero, ¿qué configuración necesita?"])
def test_a_general_followup_stays_general_when_the_manuals_have_nothing(followup):
    knowledge, provider = NoEvidence(), TurnsProvider()
    svc = service(knowledge, provider)
    first = chat(svc, "¿Qué es una VPN?")
    assert first["route"] == "general"
    asked = len(knowledge.queries)
    answer = chat(svc, followup)
    assert answer["route"] == "general" and answer["sources"] == []
    assert answer["answer"].startswith(GENERAL_NOTICE) and "No pude verificar" not in answer["answer"]
    assert len(knowledge.queries) == asked + 1                      # the manuals were asked first
    prompt, turns = provider.calls[-1]
    assert followup in prompt and turns and turns[-1][0] == "¿Qué es una VPN?"  # with the conversation


@pytest.mark.parametrize("question", ["¿Y cómo se configura la VPN de la empresa?", "¿Y cuáles son los pasos en nuestra empresa?",
                                      "¿y cuál es la política?", "¿Y según el manual?"])
def test_the_company_own_procedures_and_manual_requests_keep_the_documentation_route(question):
    svc = service()
    chat(svc, "¿Qué es una VPN?")
    answer = chat(svc, question)
    assert answer["route"] == "knowledge" and not answer["answer"].startswith(GENERAL_NOTICE)


def test_a_followup_without_documentation_words_is_an_ordinary_general_followup():
    """"empresas" (plural) is not a documentation word (DOCUMENTED_TOPIC has \\bempresa\\b): the ordinary follow-up
    path answers it without asking the manuals, with the conversation and, being about the VPN, with the notice."""
    knowledge, provider = NoEvidence(), TurnsProvider()
    svc = service(knowledge, provider)
    chat(svc, "¿Qué es una VPN?")
    asked = len(knowledge.queries)
    answer = chat(svc, "¿Y en empresas grandes?")
    assert answer["route"] == "general" and answer["sources"] == [] and len(knowledge.queries) == asked
    assert answer["answer"] == GENERAL_NOTICE + "\nRespuesta general 2."
    assert provider.calls[-1][1][-1][0] == "¿Qué es una VPN?"


# --- GENERAL_NOTICE: every general answer about IT, never small talk or general culture -----------------------------

@pytest.mark.parametrize("followup", ["¿Y en empresas grandes?", "¿Me lo resumes en una frase?", "Explícamelo mejor",
                                      "¿Y si no conecta?", "¿Por qué es más segura?"])
def test_the_notice_follows_the_it_topic_into_its_followups(followup):
    svc = service()
    chat(svc, "¿Qué es una VPN?")
    answer = chat(svc, followup)
    assert answer["route"] == "general" and answer["answer"].startswith(GENERAL_NOTICE + "\n")
    assert answer["answer"].count(GENERAL_NOTICE) == 1


@pytest.mark.parametrize("question", ["¿Qué es una metáfora?", "¿Cuál es la capital de Francia?"])
def test_general_culture_has_no_notice_even_after_an_it_topic(question):
    svc = service()
    assert not chat(svc, question, "fresh")["answer"].startswith(GENERAL_NOTICE)
    chat(svc, "¿Qué es una VPN?")
    assert not chat(svc, question)["answer"].startswith(GENERAL_NOTICE)


def test_answers_from_public_sources_carry_the_notice_and_system_messages_do_not():
    svc = service()
    official = chat(svc, "¿Cuál es la versión más reciente de Windows 11?", "web")
    assert official["route"] == "general" and official["answer"].startswith(GENERAL_NOTICE + "\n")
    assert official["sources"]
    svc.web_search = StubWebSearch(error=RuntimeError("search unavailable"))
    failed = chat(svc, "¿Cuál es la versión más reciente de Windows 11?", "web-down")
    assert failed["answer"].startswith("No pude verificar información actual")


def test_the_notice_never_reaches_the_model_as_context():
    provider = TurnsProvider()
    svc = service(provider=provider)
    chat(svc, "¿Qué es una VPN?")
    chat(svc, "¿Y si no conecta?")
    turns = provider.calls[-1][1]
    assert turns and all(not answer.startswith(GENERAL_NOTICE) for _, answer in turns)
    text = TextProvider()
    svc = service(provider=text)
    chat(svc, "¿Qué es una VPN?")
    chat(svc, "¿Y si no conecta?")
    assert GENERAL_NOTICE not in text.calls[-1]


NO_TOPIC = "No supongas ni inventes un tema"


def test_a_followup_without_context_never_gets_an_invented_topic():
    provider = TurnsProvider()
    answer = chat(service(provider=provider), "¿Y en empresas grandes?")
    assert answer["route"] == "general"
    prompt, turns = provider.calls[-1]
    assert NO_TOPIC in prompt and not turns and "Pregunta anterior" not in prompt


def test_after_a_manual_exchange_the_followup_gets_the_previous_question_and_its_it_topic():
    provider = TurnsProvider()
    svc = service(provider=provider)
    assert chat(svc, "Según el manual, ¿qué es una VPN?")["route"] == "knowledge"
    answer = chat(svc, "¿Y en empresas grandes?")
    assert answer["route"] == "general" and answer["answer"].startswith(GENERAL_NOTICE + "\n")
    prompt, turns = provider.calls[-1]
    assert "Pregunta anterior del usuario: Según el manual, ¿qué es una VPN?" in prompt and NO_TOPIC in prompt
    assert not turns and "No pude verificar" not in prompt          # never the manual answer


def test_with_a_general_context_there_is_no_no_topic_instruction():
    provider = TurnsProvider()
    svc = service(provider=provider)
    chat(svc, "¿Qué es una VPN?")
    chat(svc, "¿Y en empresas grandes?")
    assert NO_TOPIC not in provider.calls[-1][0]


# --- a generic "empresa" is never a documentation word ---------------------------------------------------------------

@pytest.mark.parametrize("question", ["¿Qué es una VPN? ¿Y para qué sirve en una empresa?",
                                      "¿Para qué sirve una VPN en cualquier empresa?",
                                      "¿Qué es un firewall empresarial?", "¿Cómo usan las empresas una VPN?"])
def test_a_generic_company_is_answered_in_general_after_the_manuals(question):
    knowledge = NoEvidence()
    answer = chat(service(knowledge), question)
    assert answer["route"] == "general" and answer["answer"].startswith(GENERAL_NOTICE + "\n")
    assert knowledge.queries                                         # the manuals were asked first


@pytest.mark.parametrize("question", ["¿Cuál es el horario de la empresa?", "Configura la VPN de mi empresa",
                                      "¿Qué usa nuestra empresa para la VPN?"])
def test_the_company_itself_still_needs_the_manuals(question):
    answer = chat(service(), question)
    assert answer["route"] == "knowledge" and answer["answer"] == UNVERIFIED


# --- a follow-up inherits whether the previous topic was IT ------------------------------------------------------------

def test_a_followup_inherits_the_topic_of_the_previous_exchange():
    svc = service()
    chat(svc, "¿Qué es una VPN?")
    chat(svc, "¿Cuál es la capital de Francia?")
    assert not chat(svc, "¿Me lo resumes en una frase?")["answer"].startswith(GENERAL_NOTICE)
    chat(svc, "¿Qué es un firewall?")
    assert chat(svc, "¿Me lo resumes en una frase?")["answer"].startswith(GENERAL_NOTICE + "\n")


def test_the_inherited_topic_survives_a_restart():
    from backend.app.history.conversations import restore_state, snapshot
    svc = service()
    chat(svc, "¿Qué es una VPN?")
    chat(svc, "¿Cuál es la capital de Francia?")
    saved = snapshot(svc.sessions.get("s")[1])
    again = service()                                            # a new backend: the state comes from the history
    restore_state(again.sessions.get("s")[1], saved)
    assert not chat(again, "¿Me lo resumes en una frase?")["answer"].startswith(GENERAL_NOTICE)


def test_a_manual_with_evidence_still_wins_over_the_general_followup():
    svc = service()
    assert chat(svc, "¿Qué es una VPN?")["route"] == "general"
    svc.knowledge = Knowledge()                # now a manual answers every question with evidence
    answer = chat(svc, "¿Y para qué sirve en una empresa?")
    assert answer["route"] == "knowledge" and answer["sources"]


def test_the_followup_rule_needs_a_follow_up_shape():
    svc = service()
    chat(svc, "¿Qué es una VPN?")
    assert chat(svc, "Necesito la configuración de la impresora")["route"] == "knowledge"


# --- E4: real turns ------------------------------------------------------------------------------------------------

def test_general_answers_get_previous_general_exchanges_as_turns_not_as_text():
    provider = TurnsProvider()
    svc = service(provider=provider)
    _, state = svc.sessions.get("s")
    state.update({"history": [("¿Qué dice el manual de reinicio?", "Paso 1: apagar."), ("¿Qué es DNS?", "Un sistema.")],
                  "history_routes": ["knowledge", "general"], "active_route": "general"})
    asyncio.run(svc._general_answer(state, "¿Y un router?"))
    prompt, turns = provider.calls[-1]
    assert turns == [("¿Qué es DNS?", "Un sistema.")]       # document exchanges never reach the general model
    assert "Conversación previa" not in prompt and "¿Qué es DNS?" not in prompt


def test_prompt_mode_and_providers_without_turns_keep_the_text_context(monkeypatch):
    state = {"history": [("¿Qué es DNS?", "Un sistema.")], "history_routes": ["general"], "active_route": "general"}
    text = TextProvider()
    asyncio.run(service(provider=text)._general_answer(dict(state), "¿Y un router?"))
    assert "Conversación previa" in text.calls[-1] and "¿Qué es DNS?" in text.calls[-1]
    monkeypatch.setenv(context.VARIABLE, "prompt")
    turns = TurnsProvider()
    asyncio.run(service(provider=turns)._general_answer(dict(state), "¿Y un router?"))
    assert turns.calls[-1][1] is None and "Conversación previa" in turns.calls[-1][0]


def ollama(seen):
    def handler(request):
        payload = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, payload))
        if request.url.path == "/api/show":
            return httpx.Response(200, json=LOCAL)
        return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": "Respuesta."}})
    return OllamaProvider(Settings(), httpx.MockTransport(handler))


def test_ollama_receives_alternating_turns_within_the_context():
    seen = []
    provider = ollama(seen)
    turns = [("¿Qué es una VPN?", "Un túnel cifrado."), ("¿Y un proxy?", "Un intermediario.")]
    assert asyncio.run(provider.generate_general_turns("¿Y para qué sirve en una empresa?", turns)) == "Respuesta."
    payload = [p for path, p in seen if path == "/api/chat"][-1]
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert payload["messages"][0]["content"].startswith(SYSTEM_PROMPT)  # + today's date (2026-11-26)
    assert payload["messages"][1]["content"] == "¿Qué es una VPN?" and payload["messages"][-1]["content"].startswith("¿Y para")
    assert payload["options"]["num_ctx"] == provider.config.context_length
    assert payload["options"]["num_predict"] == min(GENERAL_MAX_OUTPUT_TOKENS, provider.config.context_length // 2)


def test_long_conversations_are_cut_to_the_newest_turns_that_fit():
    seen = []
    provider = ollama(seen)
    turns = [(f"Pregunta {n} " + "x" * 380, "Respuesta " + "y" * 900) for n in range(6)]
    asyncio.run(provider.generate_general_turns("¿Y ahora?", turns))
    messages = [p for path, p in seen if path == "/api/chat"][-1]["messages"]
    users = [m["content"] for m in messages if m["role"] == "user"][:-1]
    assert 0 < len(users) < 6 and users[-1].startswith("Pregunta 5")      # the newest ones, in order
    assert all(len(m["content"]) <= context.ANSWER_MAX for m in messages if m["role"] == "assistant")
    budget = (provider.config.context_length - min(GENERAL_MAX_OUTPUT_TOKENS, provider.config.context_length // 2)
              - context.SAFETY_TOKENS) * context.CHARS_PER_TOKEN
    assert sum(len(m["content"]) for m in messages) <= budget


def test_fit_turns_rules():
    assert context.fit_turns([], 2048, 768) == []
    turns = [("q1", "a1"), "basura", ("q2", "a2"), ("q3", "")]
    assert context.fit_turns(turns, 2048, 768) == []                          # an empty answer stops the walk
    assert context.fit_turns([("q1", "a1"), ("q2", "a2")], 2048, 768) == [("q1", "a1"), ("q2", "a2")]
    many = [(f"q{n}", f"a{n}") for n in range(10)]
    assert [q for q, _ in context.fit_turns(many, 2048, 768)] == [f"q{n}" for n in range(4, 10)]
    assert context.fit_turns([("q", "a" * 5000)], 2048, 768)[0][1].endswith("…")
    assert context.fit_turns([("q1", "a1")], 2048, 768, fixed_text="z" * 10_000) == []


@pytest.mark.parametrize("value", ["texto", "TURNS!", "1"])
def test_an_invalid_context_mode_stops_the_backend(monkeypatch, value):
    monkeypatch.setenv(context.VARIABLE, value)
    with pytest.raises(context.ContextConfigError):
        with TestClient(create_app(MockLLM())):
            pass
