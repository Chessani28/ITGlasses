"""Startup warm-up of the chat model: retried in the background while Ollama is not up yet (e.g. after a reboot)."""
import asyncio
import logging
import time

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import _warm_up_with_retries, create_app


def scripted(results):
    calls = []

    async def warm_up():
        calls.append(len(calls) + 1)
        outcome = results[min(len(calls), len(results)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return warm_up, calls


def test_retries_until_ollama_answers_and_logs_it(caplog):
    warm_up, calls = scripted([False, RuntimeError("down"), True])
    with caplog.at_level(logging.INFO, logger="backend.app.main"):
        assert asyncio.run(_warm_up_with_retries(warm_up, interval=0.01, window=5)) is True
    assert calls == [1, 2, 3]
    assert "ollama_warmup ready attempts=3" in caplog.text
    assert "attempt=2 error=RuntimeError" in caplog.text


def test_a_first_success_is_not_retried_nor_logged_again(caplog):
    warm_up, calls = scripted([True])
    with caplog.at_level(logging.INFO, logger="backend.app.main"):
        assert asyncio.run(_warm_up_with_retries(warm_up, interval=0.01, window=5)) is True
    assert calls == [1] and "ollama_warmup ready" not in caplog.text


def test_gives_up_after_the_window(caplog):
    warm_up, calls = scripted([False])
    with caplog.at_level(logging.WARNING, logger="backend.app.main"):
        assert asyncio.run(_warm_up_with_retries(warm_up, interval=0.01, window=0.05)) is False
    assert 1 < len(calls) <= 6
    assert "ollama_warmup gave up" in caplog.text


def test_defaults_retry_every_5_s_for_about_2_minutes():
    assert main.OLLAMA_WARMUP_RETRY_SECONDS == 5 and main.OLLAMA_WARMUP_RETRY_WINDOW_SECONDS == 120


class DownProvider:
    def __init__(self):
        self.attempts = 0

    async def warm_up(self):
        self.attempts += 1
        return False


def test_startup_is_not_blocked_while_ollama_is_down(monkeypatch):
    monkeypatch.setattr(main, "OLLAMA_WARMUP_RETRY_SECONDS", 3600)
    provider = DownProvider()
    with TestClient(create_app(provider, warmup=True)) as client:
        assert client.get("/health").status_code == 200  # served while the retry waits
        for _ in range(200):  # the first attempt runs in the background
            if provider.attempts:
                break
            time.sleep(0.01)
    assert provider.attempts == 1  # the pending retry is cancelled at shutdown
