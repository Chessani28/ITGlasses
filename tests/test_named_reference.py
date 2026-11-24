"""A named document or procedure decides the context: "Según el procedimiento de reinicio, ¿qué hago primero?"
after a COMPUTER PREPARATION question must answer from «Procedimiento de Reinicio», and a name that matches no
authorized document must say so ("No encontré…; lo más cercano es…") instead of answering with another one."""
import asyncio
import csv
import re
import uuid
from pathlib import Path

import pytest

from backend.app.assistant import named
from backend.app.assistant.procedures import inline_steps, parse_document
from backend.app.assistant.references import detect_reference, named_target
from backend.app.assistant.service import UnifiedAssistantService
from backend.app.knowledge.models import KnowledgeAnswer
from test_assistant_service import StubProvider, StubWebSearch

ROOT = Path(__file__).resolve().parents[1]

# The real «Procedimiento de Reinicio» chunk: a "Procedimiento N:" heading and its steps on one line.
REINICIO_TEXT = ("Procedimiento 10: Reinicio de servidor\r\n"
                 "Pasos: 1. Apagar el servidor. 2. Esperar 30 segundos. 3. Encender el servidor.")
TRAINING_TEXT = """1. COMPUTER PREPARATION

1. Confirm the hardware functionality
a. All ports communication
b. Keyboard (For laptops)
c. BIOS battery status
d. RAM memory of 16GB
e. Installation of SSD (it also could be NVMe),
f. Motherboard cleaning with isopropyl alcohol
g. Fan cleaning
h. Thermal paste replacement on processor

2. WINDOWS DEPLOYMENT PROCEDURE

1. Connect the computer to Northwind Network by ethernet cable.
2. Then turn off the computer.
3. Then press the power on button.

3. GRAPHENE STATIONS DEPLOYMENT PROCEDURE

1. Then turn off the computer.
2. Once those aspects are confirmed, follow the steps.

4. REQUIRED PERIPHERALS

1. All computers need the following components.
2. You need to check that all components are labeled.

5. APPS INSTALLATION

1. Press Windows key + R to open the Run dialog.
2. Install Google Chrome.

6. CONFIRMS THE CPU FRECUENCY

1. Look for the Command Prompt in the Search bar."""
DOCUMENTS = {
    "doc-fiel": ("FIEL 13-AGO-2026 Acuse_renovacion (18)", "Acuse de renovación."),
    "doc-training": ("Training Setup Guideline", TRAINING_TEXT),
    "doc-reinicio": ("Procedimiento de Reinicio", REINICIO_TEXT),
    "doc-instalacion": ("Guía de Instalación", "## Pasos\n1. Descargar el instalador.\n2. Ejecutar como administrador.\n"
                                              "3. Seguir el asistente de instalación."),
    "doc-emergencia": ("Procedimiento de Emergencia", "Procedimiento de emergencia."),
    "doc-vpn": ("Manual de Soporte VPN Phase1B", "1. Verificar que la conexión a internet esté activa.\n"
                                                 "2. Confirmar que el cliente VPN esté instalado.\n"
                                                 "3. Verificar las credenciales de usuario."),
}

# work/voice-eval/frases.csv (Voz 2): the 12 recorded questions. p09 and p10 name documents that do not exist;
# p11 and p12 name sections by their Spanish alias.
PHRASES = [
    ("Según el manual de Training Setup Guideline, cuáles son los pasos de COMPUTER PREPARATION", True),
    ("En el Training Setup Guideline, cómo es el WINDOWS DEPLOYMENT PROCEDURE", True),
    ("Según el manual de Training Setup Guideline, qué dice REQUIRED PERIPHERALS", True),
    ("Según el Training Setup Guideline, cómo se hace el GRAPHENE STATIONS DEPLOYMENT PROCEDURE", True),
    ("Según el procedimiento de reinicio, qué hago primero", True),
    ("Muéstrame la guía de instalación paso a paso", True),
    ("Según el procedimiento de emergencia, a quién debo llamar", True),
    ("En el manual de soporte VPN Phase1B, cómo configuro la VPN", True),
    ("Según el manual de Server Rack Guide, cómo se monta un servidor", False),
    ("Según la guía de Network Cabling Standards, qué cable uso", False),
    ("Según el manual de Training Setup Guideline, qué dice periféricos requeridos", True),
    ("Según el manual de Training Setup Guideline, cómo es el despliegue de Windows", True),
]


