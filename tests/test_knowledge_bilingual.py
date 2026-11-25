"""Manuales en inglés con preguntas en español: traducción de la consulta, búsqueda con ambas, 4 pedazos al
selector y sin descargar los embeddings después de cada búsqueda (sin Ollama: dobles y MockTransport)."""
import asyncio
import json
from pathlib import Path

import httpx
import numpy as np

from backend.app.knowledge.grounding import EvidenceSelector
from backend.app.knowledge.models import KnowledgeRequest, Metadata
from backend.app.knowledge.service import KnowledgeService
from backend.app.knowledge.store import normalize

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "¿Qué cartucho uso para la impresora de recepción?"
ENGLISH = "Which cartridge do I use for the reception printer?"
MANUAL = "\n\n".join([
    "Reception printer\nUse only the HP 58A toner cartridge for the reception printer.",
    "Paper\nLoad letter size paper in tray two.",
    "Cleaning\nClean the rollers every month with a dry cloth.",
    "Network\nThe printer uses a static address assigned by IT.",
    "Support\nOpen a ticket if the printer shows a paper jam twice.",
])


class EnglishOnlyEmbeddings:
    """Only English words carry meaning: a Spanish question matches nothing; its English version matches."""
    model = "synthetic-english"
    digest = "synthetic-english-v1"

    def __init__(self):
        self.unloads = 0

    async def fingerprint(self):
        return self.digest

    async def unload(self):
        self.unloads += 1

    async def embed(self, texts, query=False):
        vectors = []
        for text in texts:
            text = normalize(text)
            vector = np.array([float("cartridge" in text or "toner" in text), float("paper" in text),
                               float("network" in text), 0.0, 0.0], dtype=np.float32)
            if not vector.any():  # no meaning: queries and pieces never match each other by accident
                vector[3 if query else 4] = 1.0
            vectors.append(vector / np.linalg.norm(vector))
        return np.array(vectors)


class TranslatingSelector:
    def __init__(self, translation=ENGLISH):
        self.translation = translation
        self.translated, self.selected = [], []

    async def translate_query(self, question):
        self.translated.append(question)
        return self.translation

    async def select(self, question, rows):
        self.selected.append((question, len(rows)))
        return [0]


def build(tmp_path, selector):
    embeddings = EnglishOnlyEmbeddings()
    svc = KnowledgeService(tmp_path, embeddings, selector)
    asyncio.run(svc.ingest("printer.md", MANUAL.encode(), Metadata(
        title="Reception Printer Guide", procedure_id="77", version="1", authorized=True)))
    return svc, embeddings


def ask(svc, message=QUESTION):
    return asyncio.run(svc.query(KnowledgeRequest(message=message)))


# --- translate_query ---------------------------------------------------------------------------------------

def selector_with(handler):
    return EvidenceSelector("qwen-test", transport=httpx.MockTransport(handler))


def test_translate_query_returns_the_english_question():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"english": ENGLISH})}})
    assert asyncio.run(selector_with(handler).translate_query(QUESTION)) == ENGLISH
    payload = seen[0]
    assert payload["options"]["temperature"] == 0.0
    assert payload["options"]["num_predict"] == 96
    assert payload["format"]["required"] == ["english"]
    assert QUESTION in payload["messages"][-1]["content"]


def test_translate_query_failures_give_none():
    def broken_json(request):
        return httpx.Response(200, json={"done": True, "message": {"content": "no es json"}})

    def server_error(request):
        return httpx.Response(500)

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def empty(request):
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"english": " "})}})
    for handler in (broken_json, server_error, timeout, empty):
        assert asyncio.run(selector_with(handler).translate_query(QUESTION)) is None


# --- search and query ---------------------------------------------------------------------------------------

def test_the_spanish_question_alone_does_not_find_the_english_manual(tmp_path):
    svc, _ = build(tmp_path, TranslatingSelector(translation=None))
    assert ask(svc).status == "insufficient_evidence"


def test_the_translation_finds_the_english_manual(tmp_path):
    selector = TranslatingSelector()
    svc, _ = build(tmp_path, selector)
    result = ask(svc)
    assert result.status == "answered"
    assert selector.translated == [QUESTION]
    assert selector.selected[0][0] == QUESTION  # the selector still judges the user's own question


def test_search_without_alternates_is_unchanged(tmp_path):
    svc, _ = build(tmp_path, TranslatingSelector())
    plain = asyncio.run(svc.retriever.search(ENGLISH))
    with_none = asyncio.run(svc.retriever.search(ENGLISH, alternates=()))
    assert [row["id"] for row in plain] == [row["id"] for row in with_none]
    boosted = asyncio.run(svc.retriever.search(QUESTION, alternates=(ENGLISH,)))
    assert boosted[0]["semantic"] == plain[0]["semantic"] and boosted[0]["id"] == plain[0]["id"]


