"""Tests for the grounded, session-aware unified assistant service."""
import asyncio

import pytest

from backend.app.assistant.service import UnifiedAssistantService, match_procedures
from backend.app.assistant.procedures import flatten


CATALOG = {
    "document": {"id": "doc-1", "title": "Training Setup Guideline", "authorized": True},
    "procedures": [
        {
            "number": 1,
            "title": "WINDOWS DEPLOYMENT PROCEDURE",
            "steps": [
                {"number": 1, "text": "Connect the computer to Centris Network by ethernet cable.",
                 "substeps": [], "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
                {"number": 2, "text": "Then turn off the computer. Wait until confirm the computer is off.",
                 "substeps": [], "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
                {"number": 3, "text": "Then press the power on button.",
                 "substeps": [], "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
            ],
        },
        {
            "number": 2,
            "title": "COMPUTER PREPARATION",
            "steps": [
                {"number": 1, "text": "All ports communication",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 2, "text": "Keyboard (For laptops)",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 3, "text": "BIOS battery status",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 4, "text": "RAM memory of 16GB",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 5, "text": "Installation of SSD (it also could be NVMe)",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 6, "text": "Motherboard cleaning with isopropyl alcohol",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 7, "text": "Fan cleaning",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
                {"number": 8, "text": "Thermal paste replacement on processor",
                 "substeps": [], "procedure": "COMPUTER PREPARATION"},
            ],
        },
    ],
}


class StubProvider:
    """Provider that returns a canned answer (or raises)."""

    def __init__(self, answer="TRADUCCION", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    async def generate(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        return self.answer


class StubKnowledge:
    """Minimal knowledge service exposing only what the assistant uses."""

    def __init__(self, answer="respuesta del servicio de conocimiento", catalog=CATALOG,
                 sources=None, status="answered"):
        self.answer = answer
        self.catalog = catalog
        self.sources = sources if sources is not None else [
            {"title": "Training Setup Guideline", "document_id": "doc-1", "page": 3,
             "version": "1", "revision": 1, "filename": "Training Setup Guideline.pdf",
             "citation_id": "cit-1"},
        ]
        self.status = status
        self.queries = []

    def procedure_catalog(self, document_id):
        return self.catalog

    async def query(self, request):
        self.queries.append(request)
        from backend.app.knowledge.models import KnowledgeAnswer
        return KnowledgeAnswer(status=self.status, answer=self.answer,
                               conversation_id="00000000-0000-0000-0000-000000000000", intent="steps", options=[],
                               sources=self.sources, documentation_sufficient=True,
                               retrieval={})


def build(provider=None, knowledge=None):
    return UnifiedAssistantService(knowledge or StubKnowledge(), provider or StubProvider())


def ask(service, message, session_id=None):
    return asyncio.run(service.chat(message, session_id=session_id))


# --------------------------------------------------------------------- matching
def test_match_procedures_is_deterministic_and_prefers_longest_title():
    procedures = CATALOG["procedures"]
    matched = match_procedures(procedures, "pasos de WINDOWS DEPLOYMENT PROCEDURE")
    assert matched[0]["title"] == "WINDOWS DEPLOYMENT PROCEDURE"
    assert match_procedures(procedures, "tema no documentado") == []


def test_match_procedures_requires_every_title_word():
    procedures = CATALOG["procedures"]
    assert match_procedures(procedures, "WINDOWS DEPLOYMENT") == []


# -------------------------------------------------------------------- grounding
def test_translation_markers_are_rejected_and_original_is_kept():
    service = build(StubProvider("Explicacion: esto es una explicacion con ejemplos."))
    state = {"translations": {}}
    result = asyncio.run(service._translate(state, "Connect the computer by ethernet cable.", ""))
    assert result == "Connect the computer by ethernet cable."


def test_translation_may_not_change_numbers():
    service = build(StubProvider("Conecte el cable de 32 gigabits."))
    state = {"translations": {}}
    result = asyncio.run(service._translate(state, "RAM memory of 16GB", ""))
    assert result == "RAM memory of 16GB"



# ------------------------------------------------------------------- navigation
def test_ordinal_navigation_and_follow_up_moves_forward_and_back():
    service = build()
    sid = "nav-session"
    first = ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    assert "WINDOWS DEPLOYMENT PROCEDURE" in first["answer"]
    ordinal = ask(service, "explícame el segundo paso", sid)
    assert "Paso 2" in ordinal["answer"]
    following = ask(service, "siguiente", sid)
    assert "Paso 3" in following["answer"]
    previous = ask(service, "anterior", sid)
    assert "Paso 2" in previous["answer"]


def test_ordinal_beyond_range_is_reported_without_fabrication():
    service = build()
    sid = "range-session"
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    answer = ask(service, "explícame el paso 99", sid)
    assert "No existe el paso 99" in answer["answer"]
    assert "3" in answer["answer"]


def test_adjacent_procedures_on_same_page_use_the_requested_procedure():
    # Computer Preparation (one numbered step whose a-h checks are eight ordered
    # steps) is immediately followed on the same page by Windows Deployment
    # Procedure. The conversation must stay anchored to the requested procedure.
    catalog = {
        "document": {"id": "doc-1", "title": "Training Setup Guideline", "authorized": True},
        "procedures": [
            {
                "number": 1,
                "title": "COMPUTER PREPARATION",
                "steps": [
                    {"number": 1, "text": "Confirm the hardware functionality",
                     "substeps": [
                         "Check all network ports.",
                         "Confirm the keyboard and input devices are connected.",
                         "Confirm the mouse is operational.",
                         "RAM memory of 16GB.",
                         "Confirm the video card is seated.",
                         "Verify cooling is active.",
                         "Confirm the logical drives are visible.",
                         "Save the configuration and continue."],
                     "procedure": "COMPUTER PREPARATION"},
                ],
            },
            {
                "number": 2,
                "title": "WINDOWS DEPLOYMENT PROCEDURE",
                "steps": [
                    {"number": 1, "text": "Connect the computer to Centris Network by ethernet cable.",
                     "substeps": [], "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
                    {"number": 2, "text": "Then turn off the computer.", "substeps": [],
                     "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
                    {"number": 3, "text": "Then press the power on button.",
                     "substeps": [], "procedure": "WINDOWS DEPLOYMENT PROCEDURE"},
                ],
            },
        ],
    }
    service = build(knowledge=StubStepsKnowledge(catalog=catalog))
    sid = "adjacent-session"
    first = ask(service, "¿Cuáles son los 8 pasos de Computer Preparation?", sid)
    assert "RAM memory of 16GB" in first["answer"]
    # The follow-up must stay in COMPUTER PREPARATION, not WINDOWS DEPLOYMENT
    # PROCEDURE, even though both sections sit on the same page.
    fourth = ask(service, "Explícame el cuarto paso", sid)
    assert "Paso 4 de «COMPUTER PREPARATION»" in fourth["answer"]
    assert "RAM memory of 16GB" in fourth["answer"]
    # Referencing the neighbouring procedure explicitly switches context.
    switched = ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    assert "WINDOWS DEPLOYMENT PROCEDURE" in switched["answer"]
    next_step = ask(service, "explícame el segundo paso", sid)
    assert "Paso 2 de «WINDOWS DEPLOYMENT PROCEDURE»" in next_step["answer"]


class StubStepsKnowledge(StubKnowledge):
    """Knowledge service that answers with the steps read from a procedure catalog."""

    def __init__(self, catalog, **kwargs):
        super().__init__(**kwargs)
        self.catalog = catalog

    async def query(self, request):
        self.queries.append(request)
        from backend.app.knowledge.models import KnowledgeAnswer
        import uuid
        title = (request.message or "").lower()
        procedure = next((p for p in self.catalog["procedures"]
                          if p["title"].lower() in title), None)
        if procedure is None:
            answer = super().query(request).answer
        else:
            steps = []
            for step in procedure["steps"]:
                piece = step.get("text") or ""
                if step.get("substeps"):
                    piece += " " + "; ".join(step["substeps"])
                steps.append(piece)
            answer = "\n".join(steps)
        return KnowledgeAnswer(status="answered", answer=answer,
                               conversation_id=str(uuid.uuid4()), intent="steps",
                               sources=self.sources, documentation_sufficient=True,
                               retrieval={})


def test_lettered_substeps_become_navigable_steps():
    service = build()
    sid = "substeps-session"
    answer = ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    assert "RAM memory of 16GB" in answer["answer"]
    step = ask(service, "explícame el paso 4", sid)
    assert "Paso 4" in step["answer"]
    assert "RAM memory of 16GB" in step["answer"]


# --------------------------------------------------------------------- session
def test_sessions_are_isolated():
    service = build()
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", session_id="a")
    other = ask(service, "explícame el segundo paso", session_id="b")
    assert "No pude verificar" in other["answer"] or "procedimiento" in other["answer"].lower()
    first = ask(service, "explícame el segundo paso", session_id="a")
    assert "Paso 2" in first["answer"]


def test_social_message_does_not_break_procedure_state():
    service = build()
    sid = "social-session"
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    social = ask(service, "gracias", sid)
    assert social["route"] == "social"
    step = ask(service, "explícame el segundo paso", sid)
    assert "Paso 2" in step["answer"]


def test_document_reference_is_answered_from_session_state():
    service = build()
    sid = "doc-session"
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    answer = ask(service, "¿En qué documento aparece este procedimiento?", sid)
    assert "Training Setup Guideline" in answer["answer"]
    assert "página 3" in answer["answer"]


def test_social_greeting_does_not_query_the_knowledge_service():
    knowledge = StubKnowledge()
    service = build(knowledge=knowledge)
    ask(service, "hola")
    assert knowledge.queries == []


def test_unverifiable_request_is_refused_without_inventing_a_procedure():
    knowledge = StubKnowledge(answer="No pude verificar ese procedimiento en los manuales "
                                     "y documentos autorizados disponibles. No puedo afirmar "
                                     "un procedimiento de la empresa sin esa evidencia.",
                              status="insufficient_evidence")
    service = build(knowledge=knowledge)
    answer = ask(service, "menciona un comando destructive inexistente")
    assert "No pude verificar" in answer["answer"]
    assert "destructive" not in answer["answer"]


def test_flatten_keeps_document_order():
    procedures = CATALOG["procedures"]
    steps = flatten(procedures)
    assert [step["text"] for step in steps][:3] == [
        "Connect the computer to Centris Network by ethernet cable.",
        "Then turn off the computer. Wait until confirm the computer is off.",
        "Then press the power on button.",
    ]


# ------------------------------------------------------------- general route
def test_conceptual_question_uses_the_general_provider():
    provider = StubProvider("Un switch de red conecta dispositivos.")
    knowledge = StubKnowledge()
    service = build(provider=provider, knowledge=knowledge)
    result = ask(service, "¿Qué es un switch de red?")
    assert result["route"] == "general"
    assert result["answer"] == "Un switch de red conecta dispositivos."
    assert result["sources"] == []
    # The grounded pipeline is not consulted for an ordinary question.
    assert knowledge.queries == []


@pytest.mark.parametrize("message", [
    "¿Qué es una VPN?",
    "explícame qué es un firewall",
    "diferencia entre RAID 0 y RAID 1",
    "what is a subnet?",
    "how does DHCP work?",
])
def test_conceptual_questions_are_routed_to_general(message):
    service = build(StubProvider("RESPUESTA GENERAL"))
    result = ask(service, message)
    assert result["route"] == "general"
    assert result["answer"] == "RESPUESTA GENERAL"


def test_documented_topic_stays_in_the_knowledge_route():
    knowledge = StubKnowledge()
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge=knowledge)
    result = ask(service, "¿Qué es el procedimiento de despliegue de Windows?")
    assert result["route"] == "knowledge"
    assert knowledge.queries != []


def test_general_provider_failure_does_not_crash_the_chat():
    service = build(StubProvider(error=RuntimeError("model down")))
    result = ask(service, "¿Qué es un switch de red?")
    assert result["route"] == "general"
    assert "no está disponible" in result["answer"]


def test_insufficient_evidence_on_an_ordinary_question_falls_back_to_general():
    knowledge = StubKnowledge(
        answer="No encontré información sobre ese tema en los manuales y "
               "documentos disponibles. Puedes consultar el Chat general para "
               "obtener una explicación más amplia.",
        status="insufficient_evidence")
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge=knowledge)
    result = ask(service, "hablame sobre el protocolo BGP")
    assert result["route"] == "general"
    assert result["answer"] == "RESPUESTA GENERAL"


def test_company_refusal_is_preserved_and_not_replaced_by_general():
    knowledge = StubKnowledge(
        answer="No pude verificar ese procedimiento en los manuales y documentos "
               "autorizados disponibles. No puedo afirmar un procedimiento de la "
               "empresa sin esa evidencia.",
        status="insufficient_evidence")
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge=knowledge)
    result = ask(service, "menciona un comando destructive inexistente")
    assert result["route"] == "knowledge"
    assert "No pude verificar" in result["answer"]
    assert "RESPUESTA GENERAL" not in result["answer"]


def test_general_route_keeps_the_conversation_memory():
    service = build(StubProvider("RESPUESTA GENERAL"))
    ask(service, "¿Qué es un switch de red?", session_id="g")
    follow_up = ask(service, "y el firewall?", session_id="g")
    assert follow_up["route"] == "general"
    assert service.sessions._sessions["g"]["history"]


def test_general_route_does_not_break_procedure_continuity():
    service = build(StubProvider("RESPUESTA GENERAL"))
    sid = "mixed"
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    general = ask(service, "¿Qué es un switch de red?", sid)
    assert general["route"] == "general"
    step = ask(service, "explícame el segundo paso", sid)
    assert step["route"] == "knowledge"
    assert "Paso 2" in step["answer"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

def test_provider_failure_falls_back_to_original_text():
    service = build(StubProvider(error=RuntimeError("model down")))
    state = {"translations": {}}
    result = asyncio.run(service._translate(state, "Press the power on button.", ""))
    assert result == "Press the power on button."


def test_answer_step_always_cites_the_source():
    service = build(StubProvider("Presione el boton de encendido."))
    step = {"number": 1, "text": "Press the power on button.", "substeps": [],
            "procedure": "WINDOWS DEPLOYMENT PROCEDURE",
            "source": {"title": "Training Setup Guideline", "page": 3, "version": "1"}}
    answer = asyncio.run(service._answer_step({"active_document": "Training Setup Guideline"}, step, 1))
    assert "Paso 1" in answer
    assert "[Fuente: Training Setup Guideline, página 3, versión 1" in answer
    # The literal authorized text is offered next to the translation.
    assert "Texto del documento: Press the power on button." in answer


def test_steps_are_not_taken_from_the_model():
    """A model that invents steps must not change the documented step list."""
    service = build(StubProvider("Este es un paso inventado."))
    answer = ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?")
    assert "Connect the computer to Centris Network by ethernet cable." in answer["answer"]
    assert "Paso 4" not in answer["answer"]


# --------------------------------------------------------------- guard tests
VALID_SPANISH = [
    ("Connect the computer to Centris Network by ethernet cable.",
     "Conecte la computadora a la red Centris Network por cable Ethernet."),
    ("Then turn off the computer. Wait until confirm the computer is off.",
     "Luego apague la computadora. Espere hasta confirmar que la computadora este apagada."),
    ("Select IPV4 option.", "Seleccione la opcion IPV4."),
    ("Confirm the hardware functionality", "Verifica la funcionalidad del hardware"),
]


@pytest.mark.parametrize("source,translation", VALID_SPANISH)
def test_valid_spanish_translations_are_accepted(source, translation):
    assert UnifiedAssistantService._clean_translation(source, translation) == translation


def test_inverted_polarity_is_rejected():
    source = "Then turn off the computer."
    bad = "Luego encienda la computadora."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_inverted_polarity_detects_spanish_source():
    source = "Luego apague la computadora."
    bad = "Then turn on the computer."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_invented_identifier_is_rejected():
    source = "Connect the computer to Centris Network by ethernet cable."
    bad = "Conecte la computadora a la red Centris Network por cable Ethernet de 16 gigabits."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_invented_proper_noun_is_rejected():
    source = "Select IPV4 option."
    bad = "Seleccione la opcion IPV4 en el panel de Foobar Tools."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_extra_sentence_is_rejected():
    source = "Press the power on button."
    bad = "Presione el boton de encendido. Luego espere treinta segundos."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_extra_instruction_is_rejected():
    source = "Select IPV4 option."
    bad = "Seleccione la opcion IPV4. A continuacion, ejecute el comando ping."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_changed_number_is_rejected():
    source = "Move the computer in AD from Newly Added file to its corresponding file."
    bad = "Mueve el computador en AD desde el archivo Recien Agregado hasta su archivo correspondiente 5."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_commentary_is_rejected():
    source = "Select IPV4 option."
    bad = "Explicacion: Seleccione la opcion IPV4."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_invented_detail_is_rejected():
    source = "Connect the computer to Centris Network by ethernet cable."
    bad = "Conecte la computadora al puerto Ethernet de la red Centris Network."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_invented_coloured_socket_is_rejected():
    source = "Connect the computer to Centris Network by ethernet cable."
    bad = "Enchufe el cable Ethernet de red en la toma amarilla y espere."
    assert UnifiedAssistantService._clean_translation(source, bad) == source


def test_detail_present_in_source_is_accepted():
    source = "Connect the computer to the port labeled yellow."
    translation = "Conecte la computadora al puerto etiquetado como amarillo."
    assert UnifiedAssistantService._clean_translation(source, translation) == translation


def test_quoted_translation_is_accepted_when_source_is_quoted():
    source = "Confirm the probe «Inline Networks Support Remote» is running."
    translation = "Confirma que el probe «Soporte remoto de Inline Networks» está ejecutándose."
    assert UnifiedAssistantService._clean_translation(source, translation) == translation


def test_quoted_translation_is_rejected_when_source_is_not_quoted():
    source = "Select IPV4 option."
    translation = 'Seleccione la opción "IPV4" en el panel.'
    assert UnifiedAssistantService._clean_translation(source, translation) == source