def without_noun(text):
    """The same question without the word "manual"/"guía": "según el manual de X" -> "según el X"."""
    return re.sub(r"\b(el|la)\s+(manual|gu[ií]a)\s+de\s+", r"\1 ", text, count=1, flags=re.IGNORECASE)


VARIANTS = [(without_noun(text), exists) for text, exists in PHRASES if without_noun(text) != text]


class Store:
    def evidence(self, citation_id):
        return bool(citation_id)

    def documents(self, eligible_only=False):
        return [{"id": key, "title": title, "current_revision": 1, "authorized": 1, "withdrawn": 0}
                for key, (title, _) in DOCUMENTS.items()]


class Knowledge:
    """Authorized documents of the real knowledge base (titles and procedures); retrieval picks a document by
    keyword, or the one forced by the test."""

    def __init__(self, force=None):
        self.store = Store()
        self.queries = []
        self.force = force

    def procedure_catalog(self, document_id):
        title, text = DOCUMENTS[document_id]
        chunk = {"text": text, "page": 1, "id": f"cit-{document_id}", "document_id": document_id, "title": title,
                 "version": "1"}
        return {"document": {"id": document_id, "title": title}, "procedures": parse_document([chunk])}

    async def query(self, request):
        self.queries.append(request)
        folded = named.fold(request.message)
        document = self.force or next(
            (key for word, key in (("reinicio", "doc-reinicio"), ("vpn", "doc-vpn"),
                                   ("instalacion", "doc-instalacion"), ("emergencia", "doc-emergencia"))
             if word in folded), "doc-training")
        title = DOCUMENTS[document][0]
        source = {"title": title, "document_id": document, "page": 1, "version": "1", "revision": 1,
                  "citation_id": f"cit-{document}"}
        return KnowledgeAnswer(status="answered", answer=f"Respuesta documentada de {title}.",
                               conversation_id=str(uuid.uuid4()), intent="steps", options=[], sources=[source],
                               documentation_sufficient=True, retrieval={})


def build(knowledge=None, web_search=None):
    return UnifiedAssistantService(knowledge or Knowledge(), StubProvider(), web_search=web_search or StubWebSearch())


def ask(service, message, session_id="named-session"):
    return asyncio.run(service.chat(message, session_id=session_id))


def index():
    knowledge = Knowledge()
    return named.build_index(knowledge.store.documents(eligible_only=True),
                             lambda key: knowledge.procedure_catalog(key)["procedures"])


# --- Name extraction -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("message", "noun", "name"), [
    ("Según el procedimiento de reinicio, ¿qué hago primero?", "procedimiento", "reinicio"),
    ("En el manual de soporte VPN Phase1B, cómo configuro la VPN", "manual", "soporte VPN Phase1B"),
    ("Muéstrame la guía de instalación paso a paso", "guía", "instalación"),
    ("Según la guia de Network Cabling Standards, qué cable uso", "guía", "Network Cabling Standards"),
    ("¿Qué es el procedimiento de despliegue de Windows?", "procedimiento", "despliegue de Windows"),
    ("Según la guía «Guía de Instalación», qué hago", "guía", "Guía de Instalación"),
    ("dime el paso 2 del procedimiento de reinicio", "procedimiento", "reinicio"),
    ("Según el Server Rack Guide, cómo se monta un servidor", "documento", "Server Rack Guide"),
    ("En el Training Setup Guideline, cómo es el WINDOWS DEPLOYMENT PROCEDURE", "documento",
     "Training Setup Guideline"),
])
def test_named_target_extracts_the_noun_and_the_name(message, noun, name):
    assert named_target(message) == {"noun": noun, "name": name, "feminine": noun == "guía"}


@pytest.mark.parametrize("message", [
    "Explícame este procedimiento", "¿Cuál es el procedimiento?", "dame el procedimiento completo",
    "pasos del procedimiento", "No puedo afirmar un procedimiento de la empresa", "explícame el paso 4",
    "En el soporte VPN Phase1B, cómo configuro la VPN", "¿qué hago primero?",
])
def test_anaphoric_or_unnamed_references_are_not_names(message):
    assert named_target(message) is None


