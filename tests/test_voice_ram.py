"""Whisper antes que la velocidad: si Whisper no cabe en RAM, primero se le pide a Ollama que descargue el modelo de
embeddings (que puede quedarse cargado cuando sobra RAM) y se vuelve a medir."""
import asyncio

from backend.app.voice import service as voice

GIB = 1024 ** 3


def test_enough_ram_frees_nothing():
    freed = []

    async def free():
        freed.append(True)
        return True
    assert asyncio.run(voice.ram_for_whisper_load(measure=lambda: 4 * GIB, free=free)) == 4 * GIB
    assert freed == []


def test_low_ram_frees_the_embedding_model_and_measures_again():
    readings = iter([2 * GIB, int(3.5 * GIB)])
    freed = []

    async def free():
        freed.append(True)
        return True
    assert asyncio.run(voice.ram_for_whisper_load(measure=lambda: next(readings), free=free)) == int(3.5 * GIB)
    assert freed == [True]


def test_if_nothing_could_be_freed_the_first_reading_stands():
    async def free():
        return False
    assert asyncio.run(voice.ram_for_whisper_load(measure=lambda: 2 * GIB, free=free)) == 2 * GIB


def test_the_whisper_load_uses_it():
    import inspect
    source = inspect.getsource(voice.VoiceService._whisper_worker_for)
    assert "ram_for_whisper_load" in source
