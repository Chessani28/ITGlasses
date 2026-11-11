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
                 sources=None, status="answered", documentation_sufficient=None):
        self.answer = answer
        self.catalog = catalog
        self.sources = sources if sources is not None else [
            {"title": "Training Setup Guideline", "document_id": "doc-1", "page": 3,
             "version": "1", "revision": 1, "filename": "Training Setup Guideline.pdf",
             "citation_id": "cit-1"},
        ]
        self.status = status
        self.documentation_sufficient = documentation_sufficient
        self.queries = []

    def procedure_catalog(self, document_id):
        return self.catalog

    async def query(self, request):
        self.queries.append(request)
        from backend.app.knowledge.models import KnowledgeAnswer
        sufficient = self.documentation_sufficient
        if sufficient is None:
            sufficient = self.status == "answered" and bool(self.sources)
        return KnowledgeAnswer(status=self.status, answer=self.answer,
                               conversation_id="00000000-0000-0000-0000-000000000000", intent="steps", options=[],
                               sources=self.sources,
                               documentation_sufficient=sufficient,
                               retrieval={})


class StubWebSearch:
    def __init__(self, events=None, results=None, error=None):
        self.calls = []
        self.events = events
        self.results = results
        self.error = error
        self.last_error = None
        self.last_diagnostics = {}

    async def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        if self.events is not None:
            self.events.append("web")
        if self.error:
            self.last_error = self.error
            self.last_diagnostics = {
                "query_category": "vpn",
                "provider": "custom",
                "provider_result_count": 0,
                "basic_validated_count": 0,
                "provider_discard_reasons": {},
                "error_category": getattr(self.error, "category", "provider_unavailable"),
            }
            raise self.error
        from backend.app.assistant.web_search import WebSearchResult
        if self.results is not None:
            results = self.results
        else:
            results = [WebSearchResult(
            title="Microsoft Windows 11 release information",
            url="https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information",
            snippet="Versión actual confirmada por la fuente oficial.",
            retrieved_at="2026-11-09T12:00:00-06:00",
            verified=True,
            )]
        self.last_error = None
        self.last_diagnostics = {
            "query_category": "vpn",
            "provider": "custom",
            "provider_result_count": len(results),
            "basic_validated_count": len(results),
            "provider_discard_reasons": {},
            "error_category": None,
        }
        return results[:max_results]


def build(provider=None, knowledge=None, web_search=None):
    return UnifiedAssistantService(
        knowledge or StubKnowledge(), provider or StubProvider(), web_search=web_search
    )


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
    knowledge = StubKnowledge(
        answer="No encontré evidencia suficiente.",
        status="insufficient_evidence",
    )
    service = build(provider=provider, knowledge=knowledge)
    result = ask(service, "¿Qué es un switch de red?")
    assert result["route"] == "general"
    assert result["answer"] == "Un switch de red conecta dispositivos."
    assert result["sources"] == []
    assert len(knowledge.queries) == 1


@pytest.mark.parametrize("message", [
    "¿Qué es una VPN?",
    "explícame qué es un firewall",
    "diferencia entre RAID 0 y RAID 1",
    "what is a subnet?",
    "how does DHCP work?",
])
def test_conceptual_questions_are_routed_to_general(message):
    knowledge = StubKnowledge(answer="Sin evidencia relevante.", status="insufficient_evidence")
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge)
    result = ask(service, message)
    assert result["route"] == "general"
    assert result["answer"] == "RESPUESTA GENERAL"
    assert knowledge.queries


def test_documented_topic_stays_in_the_knowledge_route():
    knowledge = StubKnowledge()
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge=knowledge)
    result = ask(service, "¿Qué es el procedimiento de despliegue de Windows?")
    assert result["route"] == "knowledge"
    assert knowledge.queries != []


