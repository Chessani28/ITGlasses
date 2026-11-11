"""Public web search provider for the assistant.

The assistant keeps local, authorized company knowledge in the knowledge layer
and uses the *general* route for ordinary concepts. A question that asks for a
current, public fact ("what is the latest version of X?") is a candidate for the
public web: it never touches internal documents, credentials or internal URLs.
"""

from __future__ import annotations

import html
from datetime import date
import asyncio
import logging
from urllib.parse import urlsplit
import os
import re
import requests
from dataclasses import dataclass, field
from typing import Protocol


_SAFE_ERROR_CATEGORIES = frozenset({
    "provider_unavailable", "timeout", "rate_limited", "no_results", "irrelevant",
})


def search_query_category(query: str) -> str:
    """Return a low-sensitivity topic label suitable for operational logs."""
    for category, pattern in (
        ("vpn", r"\bvpn\b"),
        ("windows", r"\bwindows\b"),
        ("dns", r"\bdns\b"),
        ("dhcp", r"\bdhcp\b"),
        ("network", r"\b(?:red|network|wifi|wi-fi|router)\b"),
    ):
        if re.search(pattern, query or "", re.IGNORECASE):
            return category
    return "other"


def _safe_error_category(error) -> str | None:
    category = getattr(error, "category", None)
    return category if isinstance(category, str) and category in _SAFE_ERROR_CATEGORIES else None


class WebSearchError(Exception):
    """A safe, classifiable public search failure."""

    MESSAGES = {
        "provider_unavailable": "No pude consultar fuentes públicas en este momento.",
        "timeout": "La búsqueda pública tardó demasiado y no se completó.",
        "rate_limited": "El buscador público limitó temporalmente las consultas.",
        "no_results": "El buscador público no encontró resultados para esa consulta.",
        "irrelevant": "Los resultados públicos no contienen información suficiente sobre ese tema.",
    }

    def __init__(self, message: str, category: str = "provider_unavailable"):
        super().__init__(message)
        self.category = category

    @property
    def public_message(self):
        return self.MESSAGES.get(self.category, self.MESSAGES["provider_unavailable"])


@dataclass(frozen=True)
class WebSearchResult:
    """One retrieved public source."""

    title: str
    url: str
    snippet: str
    retrieved_at: str = field(
        default_factory=lambda: __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).astimezone().isoformat()
    )
    verified: bool = False


class SearchBackend(Protocol):
    """A searchable provider of public sources."""

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        """Return at most ``max_results`` public sources for ``query``."""


class DuckDuckGoSearch:
    """Free public provider (DuckDuckGo JSON endpoint). No API key required.

    This provider is used by default. It is not a paid API and it never receives
    internal documents, internal URLs, credentials or sensitive conversation
    context.
    """

    def __init__(self, endpoint=None, timeout_seconds: float = 15.0, user_agent=None):
        self.endpoint = endpoint or os.environ.get(
            "ITGLASSES_WEB_DUCKDUCKGO_ENDPOINT",
            "https://api.duckduckgo.com/",
        )
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or os.environ.get(
            "ITGLASSES_WEB_USER_AGENT", "ITGlasses-web-assistant/1.0"
        )
        self.last_diagnostics = {}

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        self.last_diagnostics = {
            "provider_result_count": 0,
            "basic_validated_count": 0,
            "provider_discard_reasons": {},
        }
        if not query or not query.strip():
            raise WebSearchError("empty query")

        # Use ddgs for real web search (DuckDuckGo HTML search backend).
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
        except ImportError as exc:
            raise WebSearchError(
                "search dependency is unavailable", "provider_unavailable"
            ) from exc

        try:
            def lookup():
                with DDGS(timeout=self.timeout_seconds) as ddgs:
                    return list(ddgs.text(query.strip(), max_results=max_results))
            raw = await asyncio.to_thread(lookup)
        except TimeoutException as exc:
            raise WebSearchError("public search timed out", "timeout") from exc
        except RatelimitException as exc:
            raise WebSearchError("public search was rate limited", "rate_limited") from exc
        except DDGSException as exc:
            if "no results found" in str(exc).casefold():
                raise WebSearchError("provider returned no results", "no_results") from exc
            raise WebSearchError("public search provider failed", "provider_unavailable") from exc
        except (TimeoutError, asyncio.TimeoutError, requests.Timeout) as exc:
            raise WebSearchError("public search timed out", "timeout") from exc
        except Exception as exc:
            raise WebSearchError("public search provider failed", "provider_unavailable") from exc

        results: list[WebSearchResult] = []
        discard_reasons = {}
        for item in raw:
            self.last_diagnostics["provider_result_count"] += 1
            if not isinstance(item, dict):
                discard_reasons["invalid_result"] = discard_reasons.get("invalid_result", 0) + 1
                continue
            title_value = item.get("title") or ""
            link_value = item.get("href") or item.get("link") or ""
            snippet_value = item.get("body") or item.get("snippet") or ""
            if not all(isinstance(value, str) for value in (title_value, link_value, snippet_value)):
                discard_reasons["invalid_result"] = discard_reasons.get("invalid_result", 0) + 1
                continue
            title = title_value.strip()
            link = link_value.strip()
            snippet = snippet_value.strip()
            if not title:
                discard_reasons["missing_title"] = discard_reasons.get("missing_title", 0) + 1
                continue
            try:
                parsed = urlsplit(link)
                valid_url = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and not parsed.username
                )
            except ValueError:
                valid_url = False
                parsed = None
            if not valid_url:
                reason = "url_credentials" if parsed and parsed.username else "invalid_url"
                discard_reasons[reason] = discard_reasons.get(reason, 0) + 1
                continue
            title = re.sub(r"[\[\]\r\n]", " ", title)
            link = link.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            results.append(WebSearchResult(title=title, url=link, snippet=snippet))

        results = results[: max_results or 0]
        self.last_diagnostics["basic_validated_count"] = len(results)
        self.last_diagnostics["provider_discard_reasons"] = discard_reasons
        return results


