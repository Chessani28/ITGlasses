"""Local model calls: the general chat gets 768 tokens and keeps what was generated at the limit; failures say
their cause and leave one log line (codes only); the chat model is warmed up at startup; logs go to
logs\\backend.log."""
import asyncio
import json
import logging
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.assistant.service import (GENERAL_NOT_INSTALLED, GENERAL_NOT_RUNNING, GENERAL_NOTICE,
                                           GENERAL_TIMEOUT, GENERAL_UNAVAILABLE, UnifiedAssistantService)
from backend.app.core.config import Settings, load_settings
from backend.app.core.logging_setup import BACKUPS, LOG_FILE, MAX_BYTES, configure_logging
from backend.app.knowledge.embeddings import OllamaEmbeddings
from backend.app.knowledge.grounding import EvidenceSelector
from backend.app.main import create_app, ollama_warmup_enabled
from backend.app.providers.base import ModelUnavailableError, ProviderTimeoutError, ProviderUnavailableError
from backend.app.providers.ollama import GENERAL_MAX_OUTPUT_TOKENS, TRUNCATED_NOTE, OllamaProvider
from backend.app.providers.base import ProviderOutputLimitError
from test_assistant_service import StubWebSearch
from test_general_after_manuals import NoEvidenceForConcepts

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"details": {"format": "gguf"}}
VPN = "Explícame con detalle cómo funciona una VPN"


def adapter(chat=None, generate=None, seen=None):
    def handler(request):
        payload = json.loads(request.content) if request.content else {}
        if seen is not None:
            seen.append((request.url.path, payload))
        if request.url.path == "/api/show":
            return httpx.Response(200, json=LOCAL)
        if request.url.path == "/api/generate":
            return generate(payload) if generate else httpx.Response(200, json={"done": True, "done_reason": "load"})
        return chat(payload)
    return OllamaProvider(Settings(), httpx.MockTransport(handler))


def length(payload):
    return httpx.Response(200, json={"done": True, "done_reason": "length", "eval_count": payload["options"]["num_predict"],
                                     "message": {"content": "Una VPN crea un túnel cifrado entre"}})


# --- General chat output ----------------------------------------------------------------------------------------

def test_general_chat_gets_768_tokens_and_keeps_what_was_generated_at_the_limit():
    seen = []
    provider = adapter(length, seen=seen)
    answer = asyncio.run(provider.generate_general("pregunta"))
    assert answer == "Una VPN crea un túnel cifrado entre\n\n" + TRUNCATED_NOTE
    chat = [payload for path, payload in seen if path == "/api/chat"][-1]
    assert chat["options"]["num_predict"] == GENERAL_MAX_OUTPUT_TOKENS == 768
    assert chat["options"]["num_ctx"] == 2048


def test_api_v1_chat_keeps_256_tokens_and_the_explicit_output_limit_error():
    seen = []
    provider = adapter(length, seen=seen)
    with pytest.raises(ProviderOutputLimitError):
        asyncio.run(provider.generate("pregunta"))
    assert [p for path, p in seen if path == "/api/chat"][-1]["options"]["num_predict"] == 256


def test_an_empty_answer_at_the_limit_is_still_an_error():
    provider = adapter(lambda p: httpx.Response(200, json={"done": True, "done_reason": "length", "message": {"content": " "}}))
    with pytest.raises(ProviderOutputLimitError):
        asyncio.run(provider.generate_general("pregunta"))


def test_the_assistant_returns_the_truncated_general_answer_with_its_notice():
    service = UnifiedAssistantService(NoEvidenceForConcepts(), adapter(length), web_search=StubWebSearch())
    result = asyncio.run(service.chat(VPN, session_id=str(uuid.uuid4())))
    assert result["route"] == "general"
    assert result["answer"] == GENERAL_NOTICE + "\nUna VPN crea un túnel cifrado entre\n\n" + TRUNCATED_NOTE


# --- Failure messages and log lines -----------------------------------------------------------------------------

class Failing:
    def __init__(self, error):
        self.error = error

    async def generate_general(self, message):
        raise self.error


@pytest.mark.parametrize(("error", "message"), [
    (ProviderTimeoutError(), GENERAL_TIMEOUT),
    (ProviderUnavailableError(), GENERAL_NOT_RUNNING),
    (ModelUnavailableError(), GENERAL_NOT_INSTALLED),
    (RuntimeError("otra cosa"), GENERAL_UNAVAILABLE),
])
def test_general_failures_say_their_cause_without_the_notice(error, message):
    service = UnifiedAssistantService(NoEvidenceForConcepts(), Failing(error), web_search=StubWebSearch())
    assert asyncio.run(service.chat(VPN, session_id=str(uuid.uuid4())))["answer"] == message


