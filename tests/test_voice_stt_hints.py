"""Voz 2 in the backend: Whisper vocabulary hint, transcript correction (Whisper and Vosk), counts-only logs."""
import asyncio
import types

import pytest

from backend.app.voice import service as voice_service_module
from backend.app.voice.service import VoiceService, stt_correction_enabled, whisper_hint_mode
from test_voice import wav
from test_voice_whisper import FakePersistentWorker, LegacyProcess, prepare_whisper

HEARD = "Según el manual de 30 setos guillellidos, cuáles son los pasos"
FIXED = "Según el manual de Training Setup Guideline, cuáles son los pasos"


class Store:
    def __init__(self, revision=1, fail=False):
        self.revision, self.fail, self.calls = revision, fail, 0

    def documents(self, eligible_only=False):
        assert eligible_only
        self.calls += 1
        if self.fail:
            raise RuntimeError("database locked")
        return [{"id": "d1", "title": "Training Setup Guideline", "current_revision": self.revision,
                 "authorized": 1, "withdrawn": 0},
                {"id": "d2", "title": "Guía de Instalación", "current_revision": 1, "authorized": 1, "withdrawn": 0}]


class Knowledge:
    def __init__(self, store):
        self.store, self.catalog_calls = store, 0

    def procedure_catalog(self, document_id):
        self.catalog_calls += 1
        return {"procedures": [{"title": "COMPUTER PREPARATION"}]} if document_id == "d1" else None


class Request:
    def __init__(self, knowledge):
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(knowledge=knowledge))

    async def is_disconnected(self):
        return False


class HintWorker(FakePersistentWorker):
    def __init__(self, *args, text=HEARD, **kwargs):
        super().__init__(*args, **kwargs)
        self.hints, self.text = [], text

    def transcribe(self, audio, hint=None, hint_mode=None):
        self.hints.append((hint, hint_mode))
        self.transcribe_calls += 1
        self.last_used = __import__("time").monotonic()
        return {"text": self.text, "raw_text": self.text, "normalized_text": self.text, "engine": "whisper-small",
                "inference_seconds": 0.1, "hint_used": bool(hint), "hint_mode": hint_mode}


@pytest.fixture
def whisper(tmp_path, monkeypatch):
    for name in ("ITGLASSES_WHISPER_HINT_MODE", "ITGLASSES_STT_CORRECTION"):
        monkeypatch.delenv(name, raising=False)
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    monkeypatch.setattr(service, "_whisper_configuration", lambda: ({"python": python, "model": model}, None))
    monkeypatch.setattr(voice_service_module, "_available_system_memory", lambda: 4 * 1024**3)
    workers = []

    def create(*_args):
        worker = HintWorker(python, model)
        workers.append(worker)
        return worker

    monkeypatch.setattr(voice_service_module, "PersistentWhisperWorker", create)
    return service, workers


def transcribe(service, request, engine="whisper-small"):
    async def exercise():
        try:
            return await service.run("stt", wav(), request, engine=engine)
        finally:
            await service.shutdown()
    return asyncio.run(exercise())


@pytest.mark.parametrize(("value", "expected"), [
    (None, "hotwords"), ("", "hotwords"), ("hotwords", "hotwords"), ("INITIAL_PROMPT", "initial_prompt"),
    ("off", "off"), ("prompt", "hotwords"),
])
def test_hint_mode_defaults_to_hotwords(value, expected, monkeypatch):
    monkeypatch.delenv("ITGLASSES_WHISPER_HINT_MODE", raising=False)
    if value is not None:
        monkeypatch.setenv("ITGLASSES_WHISPER_HINT_MODE", value)
    assert whisper_hint_mode() == expected


@pytest.mark.parametrize(("value", "expected"), [
    (None, True), ("", True), ("true", True), ("ON", True), ("1", True),
    ("false", False), ("off", False), ("0", False), ("no", False), ("maybe", True),
])
def test_correction_is_on_by_default(value, expected, monkeypatch):
    monkeypatch.delenv("ITGLASSES_STT_CORRECTION", raising=False)
    if value is not None:
        monkeypatch.setenv("ITGLASSES_STT_CORRECTION", value)
    assert stt_correction_enabled() is expected