def test_a_selector_without_translation_still_works(tmp_path):
    class OldSelector:
        async def select(self, question, rows):
            return [0]
    svc, _ = build(tmp_path, OldSelector())
    assert ask(svc, ENGLISH).status == "answered"


def test_up_to_four_pieces_reach_the_selector(tmp_path):
    selector = TranslatingSelector()
    svc, _ = build(tmp_path, selector)
    ask(svc)
    chunks = len(svc.store.document_chunks(svc.store.documents()[0]["id"]))
    assert selector.selected[0][1] == min(4, chunks) and chunks > 2


def test_the_embedding_model_is_unloaded_after_each_search(tmp_path):
    # Kept loaded, its ~0.9 GB of RAM left Whisper Small below its 3 GiB minimum and the phone fell back to Vosk.
    # With OLLAMA_MAX_LOADED_MODELS=2 unloading it no longer evicts the chat model from the GPU.
    svc, embeddings = build(tmp_path, TranslatingSelector())
    embeddings.unloads = 0
    ask(svc)
    ask(svc)
    assert embeddings.unloads == 2


def test_the_embedding_model_is_unloaded_even_when_the_search_fails(tmp_path):
    svc, embeddings = build(tmp_path, TranslatingSelector())
    embeddings.unloads = 0

    async def broken(*args, **kwargs):
        raise RuntimeError("search failed")
    svc.retriever.search = broken
    try:
        ask(svc)
    except RuntimeError:
        pass
    assert embeddings.unloads == 1


def test_exact_identifiers_skip_the_translation(tmp_path):
    selector = TranslatingSelector()
    svc, _ = build(tmp_path, selector)
    ask(svc, "¿Cuál es el procedimiento 77?")
    assert selector.translated == []


class BandEmbeddings(EnglishOnlyEmbeddings):
    """The English translation lands in the 0.42–0.55 band (or above it with high=True); Spanish matches nothing."""

    def __init__(self, high=False):
        super().__init__()
        self.high = high

    async def embed(self, texts, query=False):
        vectors = []
        for text in texts:
            text = normalize(text)
            if not query:
                vector = [1.0, 0.0, 0.0] if "toner" in text else [0.0, 0.0, 1.0]
            elif "cartridge" in text:
                vector = [0.6, 0.8, 0.0] if self.high else [0.45, 0.893, 0.0]
            else:
                vector = [0.0, 1.0, 0.0]
            array = np.array(vector, dtype=np.float32)
            vectors.append(array / np.linalg.norm(array))
        return np.array(vectors)


def band_service(tmp_path, high):
    svc = KnowledgeService(tmp_path, BandEmbeddings(high), TranslatingSelector())
    asyncio.run(svc.ingest("printer.md", MANUAL.encode(), Metadata(
        title="Reception Printer Guide", procedure_id="77", version="1", authorized=True)))
    return svc


def test_words_shared_only_with_the_translation_do_not_open_the_middle_band(tmp_path):
    # 0.45 with the translation, and its words (cartridge, printer, reception) are in the manual; the user's own
    # Spanish words are not: as before the translation, this is not enough evidence.
    assert ask(band_service(tmp_path, high=False)).status == "insufficient_evidence"


def test_a_strong_match_through_the_translation_is_enough(tmp_path):
    assert ask(band_service(tmp_path, high=True)).status == "answered"


def test_search_reports_the_overlap_of_the_original_question(tmp_path):
    svc, _ = build(tmp_path, TranslatingSelector())
    best = asyncio.run(svc.retriever.search(QUESTION, alternates=(ENGLISH,)))[0]
    assert best["overlap"] >= 2 and best["overlap_original"] == 0


def test_english_stop_words_are_ignored():
    from backend.app.knowledge.retrieval import tokens
    assert tokens("And how do I change it in Windows?") == ["change", "windows"]
    assert tokens("What is the difference between an SSD and a hard disk?") == ["difference", "between", "ssd",
                                                                                 "hard", "disk"]


class PickingSelector(TranslatingSelector):
    def __init__(self, picked=(0,)):
        super().__init__()
        self.picked = list(picked) if picked is not None else None
        self.candidates = []

    async def pick_sentences(self, question, sentences):
        self.candidates = sentences
        return self.picked


def test_an_information_answer_is_made_of_picked_manual_sentences(tmp_path):
    selector = PickingSelector(picked=(0,))
    svc, _ = build(tmp_path, selector)
    result = ask(svc)
    assert result.status == "answered"
    first = selector.candidates[0]["text"]
    assert result.excerpts == [{"text": first, "page": selector.candidates[0]["page"]}]
    assert "Según la documentación autorizada" not in result.answer
    assert first in result.answer and "Reception Printer Guide" in result.answer


def test_without_a_pick_the_answer_is_the_clean_excerpt(tmp_path):
    svc, _ = build(tmp_path, PickingSelector(picked=None))
    result = ask(svc)
    assert result.status == "answered" and result.excerpts == []
    assert "Según la documentación autorizada" not in result.answer
    assert "Reception Printer Guide" in result.answer