def test_vpn_concept_uses_relevant_manual_evidence_and_cites_that_source():
    source = {"title": "Manual de redes", "document_id": "vpn-doc", "page": 8,
              "version": "3", "citation_id": "vpn-citation"}
    knowledge = StubKnowledge(
        answer="Según el manual de redes, una VPN crea una conexión privada cifrada.",
        sources=[source],
    )
    service = build(StubProvider("RESPUESTA GENERAL INCORRECTA"), knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "knowledge"
    assert result["answer"] == knowledge.answer
    assert result["sources"] == [source]
    assert not service.provider.calls
    assert len(knowledge.queries) == 1


def test_vpn_concept_without_evidence_falls_back_to_local_general_knowledge():
    knowledge = StubKnowledge(
        answer="No encontré información pertinente.",
        status="insufficient_evidence",
    )
    provider = StubProvider("Una VPN crea un túnel de red cifrado.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "general"
    assert result["answer"] == provider.answer
    assert result["sources"] == []
    assert len(knowledge.queries) == 1
    assert len(provider.calls) == 1


def test_manual_only_vpn_question_does_not_fall_back_without_evidence():
    knowledge = StubKnowledge(
        answer="No pude verificar esa información en los manuales autorizados.",
        status="insufficient_evidence",
        sources=[{"title": "Documento irrelevante", "document_id": "private-doc",
                  "page": 2, "citation_id": "unverified-citation"}],
    )
    provider = StubProvider("Respuesta general que no debe mostrarse.")
    service = build(provider, knowledge)

    result = ask(service, "Según mis manuales, ¿qué es una VPN?")

    assert result["route"] == "knowledge"
    assert "No encontré evidencia suficiente" in result["answer"]
    assert "VPN (Red Privada Virtual)" in result["answer"]
    assert "internet" in result["answer"]
    assert "general" in result["answer"]
    assert result["sources"] == []
    assert not provider.calls
    assert len(knowledge.queries) == 1
    assert service.sessions._sessions[result["session_id"]]["pending_alternative"]["search_consent"] is False


def test_answered_but_insufficient_rag_result_does_not_leak_irrelevant_sources():
    knowledge = StubKnowledge(
        answer="Respuesta no verificada",
        sources=[{"title": "Documento no relacionado", "document_id": "other-doc",
                  "page": 1, "citation_id": "irrelevant-citation"}],
        documentation_sufficient=False,
    )
    provider = StubProvider("Respuesta general sin citas documentales.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "general"
    assert result["answer"] == provider.answer
    assert result["sources"] == []


@pytest.mark.parametrize("message", [
    "hola",
    "Hola, ¿cómo estás?",
    "Buenos días, ¿cómo estás?",
    "Gracias, hasta luego",
])
def test_social_messages_are_social_and_skip_rag(message):
    knowledge = StubKnowledge()
    service = build(knowledge=knowledge)

    result = ask(service, message)

    assert result["route"] == "social"
    assert result["sources"] == []
    assert not knowledge.queries


def test_vpn_uses_relevant_authorized_manual_evidence_first():
    source = {"title": "VPN Configuration", "document_id": "vpn-doc", "page": 4,
              "version": "2", "revision": 1, "filename": "VPN.pdf",
              "citation_id": "vpn-citation"}
    knowledge = StubKnowledge(answer="Según el manual, la VPN se configura así.",
                              sources=[source])
    provider = StubProvider("Respuesta general que no debe sustituir el manual.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "knowledge"
    assert result["answer"] == knowledge.answer
    assert result["sources"] == [source]
    assert len(knowledge.queries) == 1
    assert not provider.calls


def test_vpn_without_sufficient_manual_evidence_uses_general_knowledge():
    knowledge = StubKnowledge(answer="No hay evidencia suficiente.", status="insufficient_evidence",
                              sources=[])
    provider = StubProvider("VPN significa red privada virtual.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "general"
    assert result["answer"] == provider.answer
    assert result["sources"] == []
    assert len(knowledge.queries) == 1
    assert len(provider.calls) == 1


def test_non_it_concept_skips_rag():
    knowledge = StubKnowledge()
    provider = StubProvider("Una metáfora compara una idea con otra.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una metáfora?")

    assert result["route"] == "general"
    assert result["answer"] == provider.answer
    assert not knowledge.queries


def test_knowledge_failure_does_not_masquerade_as_insufficient_evidence():
    knowledge = StubKnowledge()

    async def unavailable(request):
        raise RuntimeError("synthetic retrieval failure")

    knowledge.query = unavailable
    provider = StubProvider("Respuesta general que omitiría los manuales.")
    service = build(provider, knowledge)

    result = ask(service, "¿Qué es una VPN?")

    assert result["route"] == "knowledge"
    assert "No se pudo consultar la documentación" in result["answer"]
    assert result["sources"] == []
    assert not provider.calls


def test_time_sensitive_windows_question_uses_web_after_manual_attempt():
    events = []
    knowledge = StubKnowledge(answer="Sin versión actual en los manuales.",
                              status="insufficient_evidence")
    original_query = knowledge.query

    async def record_query(request):
        events.append("knowledge")
        return await original_query(request)

    knowledge.query = record_query
    web_search = StubWebSearch(events)
    provider = StubProvider("Respuesta local desactualizada.")
    service = build(provider, knowledge, web_search)

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert result["route"] == "general"
    assert result["sources"][0]["url"].startswith("https://learn.microsoft.com/")
    assert result["sources"][0]["retrieved_at"] == "2026-11-09T12:00:00-06:00"
    assert "consultada: 2026-11-09T12:00:00-06:00" in result["answer"]
    assert events == ["knowledge", "web"]
    assert web_search.calls[0][0] == "¿Cuál es la versión más reciente de Windows 11?"
    assert not provider.calls


def test_windows_web_answer_is_concise_official_and_not_a_snippet_dump():
    from backend.app.assistant.web_search import WebSearchResult

    official = WebSearchResult(
        title="Windows 11 release information",
        url="https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information",
        snippet="La versión con disponibilidad general más reciente es 26H1.",
        retrieved_at="2026-11-09T12:00:00-06:00",
        verified=True,
    )
    irrelevant = WebSearchResult(
        title="Unofficial result",
        url="https://example.com/windows",
        snippet="RAW SNIPPET THAT MUST NOT BE RETURNED",
    )
    knowledge = StubKnowledge(answer="No hay versión actual en los manuales.",
                              status="insufficient_evidence")
    web_search = StubWebSearch(results=[official, irrelevant])
    provider = StubProvider("Respuesta local desactualizada.")
    service = build(provider, knowledge, web_search)

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert result["route"] == "general"
    assert "26H1" in result["answer"]
    assert "Fuente oficial:" in result["answer"]
    assert "learn.microsoft.com" in result["answer"]
    assert "RAW SNIPPET" not in result["answer"]
    assert "Resultados recuperados de internet" not in result["answer"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["verified"] is True
    assert not provider.calls


def test_windows_web_answer_reports_inconclusive_unverified_results():
    from backend.app.assistant.web_search import WebSearchResult

    unverified = WebSearchResult(
        title="Windows 11 release information",
        url="https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information",
        snippet="Search preview that may be stale.",
        verified=False,
    )
    knowledge = StubKnowledge(answer="Sin versión actual.", status="insufficient_evidence")
    service = build(knowledge=knowledge, web_search=StubWebSearch(results=[unverified]))

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert "No pude confirmar" in result["answer"]
    assert "Search preview" not in result["answer"]
    assert result["sources"][0]["verified"] is False


def test_other_time_sensitive_web_results_are_summarized_not_dumped():
    from backend.app.assistant.web_search import WebSearchResult

    results = [
        WebSearchResult("Exchange release notes", "https://learn.microsoft.com/exchange/release",
                        "RAW EXCHANGE SNIPPET ONE"),
        WebSearchResult("Microsoft 365 updates", "https://learn.microsoft.com/microsoft-365/updates",
                        "RAW EXCHANGE SNIPPET TWO"),
    ]
    knowledge = StubKnowledge(answer="Sin datos actuales.", status="insufficient_evidence")
    provider = StubProvider("Resumen breve basado en las fuentes.")
    service = build(provider, knowledge, StubWebSearch(results=results))

    result = ask(service, "¿Cuál es la última actualización de Exchange?")

    assert result["route"] == "general"
    assert result["answer"].startswith(provider.answer)
    assert "RAW EXCHANGE SNIPPET" not in result["answer"]
    assert "Fuentes consultadas:" in result["answer"]
    assert len(result["sources"]) == 2
    assert "RAW EXCHANGE SNIPPET ONE" in provider.calls[0]


def test_web_search_failure_does_not_fall_back_to_stale_local_answer():
    knowledge = StubKnowledge(answer="Sin versión actual.", status="insufficient_evidence")
    provider = StubProvider("Respuesta posiblemente desactualizada.")
    service = build(provider, knowledge, StubWebSearch(error=RuntimeError("search unavailable")))

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert result["route"] == "general"
    assert "No pude verificar información actual" in result["answer"]
    assert "posiblemente desactualizada" not in result["answer"]
    assert result["sources"] == []
    assert not provider.calls


def test_current_fact_without_web_search_does_not_use_local_general_knowledge():
    knowledge = StubKnowledge(answer="Sin versión actual.", status="insufficient_evidence")
    provider = StubProvider("Respuesta local posiblemente desactualizada.")
    service = build(provider, knowledge, web_search=None)
    service.web_search = None

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert "búsqueda pública no está disponible" in result["answer"]
    assert not provider.calls


def test_time_sensitive_question_does_not_present_manual_evidence_as_current():
    events = []
    knowledge = StubKnowledge(
        answer="La versión indicada en un manual antiguo.",
        sources=[{"title": "Manual Windows", "document_id": "windows-doc",
                  "page": 4, "citation_id": "old-version"}],
    )
    original_query = knowledge.query

    async def record_query(request):
        events.append("knowledge")
        return await original_query(request)

    knowledge.query = record_query
    web_search = StubWebSearch(events)
    service = build(StubProvider("Respuesta local desactualizada."), knowledge, web_search)

    result = ask(service, "¿Cuál es la versión más reciente de Windows 11?")

    assert result["route"] == "general"
    assert "Versión indicada en un manual antiguo" not in result["answer"]
    assert result["sources"][0]["url"].startswith("https://learn.microsoft.com/")
    assert events == ["knowledge", "web"]


def test_general_provider_failure_does_not_crash_the_chat():
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence")
    service = build(StubProvider(error=RuntimeError("model down")), knowledge)
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
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence")
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge)
    ask(service, "¿Qué es un switch de red?", session_id="g")
    follow_up = ask(service, "y el firewall?", session_id="g")
    assert follow_up["route"] == "general"
    assert service.sessions._sessions["g"]["history"]


def test_general_route_does_not_break_procedure_continuity():
    knowledge = StubKnowledge()
    service = build(StubProvider("RESPUESTA GENERAL"), knowledge)
    sid = "mixed"
    ask(service, "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?", sid)
    knowledge.status = "insufficient_evidence"
    general = ask(service, "¿Qué es un switch de red?", sid)
    assert general["route"] == "general"
    step = ask(service, "explícame el segundo paso", sid)
    assert step["route"] == "knowledge"
    assert "Paso 2" in step["answer"]


def _start_manual_alternative(service, question="Según mis manuales, ¿cómo configuro una VPN?",
                              session_id="manual-alternative"):
    result = ask(service, question, session_id)
    assert result["route"] == "knowledge"
    assert "No encontré evidencia suficiente" in result["answer"]
    return result


def test_manual_only_request_with_sufficient_evidence_keeps_documented_answer():
    source = {"title": "VPN Configuration", "document_id": "vpn-doc", "page": 4,
              "version": "2", "revision": 1, "citation_id": "vpn-citation"}
    knowledge = StubKnowledge(answer="Según el manual, conecta el cliente VPN.",
                              sources=[source])
    provider = StubProvider("La alternativa no debe ejecutarse.")
    web = StubWebSearch()
    service = build(provider, knowledge, web)

    result = ask(service, "Según mis manuales, ¿cómo configuro una VPN?")

    assert result["route"] == "knowledge"
    assert result["answer"] == knowledge.answer
    assert result["sources"] == [source]
    assert service.sessions._sessions[result["session_id"]]["pending_alternative"] is None
    assert not provider.calls
    assert not web.calls


def test_public_search_requires_consent_and_uses_a_sanitized_query():
    from backend.app.assistant.web_search import WebSearchResult

    source = WebSearchResult(
        "VPN setup guide", "https://example.org/vpn-setup", "General steps for a VPN connection."
    )
    web = StubWebSearch(results=[source])
    provider = StubProvider("La guía pública describe una configuración general.")
    knowledge = StubKnowledge(answer="No hay evidencia suficiente.", status="insufficient_evidence",
                              sources=[])
    service = build(provider, knowledge, web)
    question = (
        "Según mis manuales, ¿cómo configuro la VPN de Contoso en vpn.corp.local "
        "con usuario ana, contraseña Secreta123 y servidor 10.1.2.3 en Windows 11?"
    )

    offer = _start_manual_alternative(service, question)

    assert not web.calls
    assert not provider.calls
    result = ask(service, "Sí, búscalo", offer["session_id"])

    assert result["route"] == "general"
    assert result["answer"].startswith("Orientación pública")
    assert result["sources"][0]["url"] == source.url
    assert web.calls == [("Cómo configurar VPN en Windows 11", 5)]
    assert not provider.calls[0].find("Contoso") >= 0
    assert "vpn.corp.local" not in provider.calls[0]
    assert "Secreta123" not in provider.calls[0]
    assert "10.1.2.3" not in provider.calls[0]
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is None


def test_consented_vpn_search_synthesizes_relevant_public_evidence():
    from backend.app.assistant.web_search import WebSearchResult

    evidence = (
        "En Windows, abre Configuración, selecciona Red e Internet y después VPN. "
        "Añade una conexión e introduce los datos proporcionados por el administrador."
    )
    source = WebSearchResult(
        "Configurar una conexión VPN en Windows",
        "https://support.example.org/windows-vpn",
        evidence,
    )
    provider = StubProvider(evidence)
    web = StubWebSearch(results=[source])
    service = build(provider, StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    assert web.calls == [("Cómo configurar VPN", 5)]
    assert provider.calls
    assert "Evidencia pública pertinente:" in provider.calls[0]
    assert "Según mis manuales" not in provider.calls[0]
    assert "VPN (Red Privada Virtual)" in result["answer"]
    assert "abre Configuración" in result["answer"]
    assert "no es un procedimiento autorizado" in result["answer"]
    assert "Fuentes públicas consultadas:" in result["answer"]
    assert result["sources"][0]["url"] == source.url


def test_search_diagnostics_report_retained_results_without_sensitive_content(caplog):
    from backend.app.assistant.web_search import WebSearchResult

    caplog.set_level("INFO", logger="uvicorn.error")
    secret_question = (
        "Según mis manuales, ¿cómo configuro la VPN de Contoso con contraseña "
        "SensitiveToken-48391?"
    )
    private_marker = "PrivateSnippet-82017"
    evidence = (
        "VPN: abre Configuración, selecciona Red e Internet y después VPN. "
        f"Referencia pública: {private_marker}."
    )
    source = WebSearchResult(
        "Configurar una VPN",
        "https://support.example.org/vpn",
        evidence,
    )
    web = StubWebSearch(results=[source])
    service = build(StubProvider(
        "VPN: abre Configuración, selecciona Red e Internet y después VPN."
    ), StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service, question=secret_question)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    events = [
        record.public_search_diagnostics
        for record in caplog.records
        if hasattr(record, "public_search_diagnostics")
    ]
    assert len(result["sources"]) == 1
    assert len(events) == 1
    assert events[0] == {
        "query_category": "vpn",
        "provider": "custom",
        "provider_result_count": 1,
        "basic_validated_count": 1,
        "relevance_filtered_count": 1,
        "discard_reasons": {"provider": {}, "conversational": {}},
        "error_category": None,
        "response_mode": "ollama_synthesis",
    }
    assert all(
        sensitive not in caplog.text
        for sensitive in ("Contoso", "SensitiveToken-48391", private_marker, evidence)
    )


def test_relevance_filter_discards_results_and_logs_safe_reason(caplog):
    from backend.app.assistant.web_search import WebSearchResult

    caplog.set_level("INFO", logger="uvicorn.error")
    web = StubWebSearch(results=[
        WebSearchResult(
            "Impresora: instala el controlador",
            "https://support.example.org/printer",
            "Instala el controlador de la impresora.",
        )
    ])
    service = build(knowledge=StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web_search=web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    event = next(
        record.public_search_diagnostics
        for record in caplog.records
        if hasattr(record, "public_search_diagnostics")
    )
    assert "no contienen información suficiente" in result["answer"]
    assert event["provider_result_count"] == 1
    assert event["basic_validated_count"] == 1
    assert event["relevance_filtered_count"] == 0
    assert event["discard_reasons"]["conversational"] == {"irrelevant_topic": 1}
    assert event["error_category"] == "irrelevant"
    assert event["response_mode"] == "conversational_recovery"


@pytest.mark.parametrize(("category", "expected"), [
    ("timeout", "timeout"),
    ("rate_limited", "rate_limited"),
])
def test_provider_failures_are_reported_safely_in_search_diagnostics(
    category, expected, caplog
):
    from backend.app.assistant.web_search import WebSearchError

    caplog.set_level("INFO", logger="uvicorn.error")
    web = StubWebSearch(error=WebSearchError(
        f"private provider detail {category}", category
    ))
    service = build(knowledge=StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web_search=web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    event = next(
        record.public_search_diagnostics
        for record in caplog.records
        if hasattr(record, "public_search_diagnostics")
    )
    assert "explique VPN" in result["answer"]
    assert event["error_category"] == expected
    assert event["response_mode"] == "conversational_recovery"
    assert "private provider detail" not in caplog.text


def test_zero_provider_results_are_reported_as_conversational_recovery(caplog):
    caplog.set_level("INFO", logger="uvicorn.error")
    web = StubWebSearch(results=[])
    service = build(knowledge=StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web_search=web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    event = next(
        record.public_search_diagnostics
        for record in caplog.records
        if hasattr(record, "public_search_diagnostics")
    )
    assert "no encontró resultados" in result["answer"]
    assert event["provider_result_count"] == 0
    assert event["relevance_filtered_count"] == 0
    assert event["error_category"] == "no_results"
    assert event["response_mode"] == "conversational_recovery"


def test_current_fact_search_logs_evidence_fallback_diagnostics(caplog):
    from backend.app.assistant.web_search import WebSearchResult

    caplog.set_level("INFO", logger="uvicorn.error")
    source = WebSearchResult(
        "Windows 11 release information",
        "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information",
        "La tabla oficial de Windows 11 confirma la versión actual.",
        verified=True,
    )
    provider = StubProvider("No debe usarse para una fuente oficial verificada.")
    service = build(provider, StubKnowledge(), StubWebSearch(results=[source]))

    answer = asyncio.run(service._general_answer(
        {},
        "¿Cuál es la versión más reciente de Windows 11?",
    ))

    event = next(
        record.public_search_diagnostics
        for record in caplog.records
        if hasattr(record, "public_search_diagnostics")
    )
    assert "tabla oficial" in answer
    assert not provider.calls
    assert event["query_category"] == "windows"
    assert event["provider_result_count"] == 1
    assert event["relevance_filtered_count"] == 1
    assert event["response_mode"] == "evidence_fallback"


def test_consented_search_with_insufficient_public_evidence_does_not_invent_steps():
    from backend.app.assistant.web_search import WebSearchResult

    provider = StubProvider("Abre el panel, configura el servidor interno y conecta.")
    web = StubWebSearch(results=[
        WebSearchResult("VPN", "https://example.org/vpn", "VPN.")
    ])
    service = build(provider, StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    assert "no contienen información suficiente" in result["answer"]
    assert "servidor interno" not in result["answer"]
    assert result["sources"] == []
    assert not provider.calls


@pytest.mark.parametrize("provider", [
    StubProvider(""),
    StubProvider(error=TimeoutError("model timed out")),
    StubProvider(error=RuntimeError("model unavailable")),
])
def test_consented_search_uses_grounded_extracts_if_model_synthesis_fails(provider):
    from backend.app.assistant.web_search import WebSearchResult

    evidence = "VPN: abre Configuración, selecciona Red e Internet y después VPN."
    source = WebSearchResult(
        "Configuración VPN", "https://support.example.org/vpn", evidence
    )
    web = StubWebSearch(results=[source])
    service = build(provider, StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    assert "abre Configuración" in result["answer"]
    assert "no pude resumirlas" not in result["answer"]
    assert result["sources"][0]["url"] == source.url


def test_public_synthesis_rejects_malformed_irrelevant_and_unsupported_content():
    from backend.app.assistant.web_search import WebSearchResult

    provider = StubProvider(
        "En Windows configura vpn.corp.local con el usuario ana y reinicia el equipo."
    )
    web = StubWebSearch(results=[
        {"title": "Malformed", "url": "not a URL", "snippet": "VPN"},
        WebSearchResult("VPN", "https://example.org/vpn", "VPN."),
        WebSearchResult("Noticias de impresoras", "https://example.org/printers",
                        "RAW IRRELEVANT SNIPPET"),
        WebSearchResult(
            "VPN para Windows",
            "https://support.example.org/vpn",
            "VPN: abre Configuración y selecciona Red e Internet.",
        ),
    ])
    service = build(provider, StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    assert "abre Configuración" in result["answer"]
    assert "vpn.corp.local" not in result["answer"]
    assert "usuario ana" not in result["answer"]
    assert "RAW IRRELEVANT SNIPPET" not in result["answer"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == "https://support.example.org/vpn"


def test_manual_alternative_search_can_be_selected_as_first_option():
    from backend.app.assistant.web_search import WebSearchResult

    web = StubWebSearch(results=[
        WebSearchResult("Public VPN guide", "https://example.org/vpn", "General VPN guidance.")
    ])
    service = build(StubProvider("Guía general."), StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "La primera opción.", offer["session_id"])

    assert result["route"] == "general"
    assert len(web.calls) == 1


@pytest.mark.parametrize("reply", [
    "Busca en internet.",
    "Mejor investígalo.",
    "Sí, por favor.",
    "La primera opción.",
])
def test_clear_public_search_replies_grant_consent(reply):
    from backend.app.assistant.web_search import WebSearchResult

    web = StubWebSearch(results=[
        WebSearchResult("Public VPN guide", "https://example.org/vpn", "General VPN guidance.")
    ])
    service = build(StubProvider("Guía general."), StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    result = ask(service, reply, offer["session_id"])

    assert result["route"] == "general"
    assert len(web.calls) == 1


def test_general_alternative_uses_local_model_and_labels_non_company_guidance():
    provider = StubProvider("¿La configurarás en Windows, Android o algún otro sistema?")
    web = StubWebSearch()
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Mejor explícame tú.", offer["session_id"])

    assert result["route"] == "general"
    assert result["answer"].startswith("Orientación general (no es un procedimiento autorizado")
    assert "VPN (Red Privada Virtual)" in provider.calls[0]
    assert "no se conoce el sistema operativo" in provider.calls[0]
    assert not web.calls
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is None


def test_general_alternative_reuses_platform_already_in_manual_question():
    provider = StubProvider("Pasos generales para Windows 11.")
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                            status="insufficient_evidence", sources=[]))
    offer = _start_manual_alternative(
        service, "Según mis manuales, ¿cómo configuro una VPN en Windows 11?"
    )

    result = ask(service, "Explícamelo de manera general.", offer["session_id"])

    assert result["route"] == "general"
    assert "Plataforma indicada: Windows 11" in provider.calls[0]
    assert "pregunta brevemente si usará Windows" not in provider.calls[0]


def test_second_option_selects_general_guidance():
    provider = StubProvider("¿Qué sistema operativo usarás?")
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]),
                    StubWebSearch())
    offer = _start_manual_alternative(service)

    result = ask(service, "La segunda opción.", offer["session_id"])

    assert result["route"] == "general"
    assert result["answer"].startswith("Orientación general")
    assert len(provider.calls) == 1


def test_declining_manual_alternatives_invokes_neither_provider():
    provider = StubProvider("No debe usarse.")
    web = StubWebSearch()
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                            status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "No, gracias.", offer["session_id"])

    assert result["route"] == "social"
    assert "No haré una búsqueda pública" in result["answer"]
    assert not provider.calls
    assert not web.calls
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is None


def test_ambiguous_yes_clarifies_without_search_or_repeated_rag():
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence", sources=[])
    web = StubWebSearch()
    provider = StubProvider("No debe usarse.")
    service = build(provider, knowledge, web)
    offer = _start_manual_alternative(service)

    first = ask(service, "Sí.", offer["session_id"])
    second = ask(service, "Sí.", offer["session_id"])

    assert "¿Prefieres" in first["answer"]
    assert second["answer"] == first["answer"]
    assert len(knowledge.queries) == 1
    assert not web.calls
    assert not provider.calls
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"]["search_consent"] is False


def test_new_question_clears_pending_alternative_and_uses_normal_routing():
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence", sources=[])
    service = build(knowledge=knowledge, web_search=StubWebSearch())
    offer = _start_manual_alternative(service)
    knowledge.status = "answered"
    knowledge.answer = "Definición autorizada de DHCP."
    knowledge.sources = [{"title": "Manual de DHCP", "document_id": "dhcp-doc", "page": 2,
                          "citation_id": "dhcp-citation"}]

    result = ask(service, "¿Qué es DHCP?", offer["session_id"])

    assert result["route"] == "knowledge"
    assert result["answer"] == knowledge.answer
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is None
    assert len(knowledge.queries) == 2


def test_rag_failure_reports_error_without_offering_alternatives():
    knowledge = StubKnowledge()

    async def unavailable(request):
        raise RuntimeError("synthetic retrieval failure")

    knowledge.query = unavailable
    web = StubWebSearch()
    service = build(StubProvider(), knowledge, web)

    result = ask(service, "Según mis manuales, ¿cómo configuro una VPN?")

    assert result["route"] == "knowledge"
    assert "No se pudo consultar la documentación" in result["answer"]
    assert "No encontré evidencia suficiente" not in result["answer"]
    assert service.sessions._sessions[result["session_id"]]["pending_alternative"] is None
    assert not web.calls


@pytest.mark.parametrize(("category", "expected"), [
    ("provider_unavailable", "No pude consultar fuentes públicas"),
    ("timeout", "tardó demasiado"),
    ("rate_limited", "limitó temporalmente"),
])
def test_consented_public_search_failure_offers_local_general_recovery(category, expected):
    from backend.app.assistant.web_search import WebSearchError

    web = StubWebSearch(error=WebSearchError("internal provider error", category))
    provider = StubProvider("Explicación general sin ajustes internos.")
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    failed = ask(service, "Busca en internet.", offer["session_id"])

    assert failed["route"] == "general"
    assert expected in failed["answer"]
    assert "¿Quieres que te explique VPN (Red Privada Virtual) de manera general?" in failed["answer"]
    assert "internal provider error" not in failed["answer"]
    assert failed["sources"] == []
    assert not provider.calls
    assert len(web.calls) == 1
    pending = service.sessions._sessions[offer["session_id"]]["pending_alternative"]
    assert pending["public_search_failed"] is True
    assert pending["offered_alternatives"] == ["general"]

    recovered = ask(service, "Sí.", offer["session_id"])

    assert recovered["route"] == "general"
    assert recovered["answer"].startswith(
        "Orientación general (no es un procedimiento autorizado"
    )
    assert len(provider.calls) == 1
    assert len(web.calls) == 1
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is None


def test_empty_public_search_is_distinguished_and_can_recover():
    web = StubWebSearch(results=[])
    provider = StubProvider("Explicación general.")
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Busca en internet.", offer["session_id"])

    assert "no encontró resultados" in result["answer"]
    assert "VPN (Red Privada Virtual)" in result["answer"]
    assert not provider.calls
    assert len(web.calls) == 1


def test_repeated_public_choice_after_failure_does_not_retry_search():
    from backend.app.assistant.web_search import WebSearchError

    web = StubWebSearch(error=WebSearchError("provider unavailable"))
    service = build(StubProvider(), StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)
    ask(service, "Busca en internet.", offer["session_id"])

    repeated = ask(service, "Sí, búscalo.", offer["session_id"])

    assert "no pudo completarse" in repeated["answer"]
    assert "VPN (Red Privada Virtual)" in repeated["answer"]
    assert len(web.calls) == 1
    assert not service.provider.calls


def test_irrelevant_public_results_offer_local_general_recovery():
    from backend.app.assistant.web_search import WebSearchResult

    web = StubWebSearch(results=[
        WebSearchResult("Guía de impresoras", "https://example.org/printers",
                        "Instala el controlador de la impresora.")
    ])
    provider = StubProvider("Explicación general.")
    service = build(provider, StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]
    ), web)
    offer = _start_manual_alternative(service)

    result = ask(service, "Sí, búscalo.", offer["session_id"])

    assert "resultados públicos no contienen información suficiente" in result["answer"]
    assert "explique VPN (Red Privada Virtual) de manera general" in result["answer"]
    assert result["sources"] == []
    assert not provider.calls
    assert len(web.calls) == 1


def test_public_search_without_safe_topic_requests_generic_description():
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence", sources=[])
    web = StubWebSearch()
    service = build(StubProvider(), knowledge, web)
    offer = _start_manual_alternative(
        service, "Según mis manuales, ¿cómo hago el trámite interno?"
    )

    clarification = ask(service, "La primera opción.", offer["session_id"])

    assert "no incluyas datos internos" in clarification["answer"]
    assert not web.calls
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"]["search_consent"] is True


def test_awaited_generic_public_description_is_sanitized_before_search():
    from backend.app.assistant.web_search import WebSearchResult

    web = StubWebSearch(results=[
        WebSearchResult("Public VPN guide", "https://example.org/vpn", "General VPN guidance.")
    ])
    service = build(StubProvider("Guía general."), StubKnowledge(
        answer="Sin evidencia.", status="insufficient_evidence", sources=[]), web)
    offer = _start_manual_alternative(
        service, "Según mis manuales, ¿cómo hago el trámite interno 742 de usuario?"
    )
    ask(service, "Busca en internet.", offer["session_id"])
    assert not web.calls

    result = ask(service, "Cómo configurar VPN en Windows 11", offer["session_id"])

    assert result["route"] == "general"
    assert web.calls == [("Cómo configurar VPN en Windows 11", 5)]


def test_pending_alternatives_are_isolated_and_expire_with_the_session(monkeypatch):
    import backend.app.assistant.memory as assistant_memory

    clock = [1000.0]
    monkeypatch.setattr(assistant_memory.time, "monotonic", lambda: clock[0])
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence", sources=[])
    web = StubWebSearch()
    service = build(knowledge=knowledge, web_search=web)
    service.sessions.ttl = 30
    first = _start_manual_alternative(service, session_id="isolated-pending")

    second = ask(service, "Sí, búscalo", "separate-session")
    assert "pending_alternative" not in service.sessions._sessions["separate-session"] \
        or service.sessions._sessions["separate-session"]["pending_alternative"] is None
    assert not web.calls
    assert second["route"] == "general"

    clock[0] += 31
    expired = ask(service, "Sí, búscalo", first["session_id"])
    assert service.sessions._sessions[first["session_id"]]["pending_alternative"] is None
    assert not web.calls
    assert expired["route"] == "general"


def test_pending_manual_conversation_preserves_social_responses():
    knowledge = StubKnowledge(answer="Sin evidencia.", status="insufficient_evidence", sources=[])
    service = build(knowledge=knowledge, web_search=StubWebSearch())
    offer = _start_manual_alternative(service)

    greeting = ask(service, "Hola, ¿cómo estás?", offer["session_id"])

    assert greeting["route"] == "social"
    assert greeting["answer"].startswith("¡Muy bien")
    assert service.sessions._sessions[offer["session_id"]]["pending_alternative"] is not None
    assert len(knowledge.queries) == 1


def test_vpn_expansion_appears_in_deterministic_manual_alternative():
    service = build(knowledge=StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]))

    result = ask(service, "Según mis manuales, ¿cómo configuro una VPN?")

    assert "VPN (Red Privada Virtual)" in result["answer"]


def test_selected_general_guidance_never_claims_to_be_company_procedure():
    provider = StubProvider("Pasos generales para establecer una conexión VPN.")
    service = build(provider, StubKnowledge(answer="Sin evidencia.",
                                             status="insufficient_evidence", sources=[]))
    offer = _start_manual_alternative(service)

    result = ask(service, "Con lo que sabes está bien.", offer["session_id"])

    assert result["answer"].startswith("Orientación general (no es un procedimiento autorizado")
    assert "vpn.corp.local" not in provider.calls[0]
    assert "credenciales" in provider.calls[0]


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
