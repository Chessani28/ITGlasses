"""Whisper base (light, CPU) before giving up: when Whisper Small does not fit in RAM (3 GiB), the light model
(about 1.5 GiB) transcribes instead of answering 503 and sending the phone to Vosk."""
import asyncio

import pytest

from backend.app.knowledge.models import KnowledgeError
from backend.app.voice import service as voice
from backend.app.voice.service import VoiceService
from test_voice import wav
from test_voice_whisper import FakePersistentWorker, prepare_whisper

GIB = 1024 ** 3


def light_folder(tmp_path):
    folder = tmp_path / "faster-whisper-base"
    folder.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (folder / name).write_bytes(b"model")
    return folder.resolve()


def configure(monkeypatch, service, python, model, light, ram):
    monkeypatch.setattr(service, "_whisper_configuration",
                        lambda: ({"python": python, "model": model, "light": light}, None))
    monkeypatch.setattr(voice, "_available_system_memory", lambda: ram)

    async def nothing_to_free():
        return False
    monkeypatch.setattr(voice, "_free_ollama_embeddings", nothing_to_free)
    created = []

    def create_worker(*args, **kwargs):
        worker = FakePersistentWorker(args[0], args[1])
        created.append(str(args[1]))
        return worker
    monkeypatch.setattr(voice, "PersistentWhisperWorker", create_worker)
    return created


def test_low_ram_uses_whisper_base_instead_of_failing(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    light = light_folder(tmp_path)
    service = VoiceService(tmp_path)
    created = configure(monkeypatch, service, python, model, light, ram=2 * GIB)
    result = asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert created == [str(light)]
    assert result["whisper_model"] == "base"
    assert result["fallback_reason"] == "voice_resource_limit"


def test_enough_ram_keeps_whisper_small(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    light = light_folder(tmp_path)
    service = VoiceService(tmp_path)
    created = configure(monkeypatch, service, python, model, light, ram=4 * GIB)
    result = asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert created == [str(model)] and result["fallback_reason"] is None


def test_too_little_ram_even_for_base_is_still_a_503(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    light = light_folder(tmp_path)
    service = VoiceService(tmp_path)
    configure(monkeypatch, service, python, model, light, ram=int(1.2 * GIB))
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.status == 503 and error.value.code == "voice_resource_limit"


def test_without_a_light_model_low_ram_is_a_503_as_before(tmp_path, monkeypatch):
    python, model = prepare_whisper(tmp_path, monkeypatch)
    service = VoiceService(tmp_path)
    configure(monkeypatch, service, python, model, None, ram=2 * GIB)
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(service.run("stt", wav(), engine="whisper-small"))
    assert error.value.code == "voice_resource_limit"


def test_the_light_model_setting(tmp_path, monkeypatch):
    light = light_folder(tmp_path)
    monkeypatch.setenv("ITGLASSES_WHISPER_LIGHT_MODEL", str(light))
    assert voice.whisper_light_model() == light
    monkeypatch.setenv("ITGLASSES_WHISPER_LIGHT_MODEL", str(tmp_path / "missing"))
    assert voice.whisper_light_model() is None
    monkeypatch.delenv("ITGLASSES_WHISPER_LIGHT_MODEL")
    monkeypatch.setattr(voice, "DEFAULT_LIGHT_MODEL", light)
    assert voice.whisper_light_model() == light  # found where the voice models live, without configuration
