"""El asistente arma las respuestas de información de un manual con frases del manual traducidas con los candados
de _translate; una traducción rechazada deja la frase original. Sin frases, como antes."""
import asyncio

from backend.app.knowledge.models import KnowledgeAnswer
from test_assistant_service import StubKnowledge, StubProvider, build

LABELS = "The only labels allowed to paste on devices are those used to identify the hostname."
CPU = "Confirm the speed value, this must be higher than 2.20 GHz."
QUESTION = "¿Qué etiquetas se permiten pegar en los equipos?"
SOURCES = [{"title": "Training Setup Guideline", "document_id": "doc-1", "page": 14, "version": "1",
            "revision": 1, "filename": "guide.pdf", "citation_id": "cit-1"}]


class ExcerptKnowledge(StubKnowledge):
    def __init__(self, excerpts, answer="Según «Training Setup Guideline»:\n\nEnglish text."):
        super().__init__(answer=answer, sources=SOURCES)
        self.excerpts = excerpts

    async def query(self, request):
        self.queries.append(request)
        return KnowledgeAnswer(status="answered", answer=self.answer, conversation_id="c-1", intent="information",
                               sources=self.sources, documentation_sufficient=True, excerpts=self.excerpts)


class TranslatingProvider(StubProvider):
    def __init__(self, translations):
        super().__init__()
        self.translations = translations

    async def generate(self, message):
        self.calls.append(message)
        for source, translation in self.translations.items():
            if source in message:
                return translation
        return "sin traducción"


def ask(service, message=QUESTION):
    return asyncio.run(service.chat(message))


def test_the_answer_is_the_translated_manual_sentence_with_its_source():
    provider = TranslatingProvider({LABELS: "Las únicas etiquetas permitidas en los equipos son las que identifican "
                                            "el hostname."})
    service = build(provider=provider, knowledge=ExcerptKnowledge([{"text": LABELS, "page": 14}]))
    result = ask(service)
    assert result["route"] == "knowledge"
    assert result["answer"] == ("Según «Training Setup Guideline», p. 14: Las únicas etiquetas permitidas en los "
                                "equipos son las que identifican el hostname.")
    assert result["sources"] == SOURCES


def test_a_translation_rejected_by_the_guards_keeps_the_original_sentence():
    # The translation changes the number (2.20 → 3): the guards keep the manual's own words.
    provider = TranslatingProvider({CPU: "Confirma la velocidad, debe ser mayor a 3 GHz."})
    service = build(provider=provider, knowledge=ExcerptKnowledge([{"text": CPU, "page": 56}]))
    assert ask(service)["answer"] == f"Según «Training Setup Guideline», p. 56: {CPU}"


def test_several_sentences_keep_their_order_and_pages():
    provider = TranslatingProvider({LABELS: "Solo se permiten etiquetas del hostname.",
                                    CPU: "Confirma la velocidad, debe ser mayor a 2.20 GHz."})
    service = build(provider=provider, knowledge=ExcerptKnowledge([{"text": LABELS, "page": 14},
                                                                   {"text": CPU, "page": 56}]))
    assert ask(service)["answer"] == ("Según «Training Setup Guideline», p. 14 y 56: Solo se permiten etiquetas del "
                                      "hostname. Confirma la velocidad, debe ser mayor a 2.20 GHz.")


def test_without_excerpts_the_answer_is_the_knowledge_answer_as_before():
    service = build(provider=StubProvider(), knowledge=ExcerptKnowledge([]))
    assert ask(service)["answer"] == "Según «Training Setup Guideline»:\n\nEnglish text."