class EnglishAwareSelector(TranslatingSelector):
    async def select(self, question, rows, question_en=None):
        self.selected.append((question, len(rows), question_en))
        return [0]


def test_the_evidence_selector_also_gets_the_english_question(tmp_path):
    selector = EnglishAwareSelector()
    svc, _ = build(tmp_path, selector)
    ask(svc)
    assert selector.selected[0][0] == QUESTION and selector.selected[0][2] == ENGLISH


def test_the_evidence_selector_payload_carries_the_english_question():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(
            {"sufficient": True, "ids": ["0"]})}})
    selector = EvidenceSelector("qwen-test", transport=httpx.MockTransport(handler))
    rows = [{"title": "T", "text": "Use only the HP 58A toner cartridge."}]
    assert asyncio.run(selector.select(QUESTION, rows, question_en=ENGLISH)) == [0]
    content = json.loads(seen[0]["messages"][-1]["content"])
    assert content["question"] == QUESTION and content["question_en"] == ENGLISH
    asyncio.run(selector.select(QUESTION, rows))
    assert "question_en" not in json.loads(seen[1]["messages"][-1]["content"])


class FlakySelector(TranslatingSelector):
    def __init__(self, failures):
        super().__init__()
        self.failures, self.calls = failures, 0

    async def select(self, question, rows):
        from backend.app.knowledge.models import KnowledgeError
        self.calls += 1
        if self.calls <= self.failures:
            raise KnowledgeError("grounding_error", "invalid selection", 502)
        return [0]


def test_an_invalid_selection_is_retried_once(tmp_path):
    selector = FlakySelector(failures=1)
    svc, _ = build(tmp_path, selector)
    assert ask(svc).status == "answered" and selector.calls == 2


def test_two_invalid_selections_mean_no_evidence_not_an_error(tmp_path):
    selector = FlakySelector(failures=2)
    svc, _ = build(tmp_path, selector)
    assert ask(svc).status == "insufficient_evidence" and selector.calls == 2


def test_cover_and_index_pages_are_not_given_to_the_selector():
    from backend.app.knowledge.service import evidence_rows
    cover = {"document_id": "d", "page": 1, "text": "Training setup\nGuideline\nIT DEPARTMENT"}
    index = {"document_id": "d", "page": 2, "text": "INDEX\n1. PREPARATION ........ Page 2\n2. DEPLOY ........ Page 3"}
    body = [{"document_id": "d", "page": page, "text": f"Real sentence on page {page}."} for page in (56, 30, 34, 32)]
    other = {"document_id": "x", "page": 9, "text": "Another document."}
    rows = evidence_rows([cover, index, other, *body], "d", 4)
    assert [row["page"] for row in rows] == [56, 30, 34, 32]
    only_cover = evidence_rows([cover], "d", 4)
    assert only_cover == [cover]  # never empty when the document has nothing else


def test_what_is_the_first_thing_is_not_an_identification_question():
    from backend.app.knowledge.retrieval import intent
    assert intent("Para Dominos CS, ¿qué es lo primero que instalo?") == "information"
    assert intent("¿Qué es lo que debo revisar después?") == "information"
    assert intent("¿Qué es formatear?") == "identify"


def test_manual_terms_are_the_names_and_acronyms_of_the_manuals(tmp_path):
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), TranslatingSelector())
    text = ("For Dominos CS install Chrome first. The CPU must be higher than 2.20 GHz.\n"
            "Then open SUMMIT and Haven OS.")
    asyncio.run(svc.ingest("guide.md", text.encode(), Metadata(title="Training Guide", procedure_id="9",
                                                               version="1", authorized=True)))
    terms = svc.manual_terms()
    assert {"dominos", "cs", "chrome", "cpu", "ghz", "summit", "haven", "os"} <= terms
    assert not {"for", "the", "then", "install", "first", "must"} & terms


def test_the_name_of_a_folder_is_information_not_a_procedure_name():
    from backend.app.knowledge.retrieval import intent
    assert intent("Para SUMMIT, ¿cómo se llama la carpeta que creo en el escritorio?") == "information"
    assert intent("¿Cómo se llama el proceso? Dame su nombre.") == "identify"
    assert intent("¿Cómo se llama el procedimiento para formatear?") == "identify"
    assert intent("No recuerdo cómo se llama") == "identify"


def test_confirm_that_it_is_running_is_not_an_identification_question():
    from backend.app.knowledge.retrieval import intent
    assert intent("¿Qué servicio debo confirmar que esté corriendo después de instalar Windows?") != "identify"


def test_the_ollama_script_keeps_two_models_loaded():
    script = (ROOT / "scripts" / "start-ollama.ps1").read_text(encoding="utf-8")
    assert "OLLAMA_MAX_LOADED_MODELS = '2'" in script
