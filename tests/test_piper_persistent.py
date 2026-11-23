"""Persistent Piper (ITGLASSES_PIPER_PERSISTENT) and the general notice that is shown but not read aloud.

No model or Piper install is needed: the child protocol runs with injected load/synth functions, and the
persistent worker runs against fake processes.
"""
import asyncio
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.assistant.service import GENERAL_NOTICE
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice import piper_worker
from backend.app.voice import service as voice_service_module
from backend.app.voice.normalize import GENERAL_NOTICE as SPOKEN_NOTICE, strip_general_notice
from backend.app.voice.piper_persistent import PersistentPiper, PiperWorkerError
from backend.app.voice.service import VoiceService, piper_persistent_enabled
from test_knowledge import service as knowledge_service


# --- The notice: in the chat, not in the voice ------------------------------------------------------------------

def test_notice_constant_is_the_one_the_assistant_shows():
    assert SPOKEN_NOTICE == GENERAL_NOTICE


def test_only_the_notice_is_removed():
    answer = GENERAL_NOTICE + "\nUna dirección IP identifica a un equipo en la red."
    assert strip_general_notice(answer) == "Una dirección IP identifica a un equipo en la red."
    assert strip_general_notice("  " + GENERAL_NOTICE + " Texto.") == "Texto."
    plain = "Paso 1: abre la consola.\nPaso 2: escribe ipconfig."
    assert strip_general_notice(plain) == plain
    mention = "La app muestra «" + GENERAL_NOTICE + "» arriba."
    assert strip_general_notice(mention) == mention, "only at the start of a line"
    assert strip_general_notice(GENERAL_NOTICE) == GENERAL_NOTICE, "never an empty synthesis"


class RecordingVoice:
    def __init__(self):
        self.texts = []

    def status(self):
        return {"tts_available": True}

    async def run(self, kind, payload, request=None, engine="vosk"):
        self.texts.append(payload["text"])
        return {"audio": b"RIFF....WAVE", "elapsed_ms": 1}


