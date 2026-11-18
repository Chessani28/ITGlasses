import asyncio
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.knowledge.models import KnowledgeError
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice import service as voice_service_module
from backend.app.voice.persistent_worker import (
    WorkerCrashedError,
    WorkerMemoryLimitError,
    WorkerTimeoutError,
)
from backend.app.voice.service import VoiceService
from test_voice import wav


def prepare_whisper(tmp_path, monkeypatch, dependency_exit=0):
    python = tmp_path / "whisper-python.exe"
    python.write_bytes(b"configured interpreter placeholder")
    model = tmp_path / "whisper-small"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (model / name).write_bytes(b"model")
    monkeypatch.setenv("ITGLASSES_WHISPER_PYTHON", str(python))
    monkeypatch.setenv("ITGLASSES_WHISPER_MODEL", str(model))
    monkeypatch.setattr(
        voice_service_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], dependency_exit),
    )
    return python.resolve(), model.resolve()


class FakePersistentWorker:
    def __init__(self, python, model, behavior=None):
        self.configuration = (str(python), str(model))
        self.behavior = behavior
        self.start_calls = 0
        self.transcribe_calls = 0
        self.close_calls = 0
        self.terminate_calls = 0
        self.alive = False
        self.last_used = None
        self.minimum_available_ram_bytes = 4 * 1024**3
        self.peak_working_set_bytes = 800 * 1024**2
        self.peak_private_bytes = 2 * 1024**3
        self.startup_seconds = 3.2
        self.abort_event = threading.Event()
        self.memory_snapshot = {"private_bytes": self.peak_private_bytes}

    def start(self):
        self.start_calls += 1
        self.alive = True
        self.last_used = time.monotonic()
        return {"model_load_seconds": 3.0}

    def transcribe(self, audio):
        self.transcribe_calls += 1
        if self.behavior:
            result = self.behavior(self, audio)
        else:
            result = {
                "text": "transcript",
                "raw_text": "transcript",
                "normalized_text": "transcript",
                "engine": "whisper-small",
                "inference_seconds": 0.1,
            }
        self.last_used = time.monotonic()
        return result

    def close(self):
        self.close_calls += 1
        self.alive = False
        return {"exited": True, "forced": False}

    def terminate(self):
        self.terminate_calls += 1
        self.alive = False
        return []

    def abort(self):
        self.abort_event.set()
        self.alive = False


def configure_fake_worker(monkeypatch, service, python, model, behaviors=None):
    monkeypatch.setattr(
        service,
        "_whisper_configuration",
        lambda: ({"python": python, "model": model}, None),
    )
    monkeypatch.setattr(
        voice_service_module, "_available_system_memory", lambda: 4 * 1024**3
    )
    workers = []
    behaviors = iter(behaviors or [])

    def create_worker(*args):
        behavior = next(behaviors, None)
        worker = FakePersistentWorker(python, model, behavior)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        voice_service_module, "PersistentWhisperWorker", create_worker
    )
    return workers


class LegacyProcess:
    def __init__(self, response):
        self.response = response
        self.returncode = None

    async def communicate(self, payload):
        task = json.loads(payload)
        if task["kind"] == "tts":
            Path(task["output"]).write_bytes(b"wav-data")
        self.returncode = 0
        return json.dumps(self.response).encode(), b""

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


def test_vosk_remains_the_default_engine():
    assert VoiceService().status()["stt_engine"] == "vosk"


def test_whisper_is_reported_unavailable_without_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_WHISPER_PYTHON", raising=False)
    monkeypatch.delenv("ITGLASSES_WHISPER_MODEL", raising=False)
    status = VoiceService(tmp_path).status()
    assert not status["stt_engines"]["whisper-small"]["available"]
    assert "ITGLASSES_WHISPER_PYTHON" in status["stt_engines"]["whisper-small"]["reason"]
    assert "ITGLASSES_WHISPER_MODEL" in status["stt_engines"]["whisper-small"]["reason"]


def test_whisper_status_identifies_the_missing_backend_environment_variable(
    tmp_path, monkeypatch
):
    prepare_whisper(tmp_path, monkeypatch)
    monkeypatch.delenv("ITGLASSES_WHISPER_MODEL")
    status = VoiceService(tmp_path).status()
    reason = status["stt_engines"]["whisper-small"]["reason"]
    assert "ITGLASSES_WHISPER_MODEL" in reason
    assert "ITGLASSES_WHISPER_PYTHON" not in reason


