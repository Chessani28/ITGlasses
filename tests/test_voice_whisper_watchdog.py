"""Whisper busy flag: always cleared, and a stuck operation never blocks chat forever (watchdog)."""
import asyncio

import pytest

from backend.app.knowledge.models import KnowledgeError
from backend.app.voice.service import WHISPER_WATCHDOG_SECONDS, VoiceService


def test_flag_is_cleared_when_the_stt_operation_fails(tmp_path, monkeypatch):
    service = VoiceService(tmp_path)

    async def failing(*_args, **_kwargs):
        raise KnowledgeError("voice_inference_failed", "boom", 502)

    monkeypatch.setattr(service, "_run_persistent_whisper_request", failing)

    async def exercise():
        with pytest.raises(KnowledgeError):
            await service._run_persistent_whisper(b"", None)
        assert service._whisper_active is False and service.busy is False
        await service.release_whisper()  # chat is no longer refused

    asyncio.run(exercise())


def test_flag_is_cleared_when_the_stt_operation_is_cancelled(tmp_path, monkeypatch):
    service = VoiceService(tmp_path)
    entered = asyncio.Event()

    async def hang(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_run_persistent_whisper_request", hang)

    async def exercise():
        task = asyncio.create_task(service._run_persistent_whisper(b"", None))
        await entered.wait()
        assert service._whisper_active is True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert service._whisper_active is False and service.busy is False

    asyncio.run(exercise())


def test_watchdog_cancels_a_stuck_operation(tmp_path, monkeypatch):
    service = VoiceService(tmp_path)
    entered = asyncio.Event()

    async def hang(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_run_persistent_whisper_request", hang)

    async def exercise():
        task = asyncio.create_task(service._run_persistent_whisper(b"", None))
        await entered.wait()
        # Younger than the watchdog: still refused, not cancelled.
        with pytest.raises(KnowledgeError) as error:
            await service.release_whisper()
        assert error.value.code == "voice_busy"
        assert not task.done()
        # Older than the watchdog: this request is still refused, the stuck one is cancelled.
        service._whisper_active_since -= WHISPER_WATCHDOG_SECONDS + 1
        with pytest.raises(KnowledgeError):
            await service.release_whisper()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert service._whisper_active is False and service.busy is False
        await service.release_whisper()  # the next chat request goes through

    asyncio.run(exercise())


def test_orphaned_flag_is_cleared(tmp_path):
    service = VoiceService(tmp_path)

    async def exercise():
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        service._whisper_active_since = 0.0
        service._whisper_active_task = finished
        service._whisper_active_op = "stt"
        service.busy = True
        await service.release_whisper()
        assert service._whisper_active is False and service.busy is False

    asyncio.run(exercise())


def test_forced_flag_still_refuses_and_is_never_cancelled(tmp_path):
    """Tests that force _whisper_active = True keep getting voice_busy (no owning task to cancel)."""
    service = VoiceService(tmp_path)

    async def exercise():
        service._whisper_active = True
        service._whisper_active_since -= WHISPER_WATCHDOG_SECONDS + 1
        with pytest.raises(KnowledgeError) as error:
            await service.release_whisper()
        assert error.value.code == "voice_busy"
        service._whisper_active = False
        await service.release_whisper()

    asyncio.run(exercise())
