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


# --- Coherence battery of 2026-11-26 (the assistant's own review of 51 answers) -----------------------------------

def test_the_greeting_does_not_name_a_part_of_the_web_page():
    # "…o usar el Chat general para otros temas" makes no sense through the glasses.
    from backend.app.knowledge.social import social_response
    greeting = social_response("greeting")
    assert "Chat general" not in greeting and "TI" in greeting


def test_context_added_to_a_support_thread_is_not_refused_as_company_documentation():
    # "Olvidé mi contraseña de Windows" → "Es una cuenta de la empresa" answered "No pude verificar… en los manuales".
    from backend.app.assistant.service import UnifiedAssistantService
    state = {"active_route": "general", "history": [("Olvidé mi contraseña de Windows", "Puedes restablecerla…")]}
    assert UnifiedAssistantService._general_followup(state, "Es una cuenta de la empresa")
    assert UnifiedAssistantService._general_followup(state, "Ya lo intenté y no funciona en la empresa")
    assert not UnifiedAssistantService._general_followup(state, "¿Cuál es la política de la empresa?")


def test_more_detail_about_a_fault_asks_for_more_steps_not_a_definition():
    # Voice: "Mi mouse no funciona" → "Sí" explained what a mouse is.
    from test_assistant_service import StubProvider
    from test_history_context import NoEvidence
    from backend.app.assistant.service import UnifiedAssistantService
    provider = StubProvider("Revisa la conexión.")
    service = UnifiedAssistantService(NoEvidence(), provider, web_search=StubWebSearch())
    sid = session()
    asyncio.run(service.chat("Mi mouse no funciona", session_id=sid, channel="voice"))
    asyncio.run(service.chat("Sí", session_id=sid, channel="voice"))
    detail_prompt = provider.calls[-1]
    assert "más pasos" in detail_prompt and "para qué sirve" not in detail_prompt


def test_how_to_play_a_video_game_is_not_it_but_a_game_that_fails_is():
    from backend.app.assistant.service import game_play_answer
    for question in ("¿Cómo consigo diamantes en Minecraft?", "¿Cómo gano en Fortnite?",
                     "¿Qué jugadores me recomiendas para mi equipo en FIFA?", "¿Cómo defiendo el 2.6 en Clash Royale?",
                     "¿Qué personaje es mejor en Free Fire?"):
        answer = game_play_answer(question)
        assert answer and "TI" in answer, question
    for question in ("Mi juego se traba mucho en la computadora", "Minecraft no abre", "Fortnite me da error al iniciar",
                     "¿Cómo instalo Roblox en la laptop?", "Tengo mucho lag en Valorant", "¿Qué es una VPN?",
                     "Si fueras panadero, ¿qué pan venderías?"):
        assert game_play_answer(question) is None, question


def test_a_game_question_never_reaches_the_manuals_or_the_web():
    web = StubWebSearch()
    knowledge = Knowledge()
    service = build(knowledge, web_search=web)
    answer = asyncio.run(service.chat("¿Cómo consigo diamantes en Minecraft?", session_id=session()))
    assert "TI" in answer["answer"] and "manual" not in answer["answer"].lower()
    assert knowledge.queries == [] and web.calls == []


def test_game_safety_and_names_that_are_not_games_stay_out():
    from backend.app.assistant.service import game_play_answer
    for question in ("Mi hijo juega Roblox, ¿es seguro?", "Me hackearon la cuenta de Fortnite", "jaja lol",
                     "Mario no puede entrar a su correo"):
        assert game_play_answer(question) is None, question


def test_a_procedure_read_in_parts_shows_the_same_part_it_says():
    # Voice, 8 steps: "Sí" read 4-6 but the bubble showed 4-8, so the next "Sí" (7-8) looked repeated.
    service, sid = build(), session()

    def say(message):
        return asyncio.run(service.chat(message, session_id=sid, channel="voice"))

    first = say("¿Cuáles son los pasos de COMPUTER PREPARATION?")
    assert "Paso 3" in first["speech"] and "Paso 4" not in first["speech"]
    second = say("Sí")
    assert "Paso 4" in second["speech"] and "Paso 6" in second["speech"] and "Paso 7" not in second["speech"]
    assert "Paso 7" not in second["answer"]                    # the bubble = what was said
    third = say("Sí")
    assert "Paso 7" in third["answer"] and "Paso 8" in third["answer"]
    assert "Paso 4" not in third["answer"]


def test_yes_after_the_last_step_says_there_are_no_more():
    service, sid = build(), session()

    def say(message):
        return asyncio.run(service.chat(message, session_id=sid, channel="voice"))

    say("¿Cuáles son los pasos de COMPUTER PREPARATION?")
    say("Sí")
    say("Sí")                                                      # 7-8: the last part
    done = say("Sí")
    assert "todos los pasos" in done["answer"] and "Paso" not in done["answer"]


# --- The assistant's own suggestions about a manual's procedure (asked by the user, never mixed with it) ------------

class RecordingProvider:
    def __init__(self, answer="Antes del paso 6, apaga el equipo y desconéctalo."):
        self.answer, self.calls = answer, []

    async def generate(self, message):
        self.calls.append(message)
        return self.answer


def test_asking_what_to_add_gets_labelled_suggestions_from_the_real_steps():
    from backend.app.assistant.service import UnifiedAssistantService, SUGGESTIONS_LABEL
    provider = RecordingProvider()
    service = UnifiedAssistantService(Knowledge(), provider, web_search=StubWebSearch())
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    answer = ask(service, "¿Y si tú le tuvieras que agregar más pasos, cuáles serían?", sid)
    assert answer["answer"].startswith(SUGGESTIONS_LABEL.format(title="COMPUTER PREPARATION"))
    assert "no están en el manual" in answer["answer"] and "No pude verificar" not in answer["answer"]
    assert "apaga el equipo" in answer["answer"]
    prompt = provider.calls[-1]
    assert "COMPUTER PREPARATION" in prompt and "Thermal paste" in prompt      # the manual's own steps


def test_other_ways_of_asking_for_an_opinion_on_the_steps():
    from backend.app.assistant.service import asks_for_suggestions
    for question in ("¿Qué le agregarías?", "¿Le falta algún paso?", "¿Sobra algún paso?", "¿Qué opinas de estos pasos?",
                     "¿Cómo lo mejorarías?", "¿Se repite algún paso?", "¿Qué le quitarías?", "¿Le cambiarías algo?"):
        assert asks_for_suggestions(question), question
    for question in ("¿Cuáles son los pasos?", "Explícame el paso 2", "¿Qué es COMPUTER PREPARATION?", "Sí",
                     "¿Qué le agrego al café?"):
        assert not asks_for_suggestions(question), question


def test_without_a_procedure_in_the_conversation_it_is_answered_as_before():
    from backend.app.assistant.service import SUGGESTIONS_LABEL
    answer = ask(build(), "¿Qué le agregarías?", session())
    assert not answer["answer"].startswith(SUGGESTIONS_LABEL.split("«")[0])


def test_the_suggestions_answer_the_question_asked_and_hide_the_internal_word():
    provider = RecordingProvider("- El paso 6 y el 7 se pueden hacer juntos.\nCOMPLETO")
    from backend.app.assistant.service import UnifiedAssistantService
    service = UnifiedAssistantService(Knowledge(), provider, web_search=StubWebSearch())
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    answer = ask(service, "¿Sobra algún paso?", sid)["answer"]
    assert "¿Sobra algún paso?" in provider.calls[-1]                     # the user's own question reaches the model
    assert "COMPLETO" not in answer and "se pueden hacer juntos" in answer
