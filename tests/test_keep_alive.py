"""keep_alive of the chat model: 2 h in config.toml, ITGLASSES_OLLAMA_KEEP_ALIVE overrides it, and every chat call
(general chat, grounded answer, evidence selector and the startup warm-up) sends the same value."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from backend.app.core.config import Settings, load_settings
from backend.app.providers.ollama import OllamaProvider

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"details": {"format": "gguf"}}


def test_config_file_keeps_it_two_hours(monkeypatch):
    monkeypatch.delenv("ITGLASSES_OLLAMA_KEEP_ALIVE", raising=False)
    assert load_settings(ROOT / "config.toml").keep_alive == "2h"


@pytest.mark.parametrize("value", ["30m", "2h", "1.5h", "3600", "-1", "0"])
def test_environment_overrides_it(monkeypatch, value):
    monkeypatch.setenv("ITGLASSES_OLLAMA_KEEP_ALIVE", f" {value} ")
    assert load_settings(ROOT / "config.toml").keep_alive == value


def test_empty_environment_keeps_the_file_value(monkeypatch):
    monkeypatch.setenv("ITGLASSES_OLLAMA_KEEP_ALIVE", "  ")
    assert load_settings(ROOT / "config.toml").keep_alive == "2h"


@pytest.mark.parametrize("value", ["forever", "2 hours", "h", "", "1d", "-5m"])
def test_an_invalid_value_stops_the_startup_with_a_clear_error(value):
    with pytest.raises(ValueError, match="keep_alive"):
        Settings(keep_alive=value)


def test_chat_and_warm_up_send_the_same_keep_alive():
    seen = []
    def handler(request):
        payload = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, payload.get("keep_alive")))
        if request.url.path == "/api/show":
            return httpx.Response(200, json=LOCAL)
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True, "done_reason": "load"})
        return httpx.Response(200, json={"done": True, "message": {"content": "ok"}})
    provider = OllamaProvider(Settings(keep_alive="2h"), httpx.MockTransport(handler))
    asyncio.run(provider.warm_up())
    asyncio.run(provider.generate("hola"))
    asyncio.run(provider.generate_general("hola"))
    assert [value for path, value in seen if path in ("/api/generate", "/api/chat")] == ["2h", "2h", "2h"]