def test_what_do_i_do_first_is_step_one():
    for message in ("Según el procedimiento de reinicio, ¿qué hago primero?", "¿Qué debo hacer primero?",
                    "lo primero que hago"):
        assert detect_reference(message) == {"kind": "ordinal", "number": 1, "text": message}
    assert detect_reference("¿qué es primero, el huevo o la gallina?") is None


# --- Parser ----------------------------------------------------------------------------------------------------

def test_inline_steps_and_numbered_heading_are_parsed():
    procedures = parse_document([{"text": REINICIO_TEXT, "page": 1, "id": "c1"}])
    assert [(p["title"], [s["text"] for s in p["steps"]]) for p in procedures] == [
        ("Reinicio de servidor", ["Apagar el servidor.", "Esperar 30 segundos.", "Encender el servidor."])]
    assert procedures[0]["steps"][0]["source"]["citation_id"] == "c1"


@pytest.mark.parametrize("text", ["1. Solo un paso.", "1. Uno. 3. Tres.", "Apagar. 1. Uno. 2. Dos.", "2. Dos. 3. Tres."])
def test_inline_lists_must_be_numbered_from_one(text):
    assert inline_steps(text) == []


def test_other_documents_parse_as_before():
    procedures = parse_document([{"text": TRAINING_TEXT, "page": 1, "id": "c1"}])
    assert [p["title"] for p in procedures] == [
        "COMPUTER PREPARATION", "WINDOWS DEPLOYMENT PROCEDURE", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE",
        "REQUIRED PERIPHERALS", "APPS INSTALLATION", "CONFIRMS THE CPU FRECUENCY"]
    assert len(procedures[0]["steps"]) == 1 and len(procedures[0]["steps"][0]["substeps"]) == 8


# --- Matching --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("name", "document", "procedure"), [
    ("reinicio", "Procedimiento de Reinicio", "Reinicio de servidor"),
    ("reinicio del servidor", "Procedimiento de Reinicio", "Reinicio de servidor"),
    ("soporte VPN Phase1B", "Manual de Soporte VPN Phase1B", None),
    ("soporte VPN", "Manual de Soporte VPN Phase1B", None),
    ("instalación", "Guía de Instalación", None),          # the exact title beats APPS INSTALLATION
    ("emergencias", "Procedimiento de Emergencia", None),
    ("Training Setup Guideline", "Training Setup Guideline", None),
    # Glossary: Spanish words for the English headings.
    ("despliegue de Windows", "Training Setup Guideline", "WINDOWS DEPLOYMENT PROCEDURE"),
    ("preparación de la computadora", "Training Setup Guideline", "COMPUTER PREPARATION"),
    ("periféricos requeridos", "Training Setup Guideline", "REQUIRED PERIPHERALS"),
    ("instalación de aplicaciones", "Training Setup Guideline", "APPS INSTALLATION"),
    ("frecuencia", "Training Setup Guideline", "CONFIRMS THE CPU FRECUENCY"),
    ("despliegue de estaciones Graphene", "Training Setup Guideline", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE"),
])
def test_names_resolve_to_the_authorized_document_and_procedure(name, document, procedure):
    resolution = named.resolve(name, index())
    assert (resolution.status, resolution.document_title, resolution.procedure) == ("found", document, procedure)


def test_glossary_is_small_singular_and_accent_free():
    assert len(named.GLOSSARY) <= 15
    for spanish, english in named.GLOSSARY.items():
        assert spanish == named.fold(spanish) and named.content_tokens(spanish) == (spanish,)
        assert all(named.content_tokens(word) == (word,) for word in english)


@pytest.mark.parametrize(("name", "nearest"), [
    ("Server Rack Guide", None),
    ("Network Cabling Standards", None),
    ("respaldo", None),
    ("usuario de la impresora", None),
    ("arranque del servidor", "Reinicio de servidor"),
])
def test_unknown_names_are_missing_with_at_most_one_closest_name(name, nearest):
    resolution = named.resolve(name, index())
    assert resolution.status == "missing" and resolution.nearest == nearest


def test_not_found_message_uses_the_noun_of_the_question():
    assert named.not_found_message({"noun": "procedimiento", "name": "respaldo", "feminine": False},
                                   "Reinicio de servidor") == \
        "No encontré un procedimiento llamado «respaldo»; lo más cercano es «Reinicio de servidor»."
    assert named.not_found_message({"noun": "guía", "name": "Network Cabling Standards", "feminine": True}, None) == \
        "No encontré una guía llamada «Network Cabling Standards» en los documentos autorizados."


