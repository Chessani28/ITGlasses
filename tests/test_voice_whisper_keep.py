"""Voz 1, G: keep the persistent Whisper loaded between voice questions only with plenty of free RAM."""
import asyncio

import pytest

from backend.app.knowledge.models import KnowledgeError
from backend.app.voice import service as voice_service_module
from backend.app.voice.service import (WHISPER_KEEP_RAM_DEFAULT, WHISPER_MIN_LOAD_RAM, VoiceService,
                                       whisper_keep_ram_bytes)
from test_voice import wav
from test_voice_whisper import LegacyProcess, configure_fake_worker, prepare_whisper

GIB = 1024**3


@pytest.mark.parametrize(("value", "expected"), [
    (None, 8 * GIB), ("", 8 * GIB), ("  ", 8 * GIB),
    ("6", 6 * GIB), ("6.5", int(6.5 * GIB)), ("12", 12 * GIB),
    ("2", 3 * GIB), ("0.5", 3 * GIB),                      # never below the 3 GiB load limit
    ("0", 8 * GIB), ("-4", 8 * GIB), ("abc", 8 * GIB), ("nan", 8 * GIB), ("inf", 8 * GIB),
])
def test_keep_threshold_is_conservative_and_never_below_the_load_limit(value, expected, monkeypatch):
    monkeypatch.delenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", raising=False)
    if value is not None:
        monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", value)
    assert whisper_keep_ram_bytes() == expected
    assert WHISPER_KEEP_RAM_DEFAULT == 8 * GIB
    assert whisper_keep_ram_bytes() >= WHISPER_MIN_LOAD_RAM


def _prepare(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    workers = configure_fake_worker(monkeypatch, service, python, model)  # 4 GiB free while loading
    return service, workers


def _set_ram(monkeypatch, ram):
    monkeypatch.setattr(voice_service_module, "_available_system_memory", ram)


def test_chat_keeps_whisper_with_ram_above_the_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", "6")
    service, workers = _prepare(tmp_path, monkeypatch)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        _set_ram(monkeypatch, lambda: 7 * GIB)
        await service.release_whisper()
        assert workers[0].alive and workers[0].close_calls == 0
        await service.run("stt", wav(), engine="whisper-small")
        assert len(workers) == 1 and workers[0].start_calls == 1  # no reload for the next question
        await service.shutdown()
        assert workers[0].close_calls == 1  # shutdown always unloads

    asyncio.run(exercise())


@pytest.mark.parametrize(("keep", "free_gib"), [(None, 7), ("6", 5)])
def test_chat_unloads_whisper_with_ram_below_the_threshold(keep, free_gib, tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", raising=False)
    if keep:
        monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", keep)
    service, workers = _prepare(tmp_path, monkeypatch)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        _set_ram(monkeypatch, lambda: free_gib * GIB)  # 7 GiB is below the 8 GiB default
        await service.release_whisper()
        assert workers[0].close_calls == 1 and not workers[0].alive

    asyncio.run(exercise())


def test_unmeasurable_memory_unloads_whisper(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", "3")
    service, workers = _prepare(tmp_path, monkeypatch)

    def broken():
        raise OSError("no counters")

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        _set_ram(monkeypatch, broken)
        await service.release_whisper()
        assert workers[0].close_calls == 1

    asyncio.run(exercise())


def test_active_transcription_still_refuses_the_release(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", "3")
    service, workers = _prepare(tmp_path, monkeypatch)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        _set_ram(monkeypatch, lambda: 16 * GIB)
        service._whisper_active = True
        try:
            with pytest.raises(KnowledgeError) as error:
                await service.release_whisper()
            assert error.value.code == "voice_busy" and error.value.status == 409
        finally:
            service._whisper_active = False
            await service.shutdown()

    asyncio.run(exercise())


def test_speech_synthesis_also_keeps_whisper_with_plenty_of_ram(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_WHISPER_KEEP_RAM_GIB", "6")
    service, workers = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_tts_available", lambda: True)

    async def spawn_piper(*_args, **_kwargs):
        return LegacyProcess({"engine": "piper"})

    monkeypatch.setattr(voice_service_module, "_spawn_worker", spawn_piper)

    async def exercise():
        await service.run("stt", wav(), engine="whisper-small")
        _set_ram(monkeypatch, lambda: 10 * GIB)
        piper = await service.run("tts", {"text": "prueba", "speaker": 0, "speed": 1})
        assert piper["audio"] == b"wav-data"
        assert workers[0].alive and workers[0].close_calls == 0
        await service.shutdown()

    asyncio.run(exercise())
