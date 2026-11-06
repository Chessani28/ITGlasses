"""Public web search provider for the assistant.

The assistant keeps local, authorized company knowledge in the knowledge layer
and uses the *general* route for ordinary concepts. A question that asks for a
current, public fact ("what is the latest version of X?") is a candidate for the
public web: it never touches internal documents, credentials or internal URLs.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
import asyncio
import logging
from urllib.parse import urlsplit
import os
import re
import httpx
from dataclasses import dataclass, field
from typing import Protocol


class WebSearchError(Exception):
    """Raised when a public search provider cannot return results."""


@dataclass(frozen=True)
class WebSearchResult:
    """One retrieved public source."""

    title: str
    url: str
    snippet: str
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SearchBackend(Protocol):
    """A searchable provider of public sources."""

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        """Return at most ``max_results`` public sources for ``query``."""


class DuckDuckGoSearch:
    """Optional DuckDuckGo search adapter via ddgs. No API key required.

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

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        if not query or not query.strip():
            raise WebSearchError("empty query")

        # Use ddgs for real web search (DuckDuckGo HTML search backend).
        try:
            from ddgs import DDGS
        except ImportError:
            raise WebSearchError("ddgs package not installed")

        try:
            def lookup():
                with DDGS(timeout=self.timeout_seconds) as ddgs:
                    return list(ddgs.text(query.strip(), max_results=max_results, backend="duckduckgo"))
            raw = await asyncio.to_thread(lookup)
        except Exception as exc:
            raise WebSearchError(f"search request failed: {exc}") from exc

        results: list[WebSearchResult] = []
        for item in raw:
            title = (item.get("title") or "").strip()
            link = (item.get("href") or item.get("link") or "").strip()
            snippet = (item.get("body") or item.get("snippet") or "").strip()
            parsed = urlsplit(link)
            if title and parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
                title = re.sub(r"[\[\]\r\n]", " ", title)
                link = link.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
                results.append(WebSearchResult(title=title, url=link, snippet=snippet))

        return results[: max_results or 0]


class OfflineSearch:
    """No-op provider for offline mode. Returns no external sources."""

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        return []


class WebSearchService:
    """Public web search used only by the general route of the assistant.

    The service never receives internal documents, internal URLs, credentials or
    sensitive conversation context: only the user's message text.
    """

    WINDOWS_RELEASE_URL = "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information"
    MAX_PAGE_BYTES = 3_000_000

    def __init__(self, backend=None, enabled: bool = True, transport=None):
        self.enabled = enabled
        self.transport = transport
        if backend is None:
            backend = self._default_backend()
        self.backend = backend
        self.last_error: Exception | None = None

    @staticmethod
    def _default_backend():
        provider = (os.environ.get("ITGLASSES_WEB_PROVIDER") or "duckduckgo").lower()
        if provider in {"offline", "none", "mock"}:
            return OfflineSearch()
        if provider in {"duckduckgo", "ddg", "free"}:
            try:
                return DuckDuckGoSearch()
            except Exception:  # noqa: BLE001 - the provider stays disabled below
                pass
        return OfflineSearch()

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        self.last_error = None
        if not self.enabled or isinstance(self.backend, OfflineSearch):
            return []
        max_results = max(1, min(20, max_results))
        try:
            async with asyncio.timeout(20):
                if (re.search(r"\bwindows\s+11\b", query, re.I)
                        and re.search(r"versi[oó]n|compilaci[oó]n|build|release", query, re.I)
                        and re.search(r"reciente|[uú]ltim|latest|current|actual", query, re.I)):
                    # Fixed official URL: no user query, history, or internal text is sent.
                    page = await self._read_official(self.WINDOWS_RELEASE_URL)
                    return [self._windows_release(page)]
                official = bool(re.search(r"microsoft|windows", query, re.I)
                                and re.search(r"oficial|official", query, re.I))
                results = await self.backend.search(query + (" site:learn.microsoft.com" if official else ""), max_results)
                if official:
                    results = [r for r in results if (urlsplit(r.url).hostname or "").endswith(".microsoft.com")]
                if not results:
                    raise WebSearchError("provider returned no public sources")
                return results[:max_results]
        except (WebSearchError, TimeoutError) as exc:
            self.last_error = exc
            # Do not log query text or provider exceptions that may echo it.
            logging.getLogger(__name__).warning("Public retrieval failed error_type=%s", type(exc).__name__)
            return []

    async def _read_official(self, url):
        # Exact allowlist prevents arbitrary URL fetches, credentials, ports, queries,
        # private IPs and redirect-based SSRF. Environment proxies are not used.
        if url != self.WINDOWS_RELEASE_URL:
            raise WebSearchError("destination not allowed")
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    transport=self.transport, timeout=httpx.Timeout(15, connect=3)) as client:
                async with client.stream("GET", url, headers={"User-Agent":"ITGlasses/1.0", "Accept":"text/html"}) as response:
                    if response.status_code != 200:
                        raise WebSearchError("official source unavailable")
                    data = bytearray()
                    async for part in response.aiter_bytes(65536):
                        if len(data) + len(part) > self.MAX_PAGE_BYTES:
                            raise WebSearchError("official page too large")
                        data.extend(part)
            return data.decode("utf-8")
        except (httpx.HTTPError, UnicodeError) as exc:
            raise WebSearchError("official source request failed") from exc

    @classmethod
    def _windows_release(cls, page):
        rows = []
        today = datetime.now(timezone.utc).date().isoformat()
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.S | re.I):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                     for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)]
            if (len(cells) == 8 and re.fullmatch(r"\d{2}H[12]", cells[0])
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[2])
                    and cells[2] <= today
                    and "General Availability" in cells[1]
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[6])
                    and re.fullmatch(r"\d+\.\d+", cells[7])):
                rows.append(cells)
        if not rows:
            raise WebSearchError("official version table not recognized")
        latest = max(rows, key=lambda cells: cells[2])
        excerpt = (f"La versión de características con fecha de disponibilidad general más reciente "
                   f"en la tabla oficial es {latest[0]} ({latest[2]}). "
                   f"Compilación publicada: {latest[7]}; fecha de revisión: {latest[6]}. "
                   "Versión de características y compilación son datos distintos. "
                   "La disponibilidad en dispositivos existentes depende del canal y la compatibilidad; "
                   "una versión para dispositivos nuevos no implica una actualización para todos los equipos.")
        return WebSearchResult("Windows 11 — información de versiones (Microsoft)", cls.WINDOWS_RELEASE_URL, excerpt)

    async def fetch_url(self, url: str, timeout: float = 12.0) -> dict:
        """Compatibility adapter restricted to the fixed official source."""
        try:
            if not self.enabled or isinstance(self.backend, OfflineSearch):
                raise WebSearchError("web retrieval disabled")
            async with asyncio.timeout(min(20, max(1, timeout))):
                page = await self._read_official(url)
            result = self._windows_release(page)
            return {"title":result.title, "url":url, "status":200}
        except (WebSearchError, TimeoutError):
            return {"url":url, "status":"unavailable", "error":"Source unavailable or destination not allowed."}



_WEB_NEED_QUERY = re.compile(
    r"seg[uú]n\s+(?:internet|la web)|busca.*(?:internet|web)|b[uú]squeda.*(?:internet|web)|search.*(?:internet|web)|versi[oó]n\s+(m[áa]s|more\s+recent)|reciente|últim|última|last\s+version|latest|what\s+is\s+the\s+current",
    re.IGNORECASE,
)

# Labels shown in the chat UI, on the same status map used for knowledge and social.
WEB_ROUTE_LABEL = "Búsqueda pública"