def test_whisper_missing_dependencies_are_not_cached(tmp_path, monkeypatch):
    prepare_whisper(tmp_path, monkeypatch, dependency_exit=1)
    service = VoiceService(tmp_path)
    assert not service.status()["stt_engines"]["whisper-small"]["available"]
    monkeypatch.setattr(
        voice_service_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )
    assert service.status()["stt_engines"]["whisper-small"]["available"]


def test_whisper_successful_dependency_probe_is_cached_and_config_changes_are_seen(
    tmp_path, monkeypatch
):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    calls = []

    def probe(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(voice_service_module.subprocess, "run", probe)
    service = VoiceService(tmp_path)
    assert service.status()["stt_engines"]["whisper-small"]["available"]
    assert service.status()["stt_engines"]["whisper-small"]["available"]
    assert len(calls) == 1
    monkeypatch.setenv("ITGLASSES_WHISPER_MODEL", str(tmp_path / "missing"))
    assert not service.status()["stt_engines"]["whisper-small"]["available"]


def test_persistent_worker_starts_once_and_preserves_raw_engine_identity(
    tmp_path, monkeypatch
):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)

    async def exercise():
        first = await service.run("stt", wav(), engine="whisper-small")
        second = await service.run("stt", wav(), engine="whisper-small")
        await service.shutdown()
        return first, second

    first, second = asyncio.run(exercise())
    assert len(workers) == 1
    assert workers[0].start_calls == 1
    assert workers[0].transcribe_calls == 2
    assert workers[0].close_calls == 1
    assert first["engine"] == second["engine"] == "whisper-small"
    assert first["raw_text"] == first["text"] == "transcript"
    assert first["worker_startup_seconds"] == 3.0
    assert second["worker_startup_seconds"] == 0


def test_transcribe_route_dispatches_the_explicit_engine_and_reports_it(tmp_path):
    class VoiceSpy:
        def __init__(self):
            self.engines = []

        def status(self):
            return {"stt_available": True, "stt_engines": {"whisper-small": {"available": True}}}

        async def run(self, kind, payload, request, engine):
            self.engines.append((kind, engine))
            return {"text": "raw result", "raw_text": "raw result", "engine": engine}

    voice = VoiceSpy()
    with TestClient(create_app(MockLLM(), voice=voice)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            content=wav(),
            headers={
                "Content-Type": "audio/wav",
                "X-ITGlasses-STT-Engine": "whisper-small",
            },
        )
    assert response.status_code == 200
    assert voice.engines == [("stt", "whisper-small")]
    assert response.json()["engine"] == "whisper-small"


