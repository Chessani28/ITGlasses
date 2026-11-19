"""Spanish aliases of the English headings (matching, Whisper vocabulary, correction) and the sections answer for a
named document whose section was not recognized, with its "¿Te refieres a «X»?" follow-up."""
import uuid

import pytest

from backend.app.assistant import named
from backend.app.assistant.service import _section_reply
from backend.app.assistant.speech import spoken_summary
from backend.app.voice import correction
from backend.app.voice.vocabulary import MAX_CHARS, build, from_knowledge
from test_named_reading import HEADINGS, Insufficient
from test_named_reference import Knowledge, ask, build as build_service, index

SECTIONS = ("COMPUTER PREPARATION, WINDOWS DEPLOYMENT PROCEDURE, GRAPHENE STATIONS DEPLOYMENT PROCEDURE, "
            "REQUIRED PERIPHERALS, APPS INSTALLATION, CONFIRMS THE CPU FRECUENCY")
MISHEARD = "Según el Manual Training Setup Guideline, ¿Qué dice Periféricos Perferinos?"


def session():
    return f"aliases-{uuid.uuid4()}"


# --- Alias table -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("spoken", "heading"), [
    ("preparación de la computadora", "COMPUTER PREPARATION"),
    ("despliegue de Windows", "WINDOWS DEPLOYMENT PROCEDURE"),
    ("despliegue de estaciones Graphene", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE"),
    ("periféricos requeridos", "REQUIRED PERIPHERALS"),
    ("perifericos requerido", "REQUIRED PERIPHERALS"),       # without accents, singular
    ("instalación de aplicaciones", "APPS INSTALLATION"),
    ("frecuencia del CPU", "CONFIRMS THE CPU FRECUENCY"),
])
def test_each_alias_names_its_heading(spoken, heading):
    assert named.find_heading(f"qué dice {spoken}", HEADINGS)[0] == heading
    resolution = named.resolve(spoken, index())
    assert (resolution.status, resolution.procedure) == ("found", heading)


def test_alias_table_has_one_alias_per_english_heading():
    assert len(named.ALIASES) == 6
    assert all(named.alias_of(heading) for heading in HEADINGS if heading != "Reinicio de servidor")
    assert named.alias_of("Reinicio de servidor") is None


def test_close_but_not_matching_names_are_only_suggested():
    assert named.find_heading(MISHEARD, HEADINGS) is None
    assert named.closest_heading(MISHEARD, HEADINGS) == "REQUIRED PERIPHERALS"
    assert named.closest_heading("¿qué dice el clima de hoy?", HEADINGS) is None
    resolution = named.resolve("Training Setup Guideline", index(), MISHEARD)
    assert (resolution.procedure, resolution.suggestion) == (None, "REQUIRED PERIPHERALS")


# --- Whisper vocabulary (Voz 2) --------------------------------------------------------------------------------

def test_vocabulary_adds_the_aliases_of_existing_headings_last():
    plain = from_knowledge(Knowledge())
    with_aliases = from_knowledge(Knowledge(), aliases=True)
    assert plain.aliases == 0 and with_aliases.aliases == 6
    assert with_aliases.stats()["terms"] == plain.stats()["terms"] + 6 <= 33
    assert with_aliases.stats()["chars"] <= MAX_CHARS
    assert with_aliases.hint.startswith(plain.hint)        # aliases go last: a token limit cuts them first
    assert with_aliases.hint.endswith("frecuencia del CPU")
    assert "periféricos requeridos" in [t.text for t in with_aliases.candidates]


def test_aliases_are_dropped_first_when_the_limits_are_reached():
    base = build(["Training Setup Guideline"], ["REQUIRED PERIPHERALS"])
    tight = build(["Training Setup Guideline"], ["REQUIRED PERIPHERALS"], aliases=["periféricos requeridos"],
                  max_chars=len(base.hint))
    assert tight.aliases == 0 and tight.hint == base.hint


def test_correction_recognizes_the_aliases_but_not_a_distant_mishearing():
    vocabulary = from_knowledge(Knowledge(), aliases=True)
    text, made = correction.correct("qué dice periféricos requerido", vocabulary)
    assert text == "qué dice periféricos requeridos" and made
    text, made = correction.correct("Qué dice Periféricos Perferinos", vocabulary)
    assert made == [] and text == "Qué dice Periféricos Perferinos"


# --- Sections answer -------------------------------------------------------------------------------------------

