"""A named procedure that was parsed is read straight from the document (no retrieval), headings are found also
misspelled or in Spanish, the reserve answer skips index pages and long answers carry a short ``speech``."""
import asyncio
import logging
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.assistant import named
from backend.app.assistant.procedures import is_index_page, parse_document
from backend.app.assistant.references import named_target
from backend.app.assistant.service import UnifiedAssistantService
from backend.app.assistant.speech import REST, spoken_summary
from backend.app.knowledge.models import KnowledgeAnswer
from backend.app.knowledge.service import KnowledgeService, reserve_answer
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from test_assistant_service import StubProvider, StubWebSearch
from test_named_reference import PHRASES, Knowledge, ask, build, index

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "storage" / "knowledge" / "knowledge.sqlite3"
INDEX_PAGE = ("1 \n \n \nINDEX \n1. COMPUTER PREPARATION…………………………………………... Page 2 \n"
              "2. WINDOWS DEPLOYMENT PROCEDURE ………………………… Page 2 - 4 \n"
              "3. GRAPHENE STATIONS DEPLOYMENT PROCEDURE ……………… Page 5 \n4. REQUIRED PERIPHERALS ……… Page 14 \n")
SECTION = ("13 \n \n4. REQUIRED PERIPHERALS \n1. All computers need the following components to set up a complete "
           "workstation: \na. Monitor \nb. Keyboard \n2. You need to check that all components are labeled.")


class Insufficient(Knowledge):
    """Retrieval that finds nothing, as the phone saw it."""

    async def query(self, request):
        self.queries.append(request)
        return KnowledgeAnswer(status="insufficient_evidence", answer="No pude verificar ese procedimiento.",
                               conversation_id=str(uuid.uuid4()), intent="steps", options=[], sources=[],
                               documentation_sufficient=False, retrieval={})


def session():
    return f"reading-{uuid.uuid4()}"


# --- Deterministic reading -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("message", "expected"), [
    ("Según el procedimiento de reinicio, ¿qué hago primero?", "Paso 1 de «Reinicio de servidor»"),
    ("Según el procedimiento de reinicio, ¿cuál es el paso 3?", "Paso 3 de «Reinicio de servidor»"),
    ("Según el procedimiento de reinicio, ¿cuáles son los pasos?", "«Reinicio de servidor» (3 pasos):"),
    ("¿Qué dice el procedimiento de reinicio?", "«Reinicio de servidor» (3 pasos):"),
    ("Muéstrame el procedimiento de reinicio", "«Reinicio de servidor» (3 pasos):"),
    ("Según el manual Training Setup Guideline, que dice Require Peripherals", "«REQUIRED PERIPHERALS» (2 pasos):"),
    ("Según el manual de Training Setup Guideline, qué dice periféricos requeridos", "«REQUIRED PERIPHERALS»"),
])
def test_named_parsed_procedures_are_read_without_retrieval(message, expected):
    knowledge = Insufficient()
    result = ask(build(knowledge), message, session())
    assert expected in result["answer"], result["answer"]
    assert knowledge.queries == [] and result["route"] == "knowledge" and result["sources"]


def test_other_questions_about_a_named_procedure_use_the_retrieval():
    knowledge = Knowledge()
    result = ask(build(knowledge), "Según el procedimiento de reinicio, ¿cuánto tiempo espero?", session())
    assert len(knowledge.queries) == 1 and "Respuesta documentada de Procedimiento de Reinicio" in result["answer"]


def test_a_citation_that_is_no_longer_valid_falls_back_to_the_retrieval():
    knowledge = Knowledge()
    knowledge.store.evidence = lambda citation_id: None
    ask(build(knowledge), "Según el procedimiento de reinicio, ¿qué hago primero?", session())
    assert len(knowledge.queries) == 1  # the retrieval was used