# --- Regression: the recorded questions (frases.csv) and their variants without "manual"/"guía" ---------------

@pytest.mark.parametrize(("message", "exists"), PHRASES + VARIANTS)
def test_recorded_questions_say_not_found_only_for_documents_that_do_not_exist(message, exists):
    answer = ask(build(), message, session_id=f"phrase-{uuid.uuid4()}")["answer"]
    assert answer.startswith("No encontré") is not exists, answer


def test_embedded_phrases_match_frases_csv_when_present():
    path = ROOT / "work" / "voice-eval" / "frases.csv"
    if not path.is_file():
        pytest.skip("work/voice-eval/frases.csv is not in this checkout")
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["reference"], bool(row["title"])) for row in rows] == PHRASES


# --- Conversation ----------------------------------------------------------------------------------------------

def test_reinicio_after_computer_preparation_answers_from_reinicio():
    service = build()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?")
    result = ask(service, "Según el procedimiento de reinicio, ¿qué hago primero?")
    assert "Paso 1 de «Reinicio de servidor»" in result["answer"]
    assert "Apagar el servidor" in result["answer"] and "Procedimiento de Reinicio" in result["answer"]
    assert "COMPUTER PREPARATION" not in result["answer"]
    assert [s["document_id"] for s in result["sources"]] == ["doc-reinicio"]


def test_unknown_name_is_said_and_the_previous_topic_is_not_carried():
    knowledge = Knowledge()
    service = build(knowledge)
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?")
    queries = len(knowledge.queries)
    result = ask(service, "Según el procedimiento de respaldo, ¿qué hago primero?")
    assert result["answer"] == "No encontré un procedimiento llamado «respaldo» en los documentos autorizados. ¿Quieres que te lo explique de forma general? Si existe ese manual, un administrador puede subirlo en Documentos."
    assert result["sources"] == [] and len(knowledge.queries) == queries
    follow_up = ask(service, "¿y el paso 2?")
    assert "COMPUTER PREPARATION" not in follow_up["answer"] and "Keyboard" not in follow_up["answer"]


def test_evidence_from_another_document_is_not_presented_as_the_named_one():
    service = build(Knowledge(force="doc-training"))
    # A question that is not a reading one goes to the retrieval (a reading one is answered from the document).
    result = ask(service, "Según el procedimiento de reinicio, ¿cuánto tiempo espero?")
    assert result["answer"] == ("Encontré «Procedimiento de Reinicio», pero no pude verificar esa respuesta en su "
                                "contenido.")
    assert result["sources"] == []


def test_explicit_internet_requests_keep_the_public_flow():
    result = ask(build(), "Busca en internet la versión más reciente del procedimiento de respaldo")
    assert not result["answer"].startswith("No encontré")


def test_anaphoric_procedure_still_uses_the_session_with_singular_and_substeps():
    knowledge = Knowledge()
    service = build(knowledge)
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?")
    queries = len(knowledge.queries)
    result = ask(service, "¿Cuál es el procedimiento?")
    assert len(knowledge.queries) == queries  # answered from the session
    assert result["answer"].startswith("El procedimiento «COMPUTER PREPARATION» tiene 1 paso: "
                                       "1) Confirm the hardware functionality (incluye: All ports communication; ")
    assert "1 pasos" not in result["answer"] and ".." not in result["answer"]


def test_named_procedure_in_the_active_document_is_anchored():
    service = build()
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?")
    result = ask(service, "Según el procedimiento de despliegue de Windows, ¿cuál es el primer paso?")
    assert "Paso 1 de «WINDOWS DEPLOYMENT PROCEDURE»" in result["answer"]


def test_step_counts_use_the_singular_for_one_step():
    service = build()
    ask(service, "¿Cuáles son los pasos de CONFIRMS THE CPU FRECUENCY?")
    missing = ask(service, "explícame el paso 9")
    assert "1 pasos" not in missing["answer"]
    steps = ask(service, "Según la guía de instalación, ¿cuáles son los pasos?")
    assert "(3 pasos)" in steps["answer"] or "Descargar el instalador" in steps["answer"]