def test_whisper_gets_hotwords_and_the_title_is_corrected(whisper):
    service, workers = whisper
    result = transcribe(service, Request(Knowledge(Store())))
    hint, mode = workers[0].hints[0]
    assert mode == "hotwords"
    assert hint.startswith("Training Setup Guideline, Guía de Instalación, COMPUTER PREPARATION, BIOS")
    assert result["text"] == FIXED and result["raw_text"] == HEARD
    assert result["corrections"][0]["replacement"] == "Training Setup Guideline"
    assert "hint" not in result and "Training Setup Guideline, Guía" not in str(result)  # vocabulary not exposed


@pytest.mark.parametrize(("mode", "sent"), [("initial_prompt", "initial_prompt"), ("off", None)])
def test_other_hint_modes(mode, sent, whisper, monkeypatch):
    monkeypatch.setenv("ITGLASSES_WHISPER_HINT_MODE", mode)
    service, workers = whisper
    result = transcribe(service, Request(Knowledge(Store())))
    assert workers[0].hints[0][1] == sent
    assert (workers[0].hints[0][0] is None) == (sent is None)
    assert result["text"] == FIXED  # the correction does not depend on the hint


def test_correction_off_leaves_the_transcript_untouched(whisper, monkeypatch):
    monkeypatch.setenv("ITGLASSES_STT_CORRECTION", "false")
    service, workers = whisper
    result = transcribe(service, Request(Knowledge(Store())))
    assert workers[0].hints[0][1] == "hotwords"
    assert result["text"] == HEARD and "corrections" not in result


def test_vocabulary_is_rebuilt_only_when_the_knowledge_base_changes(whisper):
    service, _workers = whisper
    store = Store()
    knowledge = Knowledge(store)
    transcribe(service, Request(knowledge))
    transcribe(service, Request(knowledge))
    assert knowledge.catalog_calls == 2  # one build: two documents
    store.revision = 2
    transcribe(service, Request(knowledge))
    assert knowledge.catalog_calls == 4


@pytest.mark.parametrize("request_factory", [
    lambda: None,                                   # no request (internal use)
    lambda: Request(None),                          # knowledge service not started, no autoload in tests
    lambda: Request(types.SimpleNamespace()),       # an object without a store
    lambda: Request(Knowledge(Store(fail=True))),   # the knowledge base fails
])
def test_without_vocabulary_whisper_still_transcribes(request_factory, whisper):
    service, workers = whisper
    result = transcribe(service, request_factory())
    assert workers[0].hints[0] == (None, None)
    assert result["text"] == HEARD and "corrections" not in result


def test_vosk_transcripts_are_corrected_without_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_STT_CORRECTION", raising=False)
    service = VoiceService(tmp_path)
    monkeypatch.setattr(service, "_vosk_available", lambda: True)
    tasks = []

    async def spawn(*_args, **_kwargs):
        process = LegacyProcess({"text": HEARD, "raw_text": HEARD.lower(), "engine": "vosk"})
        original = process.communicate

        async def communicate(payload):
            tasks.append(__import__("json").loads(payload))
            return await original(payload)

        process.communicate = communicate
        return process

    monkeypatch.setattr(voice_service_module, "_spawn_worker", spawn)
    result = asyncio.run(service.run("stt", wav(), Request(Knowledge(Store())), engine="vosk"))
    assert "hint" not in tasks[0] and "hint_mode" not in tasks[0]
    assert result["text"] == FIXED
    assert result["raw_text"] == HEARD.lower()  # Vosk's own original is kept


def test_logs_carry_counts_only(whisper, caplog):
    caplog.set_level("INFO")
    service, _workers = whisper
    transcribe(service, Request(Knowledge(Store())))
    text = caplog.text
    assert "Voice vocabulary rebuilt terms=" in text and "STT corrections engine=whisper-small count=1" in text
    for private in ("setos guillellidos", "Training Setup Guideline", "COMPUTER PREPARATION", "cuáles son los pasos"):
        assert private not in text