def test_synthesize_endpoint_does_not_read_the_notice(tmp_path):
    voice = RecordingVoice()
    with TestClient(create_app(MockLLM(), knowledge_service(tmp_path), voice)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": GENERAL_NOTICE + "\nDNS traduce nombres."})
    assert response.status_code == 200
    assert voice.texts == ["DNS traduce nombres."]


# --- The child process protocol -----------------------------------------------------------------------------------

def test_worker_loads_once_and_serves_every_line(tmp_path):
    loads, done = [], []
    out = io.StringIO()
    stdin = io.StringIO("\n".join([
        json.dumps({"text": "hola", "speaker": 0, "speed": 1.0, "output": str(tmp_path / "a.wav")}),
        "{not json",
        json.dumps({"text": "adiós", "speaker": 1, "speed": 1.15, "output": str(tmp_path / "b.wav")}),
    ]) + "\n")
    code = piper_worker.serve(tmp_path, stdin, out, load=lambda m: (loads.append(m) or "voice", ["CPUExecutionProvider"]),
                              synth=lambda voice, task: done.append(task["text"]))
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 0 and len(loads) == 1
    assert lines[0]["ready"] is True and lines[0]["providers"] == ["CPUExecutionProvider"]
    assert lines[1]["ok"] is True and lines[2]["error"] == "synthesis" and lines[3]["ok"] is True
    assert done == ["hola", "adiós"]
    assert "hola" not in out.getvalue(), "never echoes the text"


def test_worker_reports_a_failed_load(tmp_path):
    out = io.StringIO()
    def broken(models):
        raise ImportError("piper")
    assert piper_worker.serve(tmp_path, io.StringIO(""), out, load=broken) == 1
    assert json.loads(out.getvalue()) == {"error": "startup", "error_type": "ImportError"}


def test_worker_is_cpu_only():
    source = Path(piper_worker.__file__).read_text(encoding="utf-8")
    assert 'providers=["CPUExecutionProvider"]' in source and "CUDA" in source
    parent = Path(voice_service_module.__file__).with_name("piper_persistent.py").read_text(encoding="utf-8")
    assert '"CUDA_VISIBLE_DEVICES": "-1"' in parent


# --- The persistent worker against fake processes -----------------------------------------------------------------

class FakeProcess:
    def __init__(self, replies, ready=True, hang=False):
        self.replies, self.ready, self.hang = list(replies), ready, hang
        self.alive, self.requests = True, []

    def read_line(self):
        return json.dumps({"ready": True, "providers": ["CPUExecutionProvider"]} if self.ready else {"error": "startup"})

    def request(self, line):
        self.requests.append(json.loads(line))
        if self.hang:
            import time
            time.sleep(2)
        task = json.loads(line)
        reply = self.replies.pop(0)
        if reply.get("ok"):
            Path(task["output"]).write_bytes(b"RIFF-persistent")
        return json.dumps(reply)

    def terminate(self):
        self.alive = False


def persistent(tmp_path, processes, **kwargs):
    started = []
    def start(models, temporary, root):
        process = processes.pop(0)
        started.append(process)
        return process
    return PersistentPiper(tmp_path, tmp_path, tmp_path, start=start, **kwargs), started


def task(tmp_path, name="out.wav"):
    return {"text": "hola", "speaker": 0, "speed": 1.0, "output": str(tmp_path / name)}


def test_one_process_serves_many_answers(tmp_path):
    worker, started = persistent(tmp_path, [FakeProcess([{"ok": True}, {"ok": True}])])
    asyncio.run(worker.synthesize(task(tmp_path, "1.wav")))
    asyncio.run(worker.synthesize(task(tmp_path, "2.wav")))
    assert len(started) == 1 and len(started[0].requests) == 2


def test_failure_kills_the_process_and_the_next_answer_starts_a_new_one(tmp_path):
    first, second = FakeProcess([{"error": "synthesis"}]), FakeProcess([{"ok": True}])
    worker, started = persistent(tmp_path, [first, second])
    with pytest.raises(PiperWorkerError, match="synthesis_failed"):
        asyncio.run(worker.synthesize(task(tmp_path)))
    assert first.alive is False
    asyncio.run(worker.synthesize(task(tmp_path)))
    assert started == [first, second]


def test_a_hung_worker_times_out_and_is_killed(tmp_path):
    hung = FakeProcess([{"ok": True}], hang=True)
    worker, _ = persistent(tmp_path, [hung], request_timeout=0.2)
    with pytest.raises(PiperWorkerError, match="request_timeout"):
        asyncio.run(worker.synthesize(task(tmp_path)))
    assert hung.alive is False


def test_a_worker_that_cannot_load_is_reported(tmp_path):
    worker, _ = persistent(tmp_path, [FakeProcess([], ready=False)])
    with pytest.raises(PiperWorkerError, match="startup_failed"):
        asyncio.run(worker.synthesize(task(tmp_path)))


def test_two_phones_at_once_are_served_one_after_the_other(tmp_path):
    process = FakeProcess([{"ok": True}, {"ok": True}])
    worker, started = persistent(tmp_path, [process])
    async def both():
        await asyncio.gather(worker.synthesize(task(tmp_path, "a.wav")), worker.synthesize(task(tmp_path, "b.wav")))
    asyncio.run(both())
    assert len(started) == 1 and [r["output"][-5:] for r in process.requests] == ["a.wav", "b.wav"]


# --- VoiceService: persistent first, per-request as automatic fallback ---------------------------------------------

class LegacyProcess:
    """The per-request worker (the method before), as test_voice_whisper.py fakes it."""
    spawned = 0

    def __init__(self):
        LegacyProcess.spawned += 1
        self.returncode = None

    async def communicate(self, payload):
        Path(json.loads(payload)["output"]).write_bytes(b"RIFF-per-request")
        self.returncode = 0
        return json.dumps({"engine": "piper", "cpu_seconds": 5.0}).encode(), b""

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def voice(tmp_path, monkeypatch):
    LegacyProcess.spawned = 0
    async def spawn(*args, **kwargs):
        return LegacyProcess()
    monkeypatch.setattr(voice_service_module, "_spawn_worker", spawn)
    monkeypatch.setattr(VoiceService, "_tts_available", lambda self: True)
    return VoiceService(root=tmp_path)


def use_persistent(monkeypatch, voice, tmp_path, processes):
    monkeypatch.setenv("ITGLASSES_PIPER_PERSISTENT", "1")
    worker, started = persistent(tmp_path, processes)
    voice._piper = worker
    return started


def test_persistent_is_on_by_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("ITGLASSES_PIPER_PERSISTENT", raising=False)
    assert piper_persistent_enabled() is True
    for off in ("0", "false", "off", "no", "OFF"):
        assert piper_persistent_enabled(off) is False


def test_tts_uses_the_persistent_worker(voice, monkeypatch, tmp_path):
    use_persistent(monkeypatch, voice, tmp_path, [FakeProcess([{"ok": True, "cpu_seconds": 0.9}])])
    result = asyncio.run(voice.run("tts", {"text": "hola", "speaker": 0, "speed": 1}))
    assert result["audio"] == b"RIFF-persistent" and result["persistent"] is True
    assert LegacyProcess.spawned == 0
    assert voice.busy is False


def test_failure_falls_back_to_the_per_request_worker_and_pauses(voice, monkeypatch, tmp_path):
    started = use_persistent(monkeypatch, voice, tmp_path, [FakeProcess([{"error": "synthesis"}])])
    first = asyncio.run(voice.run("tts", {"text": "hola", "speaker": 0, "speed": 1}))
    assert first["audio"] == b"RIFF-per-request" and LegacyProcess.spawned == 1
    second = asyncio.run(voice.run("tts", {"text": "otra", "speaker": 0, "speed": 1}))
    assert second["audio"] == b"RIFF-per-request" and LegacyProcess.spawned == 2
    assert len(started) == 1, "paused after a failure: no new start for the next answer"


def test_turned_off_uses_the_per_request_worker(voice, monkeypatch, tmp_path):
    started = use_persistent(monkeypatch, voice, tmp_path, [FakeProcess([{"ok": True}])])
    monkeypatch.setenv("ITGLASSES_PIPER_PERSISTENT", "0")
    result = asyncio.run(voice.run("tts", {"text": "hola", "speaker": 0, "speed": 1}))
    assert result["audio"] == b"RIFF-per-request" and started == []


def test_still_one_speech_operation_at_a_time(voice):
    voice.busy = True
    with pytest.raises(Exception) as error:
        asyncio.run(voice.run("tts", {"text": "hola", "speaker": 0, "speed": 1}))
    assert getattr(error.value, "code", None) == "voice_busy"


def test_shutdown_stops_the_persistent_worker(voice, monkeypatch, tmp_path):
    started = use_persistent(monkeypatch, voice, tmp_path, [FakeProcess([{"ok": True}])])
    asyncio.run(voice.run("tts", {"text": "hola", "speaker": 0, "speed": 1}))
    asyncio.run(voice.shutdown())
    assert started[0].alive is False