def test_misspelled_heading_of_the_active_document_is_read():
    knowledge = Knowledge()
    service = build(knowledge)
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    queries = len(knowledge.queries)
    result = ask(service, "¿Qué dice Require Peripherals?", sid)
    assert "«REQUIRED PERIPHERALS» (2 pasos):" in result["answer"] and len(knowledge.queries) == queries


def test_exact_heading_matches_keep_their_current_behaviour():
    service = build()
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    assert "Paso 4" in ask(service, "explícame el paso 4", sid)["answer"]


# --- Headings, glossary and the closest name ------------------------------------------------------------------

HEADINGS = ["COMPUTER PREPARATION", "WINDOWS DEPLOYMENT PROCEDURE", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE",
            "REQUIRED PERIPHERALS", "APPS INSTALLATION", "CONFIRMS THE CPU FRECUENCY", "Reinicio de servidor"]


@pytest.mark.parametrize(("message", "heading"), [
    ("que dice Require Peripherals", "REQUIRED PERIPHERALS"),
    ("qué dice Required Peripheral", "REQUIRED PERIPHERALS"),
    ("los periféricos requeridos", "REQUIRED PERIPHERALS"),
    ("cómo es el despliegue de estaciones Graphene", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE"),
    ("la instalación de aplicaciones", "APPS INSTALLATION"),
    ("la preparación del equipo", "COMPUTER PREPARATION"),
    ("la preparación de la computadora", "COMPUTER PREPARATION"),
    ("reinicio del equipo", None),             # one shared word never selects a heading
    ("el equipo no enciende", None),
    ("instalación paso a paso", None),
])
def test_find_heading_covers_the_whole_heading(message, heading):
    found = named.find_heading(message, HEADINGS)
    assert (found[0] if found else None) == heading


@pytest.mark.parametrize(("message", "heading"), [
    (PHRASES[0][0], "COMPUTER PREPARATION"), (PHRASES[1][0], "WINDOWS DEPLOYMENT PROCEDURE"),
    (PHRASES[2][0], "REQUIRED PERIPHERALS"), (PHRASES[3][0], "GRAPHENE STATIONS DEPLOYMENT PROCEDURE"),
    *((text, None) for text, _ in PHRASES[4:]),
])
def test_recorded_questions_find_only_the_headings_they_name(message, heading):
    found = named.find_heading(message, HEADINGS)
    assert (found[0] if found else None) == heading


@pytest.mark.parametrize(("spanish", "english"), [
    ("requeridos", "required"), ("requerido", "required"), ("estaciones", "station"), ("estación", "station"),
    ("aplicaciones", "app"), ("aplicación", "application"), ("computadora", "computer"), ("equipo", "computer"),
    ("equipos", "computer"), ("despliegue", "deployment"), ("instalación", "installation"),
    ("periféricos", "peripheral"), ("preparación", "preparation"), ("reinicio", "restart"),
    ("frecuencia", "frequency"), ("implementación", "deployment"),
])
def test_glossary_entries_in_singular_and_plural(spanish, english):
    (token,) = named.content_tokens(spanish)
    assert english in named.GLOSSARY[token]


def test_equipo_keeps_reinicio_and_computer_preparation_apart():
    assert named.resolve("reinicio del equipo", index()).document_title == "Procedimiento de Reinicio"
    resolution = named.resolve("preparación del equipo", index())
    assert (resolution.document_title, resolution.procedure) == ("Training Setup Guideline", "COMPUTER PREPARATION")


def test_manual_without_de_and_a_capitalized_title_is_a_name():
    assert named_target("Según el manual Training Setup Guideline, que dice Require Peripherals") == \
        {"noun": "manual", "name": "Training Setup Guideline", "feminine": False}
    assert named_target("el procedimiento tiene tres pasos") is None


def test_misheard_name_offers_the_document_of_the_heading_it_mentions():
    message = "Según el manual de Terry D. Sarah Diamond, qué dice REQUIRED PERIPHERALS"
    result = ask(build(), message, session())
    assert result["answer"] == "No encontré un manual llamado «Terry D»; lo más cercano es «Training Setup Guideline»."
    other = ask(build(), "Según el manual de Server, Rack, Gally, cómo se monta", session())
    assert other["answer"] == "No encontré un manual llamado «Server» en los documentos autorizados."


# --- Reserve answer without index pages ------------------------------------------------------------------------

def test_index_pages_are_recognized():
    assert is_index_page(INDEX_PAGE) and not is_index_page(SECTION)
    assert not is_index_page("Procedimiento 10: Reinicio de servidor\nPasos: 1. Apagar el servidor.")
    assert not is_index_page("") and not is_index_page("12\n13")


def test_reserve_answer_skips_index_pages_and_logs_counts_only(caplog):
    with caplog.at_level(logging.INFO, logger="knowledge"):
        answer = reserve_answer([{"text": INDEX_PAGE}, {"text": SECTION}], "no_generator")
    assert answer == "Según la documentación autorizada:\n\n" + SECTION
    assert "reason=no_generator rows=1 index_pages_dropped=1" in caplog.text and "PERIPHERALS" not in caplog.text
    assert reserve_answer([{"text": INDEX_PAGE}], "TimeoutError").endswith(INDEX_PAGE)  # never empty


# --- speech ----------------------------------------------------------------------------------------------------

def test_speech_reads_a_short_list_whole_and_a_long_one_in_part():
    short = "«Reinicio de servidor» (3 pasos):\n1. Apagar el servidor.\n2. Esperar 30 segundos.\n" \
            "3. Encender el servidor.\n[Fuente: Procedimiento de Reinicio, procedimiento Reinicio de servidor]"
    assert spoken_summary(short) == ("«Reinicio de servidor» tiene 3 pasos. Paso 1: Apagar el servidor. "
                                     "Paso 2: Esperar 30 segundos. Paso 3: Encender el servidor.")
    long_list = "«WINDOWS DEPLOYMENT PROCEDURE» (10 pasos):\n" + "\n".join(
        f"{n}. Step number {n} with a long explanation of what to do on the computer at this point." for n in range(1, 11))
    speech = spoken_summary(long_list)
    assert speech.startswith("«WINDOWS DEPLOYMENT PROCEDURE» tiene 10 pasos. Paso 1: Step number 1")
    assert "Paso 2:" in speech and "Paso 3:" not in speech and speech.endswith(REST) and len(speech) <= 450


def test_speech_of_a_procedure_summary_drops_substeps_and_uses_the_singular():
    answer = ("El procedimiento «COMPUTER PREPARATION» tiene 1 paso: 1) Confirm the hardware functionality "
              "(incluye: All ports communication; Keyboard). [Fuente: Training Setup Guideline]")
    assert spoken_summary(answer) == "«COMPUTER PREPARATION» tiene 1 paso. Paso 1: Confirm the hardware functionality."


def test_speech_of_other_long_answers_is_the_first_sentences_without_sources():
    answer = ("Primera frase con contenido útil. " * 20 + "\nVer https://example.com/guia "
              "[Fuente: Manual]\n\nFuentes consultadas:\n- Manual de redes")
    speech = spoken_summary(answer)
    assert speech.endswith(REST) and len(speech) <= 300 + len(REST) + 1
    assert "http" not in speech and "Fuente" not in speech
    assert spoken_summary("Respuesta corta. [Fuente: Manual]") is None


def test_assistant_route_returns_speech_only_for_long_answers():
    class Assistant:
        def __init__(self, answer):
            self.answer = answer

        async def chat(self, message, session_id=None):
            return {"answer": self.answer, "session_id": "s", "route": "knowledge", "sources": []}

    app = create_app(MockLLM(), knowledge=Knowledge())
    with TestClient(app) as client:
        app.state.assistant = Assistant("«Reinicio de servidor» (3 pasos):\n1. Apagar.\n2. Esperar.\n3. Encender.")
        listed = client.post("/api/v1/assistant/chat", json={"message": "hola"}).json()
        app.state.assistant = Assistant("Respuesta corta.")
        short = client.post("/api/v1/assistant/chat", json={"message": "hola"}).json()
    assert listed["speech"] == "«Reinicio de servidor» tiene 3 pasos. Paso 1: Apagar. Paso 2: Esperar. Paso 3: Encender."
    assert short["speech"] is None and short["answer"] == "Respuesta corta."


# --- The real knowledge base, read-only ------------------------------------------------------------------------

class ReadOnlyKnowledge:
    """storage/knowledge/knowledge.sqlite3 opened read-only and immutable; retrieval finds nothing (the phone)."""

    _DOCUMENTS = """SELECT d.*, r.version, r.filename FROM documents d JOIN revisions r ON r.document_id=d.id
        AND r.revision=d.current_revision WHERE d.authorized=1 AND d.withdrawn=0"""
    _CHUNKS = """SELECT c.*, d.title, d.scope, d.procedure_id, d.procedure_key, r.version, r.sha256, r.filename
        FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_revision=c.revision
        JOIN revisions r ON r.document_id=d.id AND r.revision=c.revision
        WHERE d.authorized=1 AND d.withdrawn=0"""

    def __init__(self, path):
        self.uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
        self.queries = []
        self.store = self
        self.source = KnowledgeService.source

    def _rows(self, sql, parameters=()):
        with closing(sqlite3.connect(self.uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, parameters)]

    def documents(self, eligible_only=False):
        return self._rows(self._DOCUMENTS)

    def evidence(self, identifier):
        rows = self._rows(self._CHUNKS + " AND c.id=?", (identifier,))
        return rows[0] if rows else None

    def procedure_catalog(self, document_id):
        chunks = self._rows(self._CHUNKS + " AND c.document_id=? ORDER BY COALESCE(c.page,0), "
                                           "COALESCE(c.paragraph,0), c.offset", (document_id,))
        document = next((d for d in self.documents() if d["id"] == document_id), None)
        return {"document": document, "procedures": parse_document(chunks)} if document else None

    async def query(self, request):
        self.queries.append(request)
        return KnowledgeAnswer(status="insufficient_evidence", answer="No pude verificar ese procedimiento.",
                               conversation_id=str(uuid.uuid4()), intent="steps", options=[], sources=[],
                               documentation_sufficient=False, retrieval={})


def test_phone_questions_against_the_real_knowledge_base_read_only():
    if not DATABASE.is_file():
        pytest.skip("the real knowledge base is not in this checkout")
    knowledge = ReadOnlyKnowledge(DATABASE)
    try:
        titles = {d["title"] for d in knowledge.documents()}
    except sqlite3.Error:
        titles = set()
    if not {"Procedimiento de Reinicio", "Training Setup Guideline"} <= titles:
        pytest.skip("storage/knowledge/knowledge.sqlite3 is not the real knowledge base")
    service = UnifiedAssistantService(knowledge, StubProvider(), web_search=StubWebSearch())
    sid = session()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    first = ask(service, "Según el procedimiento de reinicio, ¿qué hago primero?", sid)
    assert "Paso 1 de «Reinicio de servidor»" in first["answer"] and "Apagar el servidor" in first["answer"]
    assert {s["title"] for s in first["sources"]} == {"Procedimiento de Reinicio"}
    peripherals = ask(service, "Según el manual Training Setup Guideline, que dice Require Peripherals", sid)
    assert peripherals["answer"].startswith("«REQUIRED PERIPHERALS» (5 pasos):")
    assert "INDEX" not in peripherals["answer"] and "Page" not in peripherals["answer"]
    assert spoken_summary(peripherals["answer"]).endswith(REST)
    misheard = ask(service, "Según el manual de Terry D. Sarah Diamond, qué dice REQUIRED PERIPHERALS", sid)
    assert misheard["answer"].endswith("lo más cercano es «Training Setup Guideline».")
    assert len(knowledge.queries) == 1  # only the first question went to the retrieval