def test_unrecognized_section_suggests_the_close_one_and_yes_reads_it():
    knowledge = Insufficient()
    service = build_service(knowledge)
    sid = session()
    first = ask(service, MISHEARD, sid)
    assert first["answer"] == ("En «Training Setup Guideline» no identifiqué esa sección. ¿Te refieres a "
                               f"«REQUIRED PERIPHERALS»? Tiene: {SECTIONS}. ¿Cuál quieres?")
    assert first["sources"] == [] and knowledge.queries == []
    yes = ask(service, "Sí, ese", sid)
    assert yes["answer"].startswith("«REQUIRED PERIPHERALS» (2 pasos):") and yes["sources"]
    assert knowledge.queries == []


def test_no_lists_the_sections_and_the_suggestion_does_not_survive():
    service = build_service(Insufficient())
    sid = session()
    ask(service, MISHEARD, sid)
    no = ask(service, "no", sid)
    assert no["answer"] == f"En «Training Setup Guideline» no identifiqué esa sección. Tiene: {SECTIONS}. ¿Cuál quieres?"
    assert "REQUIRED PERIPHERALS» (" not in ask(service, "sí", sid)["answer"]


def test_another_message_drops_the_pending_suggestion():
    service = build_service(Insufficient())
    sid = session()
    ask(service, MISHEARD, sid)
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    assert "REQUIRED PERIPHERALS» (" not in ask(service, "correcto", sid)["answer"]


def test_named_document_without_a_close_section_lists_them_without_a_suggestion():
    service = build_service(Insufficient())
    sid = session()
    result = ask(service, "Según el manual de Training Setup Guideline, ¿qué dice la sección de contabilidad?", sid)
    assert result["answer"] == f"En «Training Setup Guideline» no identifiqué esa sección. Tiene: {SECTIONS}. ¿Cuál quieres?"
    assert "pending_section" not in service.sessions.get(sid)[1]  # nothing to confirm


@pytest.mark.parametrize("message", [
    "Según el manual de Training Setup Guideline, ¿cuántos técnicos atienden?",       # not a reading question
    "Según el manual de soporte VPN Phase1B, ¿qué dice la sección de contraseñas?",   # no titled sections
])
def test_no_sections_list_for_other_questions_or_documents_without_titled_sections(message):
    knowledge = Knowledge()
    result = ask(build_service(knowledge), message, session())
    assert "no identifiqué esa sección" not in result["answer"] and len(knowledge.queries) == 1


def test_active_document_offers_sections_only_with_a_close_suggestion():
    service = build_service(Knowledge())
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    unrelated = ask(service, "¿Qué dice el clima de hoy?", sid)
    assert "no identifiqué esa sección" not in unrelated["answer"]
    close = ask(service, "¿Qué dice Periféricos Perferinos?", sid)
    assert "¿Te refieres a «REQUIRED PERIPHERALS»?" in close["answer"]
    assert ask(service, "ese", sid)["answer"].startswith("«REQUIRED PERIPHERALS» (2 pasos):")


def test_spanish_alias_in_the_question_is_read_from_the_document():
    knowledge = Insufficient()
    result = ask(build_service(knowledge),
                 "Según el manual de Training Setup Guideline, qué dice periféricos requeridos", session())
    assert result["answer"].startswith("«REQUIRED PERIPHERALS» (2 pasos):") and knowledge.queries == []


@pytest.mark.parametrize(("message", "reply"), [
    ("sí", "yes"), ("Si", "yes"), ("correcto", "yes"), ("ese", "yes"), ("Sí, ese.", "yes"), ("exacto", "yes"),
    ("no", "no"), ("No, otro", "no"), ("ninguno", "no"), ("ese no", "no"),
    ("sí, pero de COMPUTER PREPARATION", None), ("¿qué dice APPS INSTALLATION?", None), ("", None),
])
def test_section_replies(message, reply):
    assert _section_reply(message) == reply


def test_speech_of_the_sections_answer_is_short_and_does_not_list_them():
    answer = (f"En «Training Setup Guideline» no identifiqué esa sección. ¿Te refieres a «REQUIRED PERIPHERALS»? "
              f"Tiene: {SECTIONS}. ¿Cuál quieres?")
    assert spoken_summary(answer) == ("En «Training Setup Guideline» no identifiqué esa sección. ¿Te refieres a "
                                      "«REQUIRED PERIPHERALS»? Las 6 secciones están en el chat.")
    plain = f"En «Training Setup Guideline» no identifiqué esa sección. Tiene: {SECTIONS}. ¿Cuál quieres?"
    assert spoken_summary(plain) == ("En «Training Setup Guideline» no identifiqué esa sección. ¿Cuál quieres? "
                                     "Las 6 secciones están en el chat.")
