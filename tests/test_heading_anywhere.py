"""A heading (or its alias) named without a document is found in any authorized document when it is the only one;
a misheard document name with such a heading answers from it with a one-line notice; Spanish document nicknames;
the reserve answer also skips cover pages."""
import logging
import uuid

import pytest

from backend.app.assistant import named
from backend.app.assistant.procedures import is_cover_page
from backend.app.assistant.speech import spoken_summary
from backend.app.knowledge.service import reserve_answer
from backend.app.voice.vocabulary import MAX_CHARS, from_knowledge
from test_named_reading import INDEX_PAGE, SECTION, Insufficient
from test_named_reference import DOCUMENTS, Knowledge, Store, ask, build, index

COVER = "Training setup \nGuideline \nIT DEPARTMENT \nA.GOMEZ12345"
NOTICE = "No encontré un manual llamado «Trading Sarah Guyton»; respondo desde «Training Setup Guideline»."


def session():
    return f"anywhere-{uuid.uuid4()}"


class CopyStore(Store):
    def documents(self, eligible_only=False):
        return super().documents(eligible_only) + [
            {"id": "doc-copy", "title": "Training Copy", "current_revision": 1, "authorized": 1, "withdrawn": 0}]


class TwoCopies(Insufficient):
    """REQUIRED PERIPHERALS exists in two documents."""

    def __init__(self):
        super().__init__()
        self.store = CopyStore()

    def procedure_catalog(self, document_id):
        return super().procedure_catalog("doc-training" if document_id == "doc-copy" else document_id)


# --- A: the only heading in any document -----------------------------------------------------------------------

def test_heading_without_a_document_is_read_from_the_only_document_that_has_it():
    knowledge = Insufficient()
    result = ask(build(knowledge), "¿Qué dice periféricos requeridos?", session())
    assert result["answer"].startswith("«REQUIRED PERIPHERALS» (2 pasos):") and knowledge.queries == []
    assert {s["document_id"] for s in result["sources"]} == {"doc-training"}


def test_heading_of_another_document_than_the_active_one_switches_to_it():
    knowledge = Insufficient()
    service = build(knowledge)
    sid = session()
    ask(service, "Según el procedimiento de reinicio, ¿qué hago primero?", sid)
    result = ask(service, "Muéstrame la instalación de aplicaciones", sid)
    assert result["answer"].startswith("«APPS INSTALLATION» (2 pasos):") and knowledge.queries == []


@pytest.mark.parametrize("message", [
    "¿Qué dice periféricos requeridos y el despliegue de Windows?",   # two headings
    "¿Por qué fallan los periféricos requeridos?",                    # not a reading question
])
def test_two_headings_or_other_questions_use_the_retrieval(message):
    knowledge = Insufficient()
    ask(build(knowledge), message, session())
    assert len(knowledge.queries) == 1


def test_a_heading_repeated_in_two_documents_is_not_chosen():
    knowledge = TwoCopies()
    ask(build(knowledge), "¿Qué dice periféricos requeridos?", session())
    assert len(knowledge.queries) == 1
    two = named.build_index(knowledge.store.documents(), lambda key: knowledge.procedure_catalog(key)["procedures"])
    assert named.find_heading_anywhere("qué dice periféricos requeridos", two) is None
    assert named.find_heading_anywhere("qué dice periféricos requeridos", index()).procedure == "REQUIRED PERIPHERALS"


# --- Decision 1: misheard document name with the only heading in the question ---------------------------------

def test_misheard_document_name_answers_from_the_only_heading_with_a_notice_first():
    knowledge = Insufficient()
    result = ask(build(knowledge), "Según el manual Trading Sarah Guyton, qué dice periféricos requeridos", session())
    assert result["answer"].startswith(NOTICE + "\n«REQUIRED PERIPHERALS» (2 pasos):")
    assert knowledge.queries == [] and result["sources"]
    speech = spoken_summary(result["answer"])
    assert speech.startswith(NOTICE + " «REQUIRED PERIPHERALS» tiene 2 pasos.") and "[Fuente" not in speech