class OfflineSearch:
    """No-op provider for offline mode. Returns no external sources."""

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        return []


class WebSearchService:
    """Public web search used only by the general route of the assistant.

    The service never receives internal documents, internal URLs, credentials or
    sensitive conversation context: only the user's message text.
    """

    def __init__(self, backend=None, enabled: bool = True):
        self.enabled = enabled
        if backend is None:
            backend = self._default_backend()
        self.backend = backend
        self.last_error: Exception | None = None

    @staticmethod
    def _default_backend():
        provider = (os.environ.get("ITGLASSES_WEB_PROVIDER") or "duckduckgo").strip().lower()
        if provider in {"offline", "none", "mock"}:
            return OfflineSearch()
        if provider in {"duckduckgo", "ddg", "free"}:
            return DuckDuckGoSearch()
        raise ValueError("Unsupported ITGLASSES_WEB_PROVIDER configuration")

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        backend_name = (
            "duckduckgo" if isinstance(self.backend, DuckDuckGoSearch)
            else "offline" if isinstance(self.backend, OfflineSearch)
            else "custom"
        )
        self.last_diagnostics = {
            "query_category": search_query_category(query),
            "provider": backend_name,
            "provider_result_count": 0,
            "basic_validated_count": 0,
            "provider_discard_reasons": {},
            "error_category": None,
        }
        if not self.enabled:
            self.last_error = WebSearchError(
                "public search is disabled", "provider_unavailable"
            )
            self.last_diagnostics["error_category"] = "provider_unavailable"
            return []
        self.last_error = None
        windows = re.search(r"windows\s+(\d+)", query, re.I)
        windows_release_query = bool(
            windows
            and re.search(r"versi[oó]n|version|latest|current|newest|reciente|actual|[uú]ltim", query, re.I)
        )
        official_microsoft = windows_release_query or bool(
            re.search(r"windows|microsoft", query, re.I)
            and re.search(r"oficial|official", query, re.I)
        )
        if official_microsoft:
            # Search terms, not a canned answer: release values are fetched below.
            if windows:
                query = f"Windows {windows.group(1)} latest version release information"
            query += " site:learn.microsoft.com"
        try:
            results = await self.backend.search(query, max_results)
            backend_diagnostics = getattr(self.backend, "last_diagnostics", {})
            self.last_diagnostics["provider_result_count"] = backend_diagnostics.get(
                "provider_result_count", len(results)
            )
            self.last_diagnostics["basic_validated_count"] = backend_diagnostics.get(
                "basic_validated_count", len(results)
            )
            self.last_diagnostics["provider_discard_reasons"] = dict(
                backend_diagnostics.get("provider_discard_reasons", {})
            )
            if official_microsoft:
                original_count = len(results)
                results = [r for r in results if (urlsplit(r.url).hostname or "").endswith(".microsoft.com")]
                excluded = original_count - len(results)
                if excluded:
                    reasons = self.last_diagnostics["provider_discard_reasons"]
                    reasons["not_official_domain"] = reasons.get("not_official_domain", 0) + excluded
            if windows_release_query and windows:
                for index, result in enumerate(results):
                    parsed = urlsplit(result.url)
                    if (parsed.hostname == "learn.microsoft.com"
                            and parsed.path.rstrip("/").endswith(f"/windows{windows.group(1)}-release-information")):
                        # Read the live official table rather than trusting a stale snippet.
                        enriched = await asyncio.to_thread(self._windows_release, result)
                        results[index] = enriched
                        results.insert(0, results.pop(index))
                        break
        except WebSearchError as exc:
            self.last_error = exc
            backend_diagnostics = getattr(self.backend, "last_diagnostics", {})
            self.last_diagnostics["provider_result_count"] = backend_diagnostics.get(
                "provider_result_count", 0
            )
            self.last_diagnostics["basic_validated_count"] = backend_diagnostics.get(
                "basic_validated_count", 0
            )
            self.last_diagnostics["provider_discard_reasons"] = dict(
                backend_diagnostics.get("provider_discard_reasons", {})
            )
            self.last_diagnostics["error_category"] = _safe_error_category(exc) or "provider_unavailable"
            return []
        except Exception:
            self.last_diagnostics["error_category"] = "provider_unavailable"
            raise
        if not results:
            category = (
                "provider_unavailable" if isinstance(self.backend, OfflineSearch)
                else "no_results"
            )
            self.last_error = WebSearchError("provider returned no public sources", category)
            self.last_diagnostics["error_category"] = category
        else:
            self.last_diagnostics["error_category"] = _safe_error_category(self.last_error)
        return results

    @staticmethod
    def _windows_release(result):
        try:
            with requests.get(result.url, timeout=15, allow_redirects=False, stream=True) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("unexpected redirect")
                data = bytearray()
                for part in response.iter_content(65536):
                    data.extend(part)
                    if len(data) > 3_000_000:
                        raise ValueError("page too large")
            rows = []
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", data.decode("utf-8"), re.S | re.I):
                cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                         for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)]
                if (len(cells) == 8 and re.fullmatch(r"\d{2}H[12]", cells[0])
                        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[2])
                        and cells[2] <= date.today().isoformat()
                        and ("General Availability" in cells[1] or "disponibilidad general" in cells[1].lower())):
                    rows.append(cells)
            if not rows:
                raise ValueError("official version table not recognized")
            latest = max(rows, key=lambda cells: cells[2])
            excerpt = (f"La versión con fecha de disponibilidad general más reciente en la tabla oficial es {latest[0]} "
                       f"({latest[2]}). Compilación publicada: {latest[7]}; fecha de revisión: {latest[6]}. "
                       "La disponibilidad para cada equipo depende del canal y la compatibilidad.")
            logging.getLogger("uvicorn.error").info("Official release page fetched HTTP 200; version=%s", latest[0])
            return WebSearchResult(result.title, result.url, excerpt, verified=True)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            logging.getLogger("uvicorn.error").warning("Official release verification failed: %s", exc)
            return WebSearchResult(result.title, result.url,
                                   "No pude verificar la tabla actual de versiones. Consulta esta fuente oficial; no confirmo una versión más reciente.")

    async def fetch_url(self, url: str, timeout: float = 12.0) -> dict:
        """Best-effort fetch of a public document for citation enrichment."""
        try:
            response = requests.get(
                url, timeout=timeout, headers={"User-Agent": "ITGlasses-web-assistant/1.0"}
            )
            response.raise_for_status()
            return {
                "title": response.text.split("<title>")[-1].split("</title>")[0].strip() if "<title>" in response.text else url,
                "url": url,
                "status": response.status_code,
            }
        except Exception as exc:  # noqa: BLE001
            return {"url": url, "status": "unavailable", "error": str(exc)}



_WEB_NEED_QUERY = re.compile(
    r"busca.*(?:internet|web)|b[uú]squeda.*(?:internet|web)|search.*(?:internet|web)|versi[oó]n\s+(m[áa]s|more\s+recent)|reciente|últim|última|last\s+version|latest|what\s+is\s+the\s+current",
    re.IGNORECASE,
)

# Labels shown in the chat UI, on the same status map used for knowledge and social.
WEB_ROUTE_LABEL = "Búsqueda pública"