def test_unavailable_explicit_whisper_request_never_falls_back_to_vosk(tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_WHISPER_PYTHON", raising=False)
    monkeypatch.delenv("ITGLASSES_WHISPER_MODEL", raising=False)
    voice = VoiceService(tmp_path)
    with TestClient(create_app(MockLLM(), voice=voice)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            content=wav(),
            headers={
                "Content-Type": "audio/wav",
                "X-ITGlasses-STT-Engine": "whisper-small",
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_engine_unavailable"
    assert "ITGLASSES_WHISPER_PYTHON" in response.json()["error"]["message"]


def test_whisper_missing_model_fails_before_worker_start(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    monkeypatch.setattr(
        service,
        "_whisper_configuration",
        lambda: (None, "Configured Whisper Small model is incomplete or missing."),
    )
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.code == "voice_engine_unavailable"
    assert not service.busy


def test_invalid_audio_is_rejected_without_starting_worker(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", b"not a wav", engine="whisper-small"))
    assert error.value.code == "audio_size"
    assert workers == []


def test_worker_crash_does_not_fallback_and_next_request_recovers(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)

    def crash(worker, _audio):
        worker.alive = False
        raise WorkerCrashedError("simulated worker exit")

    service = VoiceService(tmp_path)
    workers = configure_fake_worker(
        monkeypatch, service, python, model, [crash, None]
    )

    async def exercise():
        with pytest.raises(KnowledgeError) as error:
            await service.run("stt", wav(), engine="whisper-small")
        assert error.value.code == "voice_inference_failed"
        result = await service.run("stt", wav(), engine="whisper-small")
        await service.shutdown()
        return result

    result = asyncio.run(exercise())
    assert len(workers) == 2
    assert result["engine"] == "whisper-small"


def test_whisper_timeout_and_emergency_memory_abort_are_explicit(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)

    def timeout(worker, _audio):
        worker.alive = False
        raise WorkerTimeoutError("timeout")

    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model, [timeout])
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.code == "voice_timeout"
    assert workers[0].alive is False

    def low_memory(worker, _audio):
        worker.alive = False
        raise WorkerMemoryLimitError("RAM below threshold")

    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model, [low_memory])
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.code == "voice_resource_limit"
    assert workers[0].alive is False


def test_whisper_startup_is_blocked_below_three_gib(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)
    monkeypatch.setattr(
        voice_service_module, "_available_system_memory", lambda: 2 * 1024**3
    )
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.code == "voice_resource_limit"
    assert workers == []


def test_idle_timeout_and_shutdown_release_the_worker(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        workers[0].last_used = time.monotonic() - 61
        await asyncio.sleep(0.35)
        assert workers[0].close_calls == 1
        await service.run("stt", wav(), engine="whisper-small")
        await service.shutdown()

    asyncio.run(exercise())
    assert workers[1].close_calls == 1


def test_concurrent_whisper_requests_are_serialized(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def wait_for_release(worker, _audio):
        entered.set()
        release.wait(timeout=2)
        return {
            "text": "serial",
            "raw_text": "serial",
            "normalized_text": "serial",
            "engine": "whisper-small",
        }

    service = VoiceService(tmp_path)
    configure_fake_worker(monkeypatch, service, python, model, [wait_for_release])

    async def exercise():
        first = asyncio.create_task(
            service.run("stt", wav(), engine="whisper-small")
        )
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(KnowledgeError) as error:
            await service.run("stt", wav(), engine="whisper-small")
        assert error.value.code == "voice_busy"
        release.set()
        await first
        await service.shutdown()

    asyncio.run(exercise())


def test_request_cancellation_aborts_owned_worker(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    entered = threading.Event()

    def wait_for_abort(worker, _audio):
        entered.set()
        worker.abort_event.wait(timeout=2)
        raise WorkerCrashedError("aborted")

    service = VoiceService(tmp_path)
    workers = configure_fake_worker(
        monkeypatch, service, python, model, [wait_for_abort]
    )

    async def exercise():
        task = asyncio.create_task(
            service.run("stt", wav(), engine="whisper-small")
        )
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert workers[0].abort_event.is_set()
    assert not service.busy


def test_returning_to_vosk_and_piper_releases_whisper(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)
    monkeypatch.setattr(service, "_vosk_available", lambda: True)
    monkeypatch.setattr(service, "_tts_available", lambda: True)

    async def spawn(*_args, **_kwargs):
        return LegacyProcess({"text": "vosk result", "engine": "vosk"})

    monkeypatch.setattr(voice_service_module, "_spawn_worker", spawn)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        vosk = await service.run("stt", wav())
        assert workers[0].close_calls == 1
        assert vosk["engine"] == "vosk"

        await service.run("stt", wav(), engine="whisper-small")

        async def spawn_piper(*_args, **_kwargs):
            return LegacyProcess({"engine": "piper"})

        monkeypatch.setattr(voice_service_module, "_spawn_worker", spawn_piper)
        piper = await service.run(
            "tts", {"text": "test", "speaker": 0, "speed": 1}
        )
        assert workers[1].close_calls == 1
        assert piper["audio"] == b"wav-data"
        await service.shutdown()

    asyncio.run(exercise())


def test_chat_and_backend_shutdown_release_whisper():
    calls = []

    class VoiceSpy:
        async def release_whisper(self):
            calls.append("release")

        async def shutdown(self):
            calls.append("shutdown")

    class KnowledgeSpy:
        """Same contract as KnowledgeService.query: always a KnowledgeAnswer."""

        async def query(self, payload):
            from backend.app.knowledge.models import KnowledgeAnswer
            return KnowledgeAnswer(status="insufficient_evidence", answer=payload.message,
                                   conversation_id="00000000-0000-0000-0000-000000000000",
                                   intent="information")

    with TestClient(
        create_app(MockLLM(), knowledge=KnowledgeSpy(), voice=VoiceSpy())
    ) as client:
        assert client.post("/api/v1/chat", json={"message": "test"}).status_code == 200
        assert client.post(
            "/api/v1/assistant/chat", json={"message": "¿Qué es DNS?"}
        ).status_code == 200
        assert client.post(
            "/api/v1/knowledge/chat", json={"message": "test"}
        ).status_code == 200
    assert calls == ["release", "release", "release", "shutdown"]
