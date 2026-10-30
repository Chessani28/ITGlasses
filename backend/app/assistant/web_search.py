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


class WebSearchError(Exception):
    """Raised when a public search provider cannot return results."""


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
                    return list(ddgs.text(query.strip(), max_results=max_results))
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

    def __init__(self, backend=None, enabled: bool = True):
        self.enabled = enabled
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
        if not self.enabled:
            return []
        self.last_error = None
        official_microsoft = bool(re.search(r"windows|microsoft", query, re.I) and re.search(r"oficial|official", query, re.I))
        windows = re.search(r"windows\s+(\d+)", query, re.I)
        if official_microsoft:
            # Search terms, not a canned answer: release values are fetched below.
            if windows:
                query = f"Windows {windows.group(1)} latest version release information"
            query += " site:learn.microsoft.com"
        try:
            results = await self.backend.search(query, max_results)
            if official_microsoft:
                results = [r for r in results if (urlsplit(r.url).hostname or "").endswith(".microsoft.com")]
            if official_microsoft and windows:
                for index, result in enumerate(results):
                    parsed = urlsplit(result.url)
                    if parsed.hostname == "learn.microsoft.com" and parsed.path.endswith(f"/windows{windows.group(1)}-release-information"):
                        # Read the live official table rather than trusting a stale snippet.
                        enriched = await asyncio.to_thread(self._windows_release, result)
                        results[index] = enriched
                        results.insert(0, results.pop(index))
                        break
            logging.getLogger("uvicorn.error").info("Public search backend=%s results=%d", type(self.backend).__name__, len(results))
        except WebSearchError as exc:
            self.last_error = exc
            logging.getLogger("uvicorn.error").warning("Public search failed: %s", exc)
            return []
        if not results:
            self.last_error = WebSearchError("provider returned no public sources")
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
            return WebSearchResult(result.title, result.url, excerpt)
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



