"""Isolated tests for public search source selection and verification."""

import asyncio
from pathlib import Path
import sys
import types

import pytest

from backend.app.assistant.web_search import (
    DuckDuckGoSearch, WebSearchError, WebSearchResult, WebSearchService,
)


def _mock_ddgs(monkeypatch, error):
    class DDGSException(Exception):
        pass

    class RatelimitException(DDGSException):
        pass

    class TimeoutException(DDGSException):
        pass

    classes = {
        "timeout": TimeoutException,
        "rate_limit": RatelimitException,
        "empty": DDGSException,
        "network": OSError,
        "ddgs_failure": DDGSException,
    }

    class RaisingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def text(self, *_args, **_kwargs):
            message = "No results found." if error == "empty" else f"internal {error} detail"
            raise classes[error](message)

    package = types.ModuleType("ddgs")
    package.DDGS = lambda **_kwargs: RaisingDDGS()
    exceptions = types.ModuleType("ddgs.exceptions")
    exceptions.DDGSException = DDGSException
    exceptions.RatelimitException = RatelimitException
    exceptions.TimeoutException = TimeoutException
    monkeypatch.setitem(sys.modules, "ddgs", package)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", exceptions)

class StubSearchBackend:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    async def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.results[:max_results]


def test_current_windows_release_search_prefers_microsoft_and_verifies_table(monkeypatch):
    backend = StubSearchBackend([
        WebSearchResult(
            "Windows 11 release information",
            "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information",
            "Potentially stale search preview.",
        ),
        WebSearchResult("Other result", "https://example.com/windows", "Untrusted result."),
    ])
    service = WebSearchService(backend=backend)
    monkeypatch.setattr(
        service,
        "_windows_release",
        lambda source: WebSearchResult(
            source.title, source.url, "Official release table: 26H1.", verified=True
        ),
    )

    results = asyncio.run(service.search("¿Cuál es la versión más reciente de Windows 11?"))

    assert backend.calls == [
        ("Windows 11 latest version release information site:learn.microsoft.com", 5)
    ]
    assert len(results) == 1
    assert results[0].verified is True
    assert "26H1" in results[0].snippet


def test_failed_public_search_returns_no_results_and_records_error():
    backend = StubSearchBackend(error=WebSearchError("provider unavailable"))
    service = WebSearchService(backend=backend)

    results = asyncio.run(service.search("¿Cuál es la versión más reciente de Windows 11?"))

    assert results == []
    assert service.last_error is not None
    assert service.last_error.category == "provider_unavailable"
    assert service.last_error.public_message == "No pude consultar fuentes públicas en este momento."


def test_empty_provider_results_are_classified():
    service = WebSearchService(backend=StubSearchBackend(results=[]))

    results = asyncio.run(service.search("Cómo configurar VPN"))

    assert results == []
    assert service.last_error.category == "no_results"
    assert service.last_diagnostics["provider_result_count"] == 0
    assert service.last_diagnostics["error_category"] == "no_results"


def test_search_dependencies_are_declared_and_locked():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "requirements.txt").read_text(encoding="utf-8")
    lock = (root / "requirements-lock.txt").read_text(encoding="utf-8")

    assert "ddgs>=9.16.1,<10" in runtime
    assert "requests>=2.34.2,<3" in runtime
    for requirement in (
        "ddgs==9.16.1",
        "requests==2.34.2",
        "primp==2.0.1",
        "charset-normalizer==3.5.2",
        "urllib3==2.8.0",
    ):
        assert requirement in lock


def test_ddgs_search_records_provider_and_validation_counts(monkeypatch):
    class ResultDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def text(self, *_args, **_kwargs):
            return [
                {"title": "VPN guide", "href": "https://example.org/vpn", "body": "VPN setup guidance."},
                {"title": "Invalid link", "href": "javascript:alert(1)", "body": "not a source"},
                {"title": "", "href": "https://example.org/empty", "body": "no title"},
            ]

    package = types.ModuleType("ddgs")
    package.DDGS = lambda **_kwargs: ResultDDGS()
    exceptions = types.ModuleType("ddgs.exceptions")
    exceptions.DDGSException = type("DDGSException", (Exception,), {})
    exceptions.RatelimitException = type("RatelimitException", (exceptions.DDGSException,), {})
    exceptions.TimeoutException = type("TimeoutException", (exceptions.DDGSException,), {})
    monkeypatch.setitem(sys.modules, "ddgs", package)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", exceptions)
    backend = DuckDuckGoSearch()
    service = WebSearchService(backend=backend)

    results = asyncio.run(service.search("Cómo configurar VPN"))

    assert len(results) == 1
    assert service.last_diagnostics["provider"] == "duckduckgo"
    assert service.last_diagnostics["provider_result_count"] == 3
    assert service.last_diagnostics["basic_validated_count"] == 1
    assert service.last_diagnostics["provider_discard_reasons"] == {
        "invalid_url": 1,
        "missing_title": 1,
    }


@pytest.mark.parametrize(("provider", "expected"), [
    ("offline", "offline"),
    ("none", "offline"),
    ("duckduckgo", "duckduckgo"),
])
def test_default_provider_configuration_accepts_supported_values(monkeypatch, provider, expected):
    monkeypatch.setenv("ITGLASSES_WEB_PROVIDER", provider)

    assert type(WebSearchService().backend).__name__.casefold().startswith(expected)


def test_invalid_provider_configuration_fails_explicitly(monkeypatch):
    monkeypatch.setenv("ITGLASSES_WEB_PROVIDER", "unknown-provider")

    with pytest.raises(ValueError, match="Unsupported ITGLASSES_WEB_PROVIDER configuration"):
        WebSearchService()


@pytest.mark.parametrize(("error", "category"), [
    ("timeout", "timeout"),
    ("rate_limit", "rate_limited"),
    ("empty", "no_results"),
    ("network", "provider_unavailable"),
    ("ddgs_failure", "provider_unavailable"),
])
def test_ddgs_provider_errors_are_classified_without_leaking_details(
    monkeypatch, error, category
):
    _mock_ddgs(monkeypatch, error)
    service = WebSearchService(backend=DuckDuckGoSearch(timeout_seconds=1))

    results = asyncio.run(service.search("Cómo configurar VPN"))

    assert results == []
    assert service.last_error.category == category
    assert "internal" not in service.last_error.public_message
    assert service.last_diagnostics["error_category"] == category


def test_ddgs_network_exception_is_classified_as_provider_unavailable(monkeypatch):
    _mock_ddgs(monkeypatch, "network")
    service = WebSearchService(backend=DuckDuckGoSearch(timeout_seconds=1))

    results = asyncio.run(service.search("Cómo configurar VPN"))

    assert results == []
    assert service.last_error.category == "provider_unavailable"
    assert "private" not in service.last_error.public_message
