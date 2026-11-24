import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.providers.base import ProviderTimeoutError, ProviderUnavailableError
from backend.app.providers.mock import MockLLM
from backend.app.core.config import settings


@pytest.fixture
def client():
    with TestClient(create_app(MockLLM())) as client:
        yield client


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_status(client):
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == dict(status="ready", provider="mock", model="mock-v1", mock=True, rag_enabled=False, local_only=True)


def test_chat(client):
    response = client.post("/api/v1/chat", json={"message": "How do I reset a synthetic test router?"})
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "mock"
    assert "Mock response" in data["answer"]
    assert data["sources"] == []
    assert data["documentation_sufficient"] is False


@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 4001}, {"message": 123}, {"message": None}, {"message": "test", "extra": True}, []])
def test_invalid_payload(client, payload):
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_malformed_json(client):
    response = client.post("/api/v1/chat", content='{secret', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "secret" not in response.text


def test_boundary(client):
    assert client.post("/api/v1/chat", json={"message": "x" * 4000}).status_code == 200


@pytest.mark.parametrize("failure,status,code", [(ProviderUnavailableError, 503, "provider_unavailable"), (ProviderTimeoutError, 504, "provider_timeout"), (RuntimeError, 500, "internal_error")])
def test_provider_errors(failure, status, code):
    class BrokenProvider(MockLLM):
        async def generate(self, message):
            raise failure("private diagnostic")
    with TestClient(create_app(BrokenProvider()), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/chat", json={"message": "private question"})
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert "private" not in response.text


def test_unavailable():
    class OfflineProvider(MockLLM):
        async def is_available(self):
            return False
    with TestClient(create_app(OfflineProvider())) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/status").json()["status"] == "unavailable"
        assert client.post("/api/v1/chat", json={"message": "test"}).status_code == 503


def test_provider_replacement_and_normalization():
    class AlternativeProvider(MockLLM):
        name = "test-provider"
        async def generate(self, message):
            assert message == "test"
            return "Synthetic answer"
    with TestClient(create_app(AlternativeProvider())) as client:
        data = client.post("/api/v1/chat", json={"message": "  test  "}).json()
        assert data["answer"] == "Synthetic answer"
        assert data["provider"] == "test-provider"


def test_http_errors(client):
    assert client.get("/missing").status_code == 404
    assert client.get("/api/v1/chat").status_code == 405


def test_loopback_configuration():
    assert settings.host == "127.0.0.1"


# --------------------------------------------------------- unified assistant
def test_assistant_chat_routes_a_conceptual_question_to_general():
    with TestClient(create_app(MockLLM())) as client:
        response = client.post("/api/v1/assistant/chat", json={"message": "¿Qué es un switch de red?"})
        assert response.status_code == 200
        data = response.json()
        assert data["route"] == "general"
        assert data["sources"] == []
        assert data["session_id"]
        assert isinstance(data["answer"], str) and data["answer"]


def test_assistant_chat_keeps_the_session_between_messages():
    with TestClient(create_app(MockLLM())) as client:
        first = client.post("/api/v1/assistant/chat",
                            json={"message": "¿Qué es un switch de red?"}).json()
        second = client.post("/api/v1/assistant/chat",
                             json={"message": "y el firewall?", "session_id": first["session_id"]}).json()
        assert second["session_id"] == first["session_id"]
        assert second["route"] == "general"


@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 4001},
                                    {"message": 123}, {"message": "test", "extra": True}])
def test_assistant_chat_rejects_invalid_payloads(payload):
    with TestClient(create_app(MockLLM())) as client:
        response = client.post("/api/v1/assistant/chat", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_assistant_chat_method_not_allowed():
    with TestClient(create_app(MockLLM())) as client:
        assert client.get("/api/v1/assistant/chat").status_code == 405
