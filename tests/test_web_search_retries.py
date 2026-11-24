"""Public search: the ddgs engines of ITGLASSES_WEB_BACKENDS are tried in order within a total time, an official
question moves on until it gets official results, "current version" answers are cached for a few hours, and the
diagnostics (counts and categories only) reach logs\\backend.log."""
import asyncio
import logging
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from backend.app.assistant import web_search
from backend.app.assistant.service import UnifiedAssistantService
from backend.app.assistant.web_search import (DuckDuckGoSearch, WebSearchConfigError, WebSearchResult,
                                              WebSearchService, search_settings)
from backend.app.core.logging_setup import configure_logging
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM

WINDOWS = "¿Cuál es la versión más reciente de Windows 11?"
RELEASE = "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for variable in (web_search.BACKENDS_VARIABLE, web_search.TOTAL_VARIABLE, web_search.CACHE_VARIABLE):
        monkeypatch.delenv(variable, raising=False)


def fake_ddgs(monkeypatch, plan):
    """plan: backend -> list of (title, url) results, an exception class name, or ("sleep", seconds)."""
    calls = []
    DDGSException = type("DDGSException", (Exception,), {})
    errors = {"empty": DDGSException, "ratelimit": type("RatelimitException", (DDGSException,), {}),
              "timeout": type("TimeoutException", (DDGSException,), {})}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def text(self, query, max_results=5, backend="auto"):
            calls.append(backend)
            step = plan.get(backend, "empty")
            if isinstance(step, tuple) and step[0] == "sleep":
                time.sleep(step[1])
                return []
            if isinstance(step, str):
                raise errors[step]("No results found." if step == "empty" else "detail")
            return [{"title": title, "href": url, "body": "snippet"} for title, url in step]

    package = types.ModuleType("ddgs")
    package.DDGS = lambda **_kwargs: Client()
    exceptions = types.ModuleType("ddgs.exceptions")
    exceptions.DDGSException = DDGSException
    exceptions.RatelimitException = errors["ratelimit"]
    exceptions.TimeoutException = errors["timeout"]
    monkeypatch.setitem(sys.modules, "ddgs", package)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", exceptions)
    return calls


def verified(service, monkeypatch):
    monkeypatch.setattr(service, "_windows_release",
                        lambda source: WebSearchResult(source.title, source.url, "Tabla oficial: 26H2.", verified=True))
    return service


def outcomes(service):
    return [(item["backend"], item["outcome"]) for item in service.last_diagnostics["attempts"]]


# --- order, fallthrough and time budget ------------------------------------------------------------------------------

def test_the_engines_are_tried_in_the_configured_order_until_one_answers(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"google": [("VPN guide", "https://example.org/vpn")]})
    service = WebSearchService(backend=DuckDuckGoSearch())
    results = asyncio.run(service.search("Cómo configurar VPN"))
    assert calls == ["yahoo", "google"] and len(results) == 1
    assert outcomes(service) == [("yahoo", "no_results"), ("google", "ok")]
    assert service.last_error is None


def test_an_official_question_moves_on_until_it_gets_official_results(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"yahoo": [("Blog", "https://example.com/windows")],
                                    "google": "ratelimit",
                                    "yandex": [("Windows 11 release information", RELEASE)]})
    service = verified(WebSearchService(backend=DuckDuckGoSearch()), monkeypatch)
    results = asyncio.run(service.search(WINDOWS))
    assert calls == ["yahoo", "google", "yandex"]
    assert outcomes(service) == [("yahoo", "not_official"), ("google", "rate_limited"), ("yandex", "ok")]
    assert results[0].verified and "26H2" in results[0].snippet


def test_without_official_results_anywhere_the_answer_is_no_results(monkeypatch):
    fake_ddgs(monkeypatch, {name: [("Blog", "https://example.com/windows")] for name in ("yahoo", "google", "yandex", "auto")})
    service = WebSearchService(backend=DuckDuckGoSearch())
    assert asyncio.run(service.search(WINDOWS)) == []
    assert service.last_error.category == "no_results"
    assert service.last_diagnostics["provider_discard_reasons"]["not_official_domain"] == 1


def test_when_every_engine_fails_the_last_failure_is_reported(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"yahoo": "empty", "google": "ratelimit", "yandex": "empty", "auto": "timeout"})
    service = WebSearchService(backend=DuckDuckGoSearch())
    assert asyncio.run(service.search("Cómo configurar VPN")) == []
    assert calls == ["yahoo", "google", "yandex", "auto"]
    assert service.last_error.category == "timeout"


