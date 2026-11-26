"""Regression tests of the user's chat test of 2026-11-26 (web, typed). Each case keeps the exact question.

1. "Que es una vpn?" (lowercase, no "¿"): a manual that only *mentions* VPN never answers a definition with
   "Documento identificado"; the concept is explained.
2. "¿Qué es COMPUTER PREPARATION?": what the section is about, not its step list; the steps are offered.
3. "¿Cuáles son los pasos?" right after: the section being discussed, not "¿Qué procedimiento quieres consultar?".
4. "¿Qué día es hoy?": the server's own date, never a web page's (it answered "16 de noviembre de 2024").
"""
import asyncio
import datetime
import uuid

from backend.app.knowledge.models import KnowledgeAnswer
from test_assistant_service import StubWebSearch
from test_named_reference import Knowledge, ask, build


def session():
    return str(uuid.uuid4())


class IdentifyingKnowledge(Knowledge):
    """The real retrieval for "Que es una vpn?": the VPN support manual is *identified* (intent "identify")."""

    async def query(self, request):
        self.queries.append(request)
        source = {"title": "Manual de Soporte VPN Phase1B", "document_id": "doc-vpn", "page": 1, "version": "1",
                  "revision": 1, "citation_id": "cit-doc-vpn", "quote": ""}
        return KnowledgeAnswer(status="answered",
                               answer="Documento identificado: «Manual de Soporte VPN Phase1B» (versión 1).\n"
                                      "Si necesitas sus pasos, pregunta «¿Cuáles son los pasos de…?».",
                               conversation_id=str(uuid.uuid4()), intent="identify", options=[], sources=[source],
                               documentation_sufficient=True, retrieval={})


def test_a_definition_is_explained_not_answered_with_a_document_name():
    service, sid = build(IdentifyingKnowledge()), session()
    answer = ask(service, "Que es una vpn?", sid)
    assert "Documento identificado" not in answer["answer"]
    assert answer["route"] == "general"


def test_asking_for_a_name_still_identifies_the_document():
    knowledge = IdentifyingKnowledge()
    answer = ask(build(knowledge), "¿Cómo se llama el manual de la VPN?", session())
    assert "Documento identificado" in answer["answer"] or "VPN" in answer["answer"]


def test_what_is_a_section_says_what_it_is_and_offers_the_steps():
    service, sid = build(), session()
    answer = ask(service, "¿Qué es COMPUTER PREPARATION?", sid)["answer"]
    assert answer.startswith("«COMPUTER PREPARATION» es una sección de «Training Setup Guideline» (")
    assert "\n1. " not in answer                                          # not the step list
    assert "¿Cuáles son los pasos?" in answer                             # offered instead
    service2, sid2 = build(), session()
    listed = ask(service2, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid2)["answer"]
    count = listed.split("(", 1)[1].split(")", 1)[0]                        # "8 pasos": the same count in both
    assert f"({count})" in answer


def test_the_steps_question_right_after_uses_that_section():
    service, sid = build(), session()
    ask(service, "¿Qué es COMPUTER PREPARATION?", sid)
    steps = ask(service, "¿Cuáles son los pasos?", sid)["answer"]
    assert "¿Qué procedimiento quieres consultar?" not in steps
    assert steps.startswith("«COMPUTER PREPARATION» (") and "\n1. " in steps
    third = ask(service, "Explícame el paso 2", sid)["answer"]
    assert "Paso 2" in third and "COMPUTER PREPARATION" in third


def test_today_is_the_servers_date_without_searching():
    web = StubWebSearch()
    service = build(web_search=web)
    answer = asyncio.run(service.chat("¿Qué día es hoy?", session_id=session()))
    today = datetime.date.today()
    assert str(today.year) in answer["answer"] and str(today.day) in answer["answer"]
    assert web.calls == []


def test_the_model_always_knows_todays_date():
    # "Pero no me dijiste qué día" / "quiero el día, el mes y el año": any wording, since the model knows the date
    # (it answered "Hoy es el día de la semana que corresponde a la fecha actual en tu zona horaria").
    from test_history_context import ollama
    seen = []
    asyncio.run(ollama(seen).generate_general_turns("Pero no me dijiste qué día", [("¿Qué día es hoy?", "Hoy es…")]))
    system = [p for path, p in seen if path == "/api/chat"][-1]["messages"][0]["content"]
    from backend.app.core.clock import today_text
    assert today_text() in system                                          # "jueves 26 de noviembre de 2026"


class ExpiringKnowledge(Knowledge):
    """The manuals keep their conversations 30 min in memory; the assistant's is saved (and survives a restart)."""

    async def query(self, request):
        if request.conversation_id:
            from backend.app.knowledge.models import KnowledgeError
            raise KnowledgeError("conversation_expired", "Conversation expired.", 409)
        return await super().query(request)


def test_an_expired_manuals_conversation_starts_a_new_one():
    # After a restart (or 30 min) "¿Qué es una VPN?" answered "No se pudo consultar la documentación autorizada".
    knowledge = ExpiringKnowledge()
    service, sid = build(knowledge), session()
    ask(service, "Dame los pasos para reiniciar el servidor", sid)
    state = service.sessions.get(sid)[1]
    state["knowledge_conversation_id"] = str(uuid.uuid4())               # as restored from the database
    answer = ask(service, "Dame los pasos de la guía de instalación", sid)["answer"]
    assert "No se pudo consultar" not in answer
    assert knowledge.queries[-1].conversation_id is None


def test_a_bet_is_not_looked_up_on_the_web():
    from backend.app.assistant.service import UnifiedAssistantService
    assert not UnifiedAssistantService._needs_web("¿A qué equipo le apuesto en el partido de hoy?")
    assert UnifiedAssistantService._needs_web("¿Cuál es la última versión de Windows 11?")


def test_general_answers_carry_the_scope_rule():
    from backend.app.assistant.service import GENERAL_SCOPE
    assert "no es un tema de TI" in GENERAL_SCOPE and "preguntas ligeras" in GENERAL_SCOPE
