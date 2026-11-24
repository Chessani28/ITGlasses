"""Privacy filter before the public search provider, and visible search diagnostics."""
import asyncio

import pytest

from backend.app.assistant.service import UnifiedAssistantService
from backend.app.assistant.web_search import WebSearchError

TITLES = ["Training Setup Guideline", "Procedimiento de Reinicio"]


class Provider:
    async def generate(self, message):
        return "respuesta general"


class Knowledge:
    """No evidence in the manuals, so the question goes on to the public search."""

    def __init__(self, titles=()):
        self.titles = list(titles)
        if titles:
            self.store = self

    def documents(self):
        return [{"title": title} for title in self.titles]

    def procedure_catalog(self, document_id):
        return {"procedures": []}

    async def query(self, request):
        from backend.app.knowledge.models import KnowledgeAnswer
        return KnowledgeAnswer(status="insufficient_evidence", answer="Sin evidencia.",
                               conversation_id="00000000-0000-0000-0000-000000000000", intent="steps",
                               options=[], sources=[], documentation_sufficient=False, retrieval={})


class Web:
    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self.last_error = None
        self.last_diagnostics = {}

    async def search(self, query, max_results=5):
        self.calls.append(query)
        if self.error:
            self.last_error = self.error
            self.last_diagnostics = {"provider": "custom", "provider_result_count": 0, "basic_validated_count": 0,
                                     "provider_discard_reasons": {}, "error_category": self.error.category}
            raise self.error
        return []


def ask(message, titles=(), web=None):
    web = web or Web()
    service = UnifiedAssistantService(Knowledge(titles), Provider(), web_search=web)
    result = asyncio.run(service.chat(message))
    return web, result


@pytest.mark.parametrize("message", [
    "Según internet, cuál es la versión más reciente de windows 11",
    "Busca en internet la última versión del estándar internacional",
    "Busca en internet cómo configurar manualmente el DNS",
])
def test_public_questions_are_not_private(message):
    assert not UnifiedAssistantService._private_for_public_search(message, TITLES)


@pytest.mark.parametrize("message", [
    "Según el manual, ¿cuál es la versión más reciente?",
    "según Training Setup Guideline, cuál es la versión más reciente",
    "según procedimiento de reinicio, ¿cuál es la versión más reciente?",  # A document title, whole words.
    "¿Cuál es la contraseña del wifi interno? busca en internet",
    "Busca en internet la última versión de https://intranet.local/x",
    "Busca en internet la versión más reciente del token de la empresa",
])
def test_internal_questions_stay_private(message):
    assert UnifiedAssistantService._private_for_public_search(message, TITLES)


def test_segun_internet_reaches_the_public_search():
    web, result = ask("Según internet, cuál es la versión más reciente de windows 11")
    assert len(web.calls) == 1
    assert "No enviaré" not in result["answer"]


@pytest.mark.parametrize("message", [
    "Según el manual, ¿cuál es la versión más reciente?",
    "según Training Setup Guideline, cuál es la versión más reciente",
    "Busca en internet la versión más reciente del procedimiento de reinicio",
])
def test_internal_questions_never_reach_the_public_search(message):
    web, result = ask(message, titles=TITLES)
    assert web.calls == []


def test_diagnostics_are_readable_in_the_log_text_without_the_query(caplog):
    caplog.set_level("INFO", logger="backend.app.assistant.web_search")   # the logger that reaches backend.log
    message = "Busca en internet cuál es la versión más reciente de DNS SecretoPrueba-7731"
    web, _ = ask(message, web=Web(error=WebSearchError("private provider detail", "timeout")))
    assert len(web.calls) == 1
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Public search diagnostics: {")]
    assert lines, "the diagnostics must be in the log text, not only in extra"
    assert '"error_category": "timeout"' in lines[0]
    assert '"provider": "custom"' in lines[0]
    assert "SecretoPrueba-7731" not in caplog.text
    assert "private provider detail" not in caplog.text