@pytest.mark.parametrize(("message", "answer"), [
    ("Según el manual de Server Rack Guide, cómo se monta un servidor",
     "No encontré un manual llamado «Server Rack Guide» en los documentos autorizados."),
    ("Según el manual Trading Sarah Guyton, ¿por qué fallan los periféricos requeridos?",   # not a reading question
     "No encontré un manual llamado «Trading Sarah Guyton»; lo más cercano es «Training Setup Guideline»."),
])
def test_otherwise_the_not_found_answer_stays(message, answer):
    assert ask(build(Insufficient()), message, session())["answer"] == answer


def test_notice_speech_reads_a_short_answer_whole():
    answer = NOTICE + "\nPaso 1 de «Reinicio de servidor»: Apagar el servidor.\n[Fuente: Procedimiento de Reinicio]"
    assert spoken_summary(answer) == NOTICE + " Paso 1 de «Reinicio de servidor»: Apagar el servidor."


# --- B: Spanish document nicknames -----------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Según el manual de entrenamiento, qué dice periféricos requeridos",
    "Según la guía de capacitación, ¿cuáles son los pasos de COMPUTER PREPARATION?",
])
def test_document_nicknames_name_the_document(message):
    knowledge = Insufficient()
    result = ask(build(knowledge), message, session())
    assert result["answer"].startswith("«") and knowledge.queries == []
    assert {s["document_id"] for s in result["sources"]} == {"doc-training"}


def test_nicknames_match_only_whole_and_are_never_the_closest_name():
    assert named.resolve("entrenamiento", index()).document_title == "Training Setup Guideline"
    assert named.resolve("capacitación", index()).document_title == "Training Setup Guideline"
    other = named.resolve("capacitación de ventas", index())
    assert other.status == "missing" and other.nearest not in {"manual de entrenamiento", "guía de capacitación"}
    assert named.nicknames_of("Training Setup Guideline") == ("manual de entrenamiento", "guía de capacitación")
    assert named.nicknames_of("Procedimiento de Reinicio") == ()


def test_vocabulary_puts_nicknames_before_the_heading_aliases():
    vocabulary = from_knowledge(Knowledge(), aliases=True, nicknames=True)
    hint = vocabulary.hint
    assert hint.index("manual de entrenamiento") < hint.index("guía de capacitación") < hint.index(
        "preparación de la computadora")
    assert from_knowledge(Knowledge(), aliases=True).hint == from_knowledge(Knowledge(), aliases=True).hint
    assert "manual de entrenamiento" not in from_knowledge(Knowledge(), aliases=True).hint
    assert vocabulary.nicknames == 2 and vocabulary.stats()["chars"] <= MAX_CHARS  # aliases cut first at the cap
    # Nicknames are Spanish words Whisper hears well: not correction targets (they would compete with the titles).
    assert "manual de entrenamiento" not in [t.text for t in vocabulary.candidates]


# --- D: cover pages in the reserve answer ----------------------------------------------------------------------

def test_cover_pages_are_recognized():
    assert is_cover_page(COVER, 1)
    assert not is_cover_page(COVER, 2) and not is_cover_page(COVER, None)
    assert not is_cover_page("Procedimiento 10: Reinicio de servidor\nPasos: 1. Apagar el servidor.", 1)
    assert not is_cover_page("Guía rápida\nConecta el equipo a la red.", 1)
    assert not is_cover_page("Título\n1. Primer paso", 1)
    assert not is_cover_page("", 1)


def test_reserve_answer_skips_cover_and_index_pages_and_is_never_empty(caplog):
    with caplog.at_level(logging.INFO, logger="knowledge"):
        answer = reserve_answer([{"text": COVER, "page": 1}, {"text": INDEX_PAGE, "page": 2},
                                 {"text": SECTION, "page": 14}], "no_generator")
    assert answer == "Según la documentación autorizada:\n\n" + SECTION
    assert "rows=1 index_pages_dropped=1 cover_pages_dropped=1" in caplog.text and "GOMEZ" not in caplog.text
    assert reserve_answer([{"text": COVER, "page": 1}], "no_generator").endswith(COVER)