def test_a_failed_call_leaves_one_line_with_codes_only(caplog):
    def timeout(payload):
        raise httpx.ReadTimeout("slow")
    provider = adapter(timeout)
    with caplog.at_level(logging.INFO, logger="ollama"), pytest.raises(ProviderTimeoutError):
        asyncio.run(provider.generate_general("pregunta privada del usuario"))
    line = caplog.text
    assert "ollama_call component=general phase=chat error=ProviderTimeoutError" in line
    assert "model=qwen3:4b-instruct-2507-q4_K_M" in line and "privada" not in line
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ollama"):
        asyncio.run(adapter(length).generate_general("pregunta privada"))
    assert "error=truncated" in caplog.text and "done_reason=length eval_count=768" in caplog.text
    assert "privada" not in caplog.text and "túnel" not in caplog.text


def test_selector_and_embedding_failures_leave_one_line(caplog, monkeypatch):
    async def refused(self, *args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx.AsyncClient, "post", refused)
    with caplog.at_level(logging.WARNING, logger="ollama"):
        with pytest.raises(Exception):
            asyncio.run(EvidenceSelector("qwen3:4b", keep_alive="10m").select("pregunta privada", [{"title": "t", "text": "x"}]))
        with pytest.raises(Exception):
            asyncio.run(OllamaEmbeddings().embed(["texto privado"]))
    assert "component=grounding phase=chat error=ConnectError" in caplog.text
    assert "component=embeddings phase=embed error=ConnectError" in caplog.text
    assert "privad" not in caplog.text


# --- Warm-up and keep_alive -------------------------------------------------------------------------------------

def test_warm_up_loads_the_model_without_a_prompt():
    """With the same num_ctx as the chat calls: Ollama reloads a model whose context differs."""
    seen = []
    assert asyncio.run(adapter(seen=seen).warm_up()) is True
    assert seen == [("/api/generate", {"model": "qwen3:4b-instruct-2507-q4_K_M", "keep_alive": "30s", "options": {"num_ctx": 2048}})]
    failing = adapter(generate=lambda payload: httpx.Response(500, json={"error": "x"}))
    assert asyncio.run(failing.warm_up()) is False  # never raises


def test_backend_startup_warms_up_only_its_own_provider_and_can_be_turned_off(monkeypatch):
    seen = []
    provider = adapter(lambda p: httpx.Response(200, json={"done": True, "message": {"content": "ok"}}), seen=seen)
    with TestClient(create_app(provider)):
        pass
    assert ("/api/generate", {"model": provider.model, "keep_alive": "30s", "options": {"num_ctx": provider.config.context_length}}) not in seen  # injected provider
    with TestClient(create_app(provider, warmup=True)) as client:
        client.get("/health")
    assert seen.count(("/api/generate", {"model": provider.model, "keep_alive": "30s", "options": {"num_ctx": provider.config.context_length}})) == 1
    seen.clear()
    monkeypatch.setenv("ITGLASSES_OLLAMA_WARMUP", "false")
    with TestClient(create_app(provider, warmup=True)) as client:
        client.get("/health")
    assert seen == []


@pytest.mark.parametrize(("value", "enabled"), [(None, True), ("", True), ("true", True), ("1", True),
                                                ("false", False), ("0", False), ("off", False), ("no", False)])
def test_warmup_setting(value, enabled, monkeypatch):
    monkeypatch.delenv("ITGLASSES_OLLAMA_WARMUP", raising=False)
    if value is not None:
        monkeypatch.setenv("ITGLASSES_OLLAMA_WARMUP", value)
    assert ollama_warmup_enabled() is enabled


def test_config_keeps_the_chat_model_loaded_two_hours_and_the_selector_uses_it():
    assert load_settings(ROOT / "config.toml").keep_alive == "2h"
    assert Settings().keep_alive == "30s"  # the code default is unchanged
    assert EvidenceSelector("m", keep_alive="10m").keep_alive == "10m"
    routes = (ROOT / "backend" / "app" / "knowledge" / "routes.py").read_text(encoding="utf-8")
    assert "EvidenceSelector(settings.model, settings.ollama_url, settings.keep_alive)" in routes


# --- Logs to logs\\backend.log ----------------------------------------------------------------------------------

def test_logs_go_to_a_rotated_file_and_configuring_twice_adds_nothing(tmp_path):
    root_logger = logging.getLogger()
    before = list(root_logger.handlers)
    try:
        path = configure_logging(tmp_path, console=False)
        configure_logging(tmp_path, console=False)
        ours = [h for h in root_logger.handlers if h not in before]
        assert len(ours) == 1 and path == tmp_path / LOG_FILE
        assert ours[0].maxBytes == MAX_BYTES == 1_000_000 and ours[0].backupCount == BACKUPS == 5
        logging.getLogger("ollama").info("ollama_warmup model=m elapsed_ms=5")
        ours[0].flush()
        assert "INFO ollama: ollama_warmup model=m elapsed_ms=5" in path.read_text(encoding="utf-8")
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        for handler in [h for h in root_logger.handlers if h not in before]:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(logging.WARNING)


def test_run_configures_the_logs_before_starting():
    run = (ROOT / "backend" / "run.py").read_text(encoding="utf-8")
    assert run.index("configure_logging(PROJECT_ROOT)") < run.index("uvicorn.run(")
