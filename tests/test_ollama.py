import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, load_settings
from backend.app.main import create_app
from backend.app.providers.ollama import OllamaProvider
from backend.app.providers.base import (
    ModelUnavailableError, ProviderUnavailableError,
    ProviderTimeoutError, ProviderResponseError,
)

LOCAL = {"details": {"format": "gguf"}}


def provider(handler, **kwargs):
    return OllamaProvider(Settings(**kwargs), httpx.MockTransport(handler))


def test_success_and_request_limits():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == "127.0.0.1"
        payload = json.loads(request.content)
        if request.url.path == "/api/show":
            assert payload == {"model": "qwen3:4b-instruct-2507-q4_K_M"}
            return httpx.Response(200, json=LOCAL)
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 2048
        assert payload["options"]["num_predict"] == 256
        assert payload["keep_alive"] == "30s"
        assert payload["messages"][-1] == {"role": "user", "content": "Synthetic question"}
        return httpx.Response(200, json={"done": True, "message": {"content": " Answer ", "thinking": "Internal synthetic reasoning"}})
    adapter = provider(handler)
    with TestClient(create_app(adapter)) as client:
        assert client.get("/api/v1/status").json()["mock"] is False
        response = client.post("/api/v1/chat", json={"message": "Synthetic question"})
        assert response.status_code == 200
        assert response.json()["answer"] == "Answer"
        assert response.json()["sources"] == []
        assert response.json()["documentation_sufficient"] is False
    assert "/api/chat" in paths
    assert "/api/pull" not in paths


@pytest.mark.parametrize("http_status,error,api_status,code", [
    (404, ModelUnavailableError, 503, "model_unavailable"),
    (500, ProviderUnavailableError, 503, "provider_unavailable"),
    (400, ProviderResponseError, 502, "provider_response_error"),
    (302, ProviderResponseError, 502, "provider_response_error"),
])
def test_http_failures(http_status, error, api_status, code):
    adapter = provider(lambda request: httpx.Response(http_status, json={"error": "private diagnostic"}))
    assert asyncio.run(adapter.is_available()) is False
    with pytest.raises(error):
        asyncio.run(adapter.generate("test"))
    with TestClient(create_app(adapter)) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/api/v1/chat", json={"message": "secret"})
        assert response.status_code == api_status
        assert response.json()["error"]["code"] == code
        assert "private" not in response.text and "secret" not in response.text


@pytest.mark.parametrize("exception,error,status", [
    (httpx.ConnectError, ProviderUnavailableError, 503),
    (httpx.ReadTimeout, ProviderTimeoutError, 504),
])
def test_network_errors(exception, error, status):
    def handler(request):
        raise exception("private diagnostic", request=request)
    adapter = provider(handler)
    assert asyncio.run(adapter.is_available()) is False
    with pytest.raises(error):
        asyncio.run(adapter.generate("test"))
    with TestClient(create_app(adapter)) as client:
        response = client.post("/api/v1/chat", json={"message": "test"})
        assert response.status_code == status
        assert "Mock response" not in response.text


@pytest.mark.parametrize("payload", [{"done": True, "done_reason": "length", "message": {"content": "truncated answer"}}, {}, [], {"error": "private"}, {"done": False, "message": {"content": "partial"}}, {"done": True, "message": {"content": ""}}, {"done": True, "message": {"content": 42}}])
def test_invalid_generation(payload):
    def handler(request):
        return httpx.Response(200, json=LOCAL if request.url.path == "/api/show" else payload)
    with pytest.raises(ProviderResponseError):
        asyncio.run(provider(handler).generate("test"))


def test_invalid_json():
    adapter = provider(lambda request: httpx.Response(200, content=b"not json"))
    assert asyncio.run(adapter.is_available()) is False


def test_cloud_metadata_rejected():
    adapter = provider(lambda request: httpx.Response(200, json={**LOCAL, "remote_host": "https://ollama.com"}))
    with pytest.raises(ModelUnavailableError):
        asyncio.run(adapter.generate("test"))


@pytest.mark.parametrize("kwargs", [
    {"thinking": "false"}, {"provider": "unknown"}, {"ollama_url": "https://example.com:443"},
    {"ollama_url": "http://127.0.0.1:11434/path"},
    {"ollama_url": "http://user:pass@127.0.0.1:11434"},
    {"model": "example:cloud"}, {"host": "0.0.0.0"},
    {"context_length": 8192}, {"timeout_seconds": 0}, {"max_output_tokens": 0},
])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_local_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("ITGLASSES_PROVIDER", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('provider = "ollama"\nmodel = "qwen3:4b"\n')
    assert load_settings(path).provider == "ollama"
    monkeypatch.setenv("ITGLASSES_PROVIDER", "mock")
    assert load_settings(path).provider == "mock"


def test_single_generation_concurrency():
    async def scenario():
        active = 0
        maximum = 0
        async def handler(request):
            nonlocal active, maximum
            if request.url.path == "/api/show":
                return httpx.Response(200, json=LOCAL)
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json={"done": True, "message": {"content": "ok"}})
        adapter = provider(handler)
        assert await asyncio.gather(adapter.generate("a"), adapter.generate("b")) == ["ok", "ok"]
        assert maximum == 1
    asyncio.run(scenario())


def test_total_deadline():
    async def handler(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json=LOCAL)
    with pytest.raises(ProviderTimeoutError):
        asyncio.run(provider(handler, timeout_seconds=1).generate("test"))


def test_output_limit_error_is_explicit():
    def handler(request):
        return httpx.Response(200, json=LOCAL if request.url.path == "/api/show" else {
            "done": True, "done_reason": "length", "message": {"content": "partial"}})
    with TestClient(create_app(provider(handler))) as client:
        response = client.post("/api/v1/chat", json={"message": "test"})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_output_limit"
        assert "partial" not in response.text
