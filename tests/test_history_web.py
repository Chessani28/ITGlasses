"""Stage "historial" 5: the history panel and the voice notice in the web, and the Vosk notice of the backend."""
import io
import re
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.knowledge.models import KnowledgeError
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice import routes as voice_routes

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
VRAM = "Whisper no disponible por memoria de la GPU; usando Vosk, la transcripción puede ser menos precisa."


def wav(rate=16000, seconds=0.2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(b"\0\0" * int(rate * seconds))
    return output.getvalue()


class Voice:
    """Whisper fails with the given code (503) or answers through the CPU fallback; Vosk always answers."""

    def __init__(self, whisper_code="voice_vram_limit", cpu_fallback=False):
        self.whisper_code, self.cpu_fallback, self.calls = whisper_code, cpu_fallback, []

    def status(self):
        return {"stt_available": True, "stt_engine": "vosk", "tts_available": True, "device": "cpu",
                "stt_engines": {"vosk": {"available": True}, "whisper-small": {"available": True}}}

    async def run(self, kind, payload, request=None, engine="vosk"):
        self.calls.append(engine)
        if engine == "whisper-small":
            if self.cpu_fallback:
                return {"text": "hola", "elapsed_ms": 9, "engine": "whisper-small", "device": "cpu",
                        "fallback_reason": "voice_vram_limit"}
            raise KnowledgeError(self.whisper_code, "Whisper is not available.", 503)
        return {"text": "hola", "elapsed_ms": 5, "engine": "vosk"}


@pytest.fixture
def client():
    def make(voice):
        return TestClient(create_app(MockLLM(), voice=voice))
    return make


def transcribe(c, engine):
    return c.post("/api/v1/voice/transcribe", content=wav(),
                  headers={"Content-Type": "audio/wav", "X-ITGlasses-STT-Engine": engine})


def test_the_vosk_retry_after_a_whisper_503_says_why(client):
    with client(Voice()) as c:
        assert transcribe(c, "whisper").status_code == 503
        retry = transcribe(c, "vosk").json()
        assert (retry["engine"], retry["fallback_from"], retry["fallback_reason"], retry["notice"]) == (
            "vosk", "whisper-small", "voice_vram_limit", VRAM)
        assert "notice" not in transcribe(c, "vosk").json()            # used once: a later Vosk turn is plain


@pytest.mark.parametrize(("code", "expected"), [("voice_gpu_unavailable", "no puede usar la GPU"),
                                                 ("voice_worker_startup", "no pudo iniciar en la GPU"),
                                                 ("voice_engine_unavailable", "Whisper no está disponible")])
def test_each_reason_has_its_notice(client, code, expected):
    with client(Voice(code)) as c:
        transcribe(c, "whisper-small")
        assert expected in transcribe(c, "vosk").json()["notice"]


def test_vosk_by_choice_or_long_after_has_no_notice(client, monkeypatch):
    with client(Voice()) as c:
        assert "notice" not in transcribe(c, "vosk").json()
        transcribe(c, "whisper")
        monkeypatch.setattr(voice_routes, "FALLBACK_SECONDS", -1)
        assert "notice" not in transcribe(c, "vosk").json()


def test_whisper_on_the_cpu_fallback_says_it_may_be_slower(client):
    with client(Voice(cpu_fallback=True)) as c:
        answer = transcribe(c, "whisper").json()
    assert answer["engine"] == "whisper-small" and "CPU" in answer["notice"]


def test_a_busy_voice_is_not_remembered_as_a_whisper_failure(client):
    class Busy(Voice):
        async def run(self, kind, payload, request=None, engine="vosk"):
            if engine == "whisper-small":
                raise KnowledgeError("voice_busy", "busy", 409)
            return await super().run(kind, payload, request, engine)
    with client(Busy()) as c:
        assert transcribe(c, "whisper").status_code == 409
        assert "notice" not in transcribe(c, "vosk").json()


def test_every_element_the_history_and_voice_scripts_use_exists_in_the_page():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', page))
    for name in ("history.mjs", "voice-ui.mjs"):
        used = set(re.findall(r'\$\("#?([a-z0-9-]+)"\)', (FRONTEND / name).read_text(encoding="utf-8-sig")))
        assert used and used <= ids, (name, sorted(used - ids))


def test_the_old_voice_texts_are_gone():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "LOCAL · CPU" not in page and "no conserva contexto semántico" not in page
    assert 'id="voice-engine-active">—<' in page
    for name in ("voice-ui.mjs", "voice-core.mjs", "index.html", "history.mjs"):
        text = (FRONTEND / name).read_text(encoding="utf-8-sig")
        assert not re.search(r"[ÂÃ][\x80-\xbf]|Ã[ -¿]|â€", text), name     # no mojibake left


def test_no_history_script_writes_html():
    source = (FRONTEND / "history.mjs").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source and "outerHTML" not in source
