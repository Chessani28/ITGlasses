"""Monitoreo: tiempos y códigos por etapa (voz a texto, asistente, texto a voz), sin contenido de las conversaciones;
resumen de la última hora solo para administradores."""
from backend.app.core import metrics


def setup_function():
    metrics.reset()


def test_summary_per_stage_with_median_p90_and_errors():
    for ms in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
        metrics.record("chat", ms, detail="knowledge", now=1000)
    metrics.record("chat", 50, outcome="ProviderTimeoutError", now=1000)
    summary = metrics.summary(now=1500)
    chat = summary["stages"]["chat"]
    assert chat["count"] == 11 and chat["errors"] == 1
    assert chat["median_ms"] == 550 and chat["p90_ms"] == 900
    assert summary["recent_errors"][-1] == {"stage": "chat", "outcome": "ProviderTimeoutError", "detail": None,
                                            "seconds_ago": 500}


def test_speech_engines_and_whisper_fallback_reasons_are_counted():
    metrics.record("stt", 400, detail="whisper-small", now=10)
    metrics.record("stt", 30, outcome="voice_resource_limit", detail="whisper-small", now=10)
    metrics.record("stt", 300, detail="vosk", now=10)
    summary = metrics.summary(now=20)
    assert summary["stt_engines"] == {"whisper-small": 1, "vosk": 1}
    assert summary["whisper_failures"] == {"voice_resource_limit": 1}


def test_only_the_last_hour_counts():
    metrics.record("tts", 100, now=0)
    metrics.record("tts", 200, now=4000)
    assert metrics.summary(now=4100)["stages"]["tts"]["count"] == 1


def test_no_content_is_ever_stored():
    metrics.record("chat", 10, outcome="ok", detail="¿Cuál es mi contraseña? hola123", now=1)
    stored = repr(metrics._events)
    assert "contraseña" not in stored and "hola123" not in stored


def test_the_buffer_is_bounded():
    for i in range(metrics.MAX_EVENTS + 50):
        metrics.record("chat", 1, now=i)
    assert len(metrics._events) == metrics.MAX_EVENTS


def test_the_endpoint_is_admin_only(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    with TestClient(create_app(MockLLM()), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/admin/metrics").status_code in (401, 403)


def test_the_chat_route_records_its_time():
    import inspect
    from backend.app.api import routes
    from backend.app.voice import routes as voice_routes
    assert "metrics.record(\"chat\"" in inspect.getsource(routes.assistant_chat)
    assert "metrics.record(\"stt\"" in inspect.getsource(voice_routes.transcribe)
    assert "metrics.record(\"tts\"" in inspect.getsource(voice_routes.synthesize)