def test_the_total_time_is_bounded(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"yahoo": ("sleep", 1.5), "google": [("VPN guide", "https://example.org/vpn")]})
    service = WebSearchService(backend=DuckDuckGoSearch(total_seconds=1.2))
    assert asyncio.run(service.search("Cómo configurar VPN")) == []
    assert calls == ["yahoo"]
    assert outcomes(service)[0] == ("yahoo", "timeout") and outcomes(service)[-1] == ("google", "skipped_budget")


def test_a_custom_order_is_used(monkeypatch):
    monkeypatch.setenv(web_search.BACKENDS_VARIABLE, " Bing, auto ")
    calls = fake_ddgs(monkeypatch, {"auto": [("VPN guide", "https://example.org/vpn")]})
    asyncio.run(WebSearchService(backend=DuckDuckGoSearch()).search("Cómo configurar VPN"))
    assert calls == ["bing", "auto"]


# --- configuration -------------------------------------------------------------------------------------------------

def test_default_settings():
    assert search_settings() == (("yahoo", "google", "yandex", "auto"), 25, 6)


@pytest.mark.parametrize(("variable", "value"), [
    (web_search.BACKENDS_VARIABLE, "yahoo,altavista"), (web_search.BACKENDS_VARIABLE, " , "),
    (web_search.TOTAL_VARIABLE, "2"), (web_search.TOTAL_VARIABLE, "mucho"),
    (web_search.CACHE_VARIABLE, "-1"), (web_search.CACHE_VARIABLE, "100"),
])
def test_invalid_settings_stop_the_backend(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(WebSearchConfigError):
        search_settings()
    with pytest.raises(WebSearchConfigError):
        with TestClient(create_app(MockLLM())):
            pass


# --- cache of current facts ----------------------------------------------------------------------------------------

def test_a_verified_current_version_is_cached_for_the_configured_hours(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"yahoo": [("Windows 11 release information", RELEASE)]})
    service = verified(WebSearchService(backend=DuckDuckGoSearch()), monkeypatch)
    now = [1000.0]
    service._clock = lambda: now[0]
    first = asyncio.run(service.search(WINDOWS))
    again = asyncio.run(service.search(WINDOWS))
    assert calls == ["yahoo"] and again == first and service.last_diagnostics["cache"] == "hit"
    assert again[0].retrieved_at == first[0].retrieved_at          # the time it was really consulted
    now[0] += 6 * 3600 + 1
    asyncio.run(service.search(WINDOWS))
    assert calls == ["yahoo", "yahoo"] and service.last_diagnostics["cache"] == "miss"


def test_what_is_not_cached(monkeypatch):
    calls = fake_ddgs(monkeypatch, {"yahoo": [("Windows 11 release information", RELEASE)]})
    service = WebSearchService(backend=DuckDuckGoSearch())
    monkeypatch.setattr(service, "_windows_release", lambda source: WebSearchResult(
        source.title, source.url, "No pude verificar la tabla actual de versiones.", verified=False))
    asyncio.run(service.search(WINDOWS))
    asyncio.run(service.search(WINDOWS))                             # an unverified table is asked again
    asyncio.run(service.search("Cómo configurar VPN"))
    asyncio.run(service.search("Cómo configurar VPN"))               # not a current fact
    assert calls == ["yahoo"] * 4


def test_the_cache_can_be_turned_off(monkeypatch):
    monkeypatch.setenv(web_search.CACHE_VARIABLE, "0")
    calls = fake_ddgs(monkeypatch, {"yahoo": [("Windows 11 release information", RELEASE)]})
    service = verified(WebSearchService(backend=DuckDuckGoSearch()), monkeypatch)
    asyncio.run(service.search(WINDOWS))
    asyncio.run(service.search(WINDOWS))
    assert calls == ["yahoo", "yahoo"] and service.last_diagnostics["cache"] == "off"


# --- logs\backend.log ----------------------------------------------------------------------------------------------

def test_the_diagnostics_reach_the_backend_log_without_the_query(monkeypatch, tmp_path):
    fake_ddgs(monkeypatch, {"google": [("Windows 11 release information", RELEASE)]})
    service = verified(WebSearchService(backend=DuckDuckGoSearch()), monkeypatch)
    root = logging.getLogger()
    level = root.level
    path = configure_logging(tmp_path, console=False)
    try:
        asyncio.run(service.search(WINDOWS))
        UnifiedAssistantService._log_public_search_diagnostics(service, WINDOWS, response_mode="evidence_fallback")
        logging.getLogger("primp").info("response: https://search.example/?q=Windows+11+secret")
        for handler in root.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_itglasses_handler", False):
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)
    assert "Public search diagnostics" in text
    assert '"backend": "yahoo", "ms": ' in text and '"outcome": "ok"' in text and '"cache": "miss"' in text
    assert "versión más reciente" not in text and "secret" not in text
