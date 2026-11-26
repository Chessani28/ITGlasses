"""Regression tests of the web questions of 2026-11-24 (15 asked, 10 right). Each case keeps the exact question.

1. "¿Qué es <sección>?" about a manual says what the section is (2026-11-26: no longer its step list) and never
   shows an internal id.
2. A name that matches nothing related is not "closest" to an unrelated section; a general answer is offered.
3. The refusal without evidence uses the word of the question (política, procedimiento) or "información".
4. An IT question with a typo ("q es un firewal") is IT: the general answer carries its notice.
(5, the Markdown of the answers in the web, is in tests/markdown.test.mjs.)
"""
import asyncio
import re
import uuid

import pytest

from backend.app.assistant import named
from backend.app.assistant.service import GENERAL_NOTICE, MISSING_OFFER, UnifiedAssistantService
from backend.app.knowledge.service import unverified_message
from test_assistant_service import StubWebSearch
from test_history_context import NoEvidence, TurnsProvider
from test_named_reference import Knowledge, ask, build, index

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def session():
    return str(uuid.uuid4())


# --- 1. "¿Qué es <sección>?" ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["Según el manual Training Setup Guideline, ¿qué es COMPUTER PREPARATION?",
                                      "¿Qué es COMPUTER PREPARATION?",
                                      "¿En qué consiste COMPUTER PREPARATION?"])
def test_what_is_a_section_says_what_it_is_without_internal_ids(question):
    # 2026-11-26: what it is (described from its steps) with the steps offered, no longer the list itself.
    knowledge = Knowledge()
    service, sid = build(knowledge), session()
    answer = ask(service, question, sid)
    assert answer["route"] == "knowledge" and answer["sources"] and knowledge.queries == []
    assert answer["answer"].startswith("«COMPUTER PREPARATION» es una sección de «Training Setup Guideline» (")
    assert answer["answer"].count("COMPUTER PREPARATION") == 1           # the title once, not twice
    assert "\n1. " not in answer["answer"] and "¿Cuáles son los pasos?" in answer["answer"]
    assert "Documento identificado" not in answer["answer"]
    assert not UUID.search(answer["answer"])
    third = ask(service, "¿Y cuál es el paso 3?", sid)                       # the follow-up still works
    assert third["route"] == "knowledge" and "Paso 3" in third["answer"] and not UUID.search(third["answer"])


def test_the_steps_question_is_unchanged():
    answer = ask(build(), "¿Cuáles son los pasos de COMPUTER PREPARATION?", session())
    assert answer["answer"].startswith("«COMPUTER PREPARATION» (") and "es una sección" not in answer["answer"]


@pytest.mark.parametrize("question", ["¿Qué es una VPN?", "¿Qué es una computadora?", "¿Qué es la preparación?"])
def test_what_is_a_general_concept_never_reads_a_section(question):
    answer = ask(build(), question, session())
    assert "es una sección" not in answer["answer"]


# --- 2. Nothing related: no unrelated suggestion, a general answer offered -------------------------------------------

def test_an_unrelated_name_suggests_nothing_and_offers_a_general_answer():
    service, sid = UnifiedAssistantService(Knowledge(), TurnsProvider(), web_search=StubWebSearch()), session()
    question = "¿Cuál es el procedimiento para cambiar la impresora del piso 2?"
    answer = ask(service, question, sid)
    assert answer["answer"] == ("No encontré un procedimiento llamado «cambiar la impresora del piso 2» en los "
                                "documentos autorizados. " + MISSING_OFFER)
    assert "COMPUTER PREPARATION" not in answer["answer"]
    general = ask(service, "sí", sid)
    assert general["route"] == "general" and general["answer"].startswith(GENERAL_NOTICE + "\n")


@pytest.mark.parametrize(("name", "nearest"), [
    ("cambiar la impresora del piso 2", None),
    ("configurar la vpn", None),
    ("arranque del servidor", "Reinicio de servidor"),        # a shared word: still offered
    ("Trading Sarah Guyton", "Training Setup Guideline"),     # a misheard name: still offered
])
def test_only_related_names_are_offered_as_the_closest_one(name, nearest):
    assert named.resolve(name, index()).nearest == nearest


# --- 3. The word of the question in the refusal ----------------------------------------------------------------------

@pytest.mark.parametrize(("question", "expected"), [
    ("¿Cuál es la política de contraseñas de la empresa?",
     "No pude verificar esa política en los manuales y documentos autorizados disponibles. "
     "No puedo afirmar una política de la empresa sin esa evidencia."),
    ("¿Cuál es el procedimiento de alta de usuarios?",
     "No pude verificar ese procedimiento en los manuales y documentos autorizados disponibles. "
     "No puedo afirmar un procedimiento de la empresa sin esa evidencia."),
    ("¿Qué dice el manual sobre el horario de la empresa?",
     "No pude verificar esa información en los manuales y documentos autorizados disponibles. "
     "No puedo afirmar información de la empresa sin esa evidencia."),
])
def test_the_refusal_uses_the_word_of_the_question(question, expected):
    assert unverified_message(question) == expected


# --- 4. IT with a typo -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["q es un firewal", "mi ruter no prende", "como configuro el swich"])
def test_an_it_question_with_a_typo_carries_the_general_notice(question):
    service = UnifiedAssistantService(NoEvidence(), TurnsProvider(), web_search=StubWebSearch())
    answer = asyncio.run(service.chat(question, session_id=session()))
    assert answer["route"] == "general" and answer["answer"].startswith(GENERAL_NOTICE + "\n")


@pytest.mark.parametrize("question", ["¿Quién pintó la Mona Lisa?", "quiero comprar un mueble"])
def test_general_culture_still_has_no_notice(question):
    service = UnifiedAssistantService(NoEvidence(), TurnsProvider(), web_search=StubWebSearch())
    assert not asyncio.run(service.chat(question, session_id=session()))["answer"].startswith(GENERAL_NOTICE)
