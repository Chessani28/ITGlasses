"""Public web search route for the assistant.

This is the WEB route of the unified assistant. It is exposed for tooling and
testing; the assistant chat layer decides when a general route question needs
live public information (see UnifiedAssistantService._needs_web and chat).
"""

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["assistant"])


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    provider: str | None = Field(default=None, max_length=64)
    max_results: int = Field(default=5, ge=1, le=20)


class WebSearchResponse(BaseModel):
    query: str
    results: list[dict]
    retrieved_at: str
    provider: str
    provider_error: str | None = None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post("/web/search", response_model=WebSearchResponse)
async def web_search(request: Request, payload: WebSearchRequest = Body(...)):
    """Run a public web search and return live, citable results.

    The current provider is Free/DuckDuckGo (no API key). It may return no results
    or fail while the provider is public; in that case the response carries the
    error but still returns the requested shape.
    """
    backend = payload.provider or "duckduckgo"
    try:
        from backend.app.assistant.web_search import DuckDuckGoSearch, WebSearchService
    except Exception as exc:  # pragma: no cover - import guard
        return WebSearchResponse(
            query=payload.query,
            results=[],
            retrieved_at="",
            provider=backend,
            provider_error=f"provider module unavailable: {exc}",
        )

    service = WebSearchService(
        backend=DuckDuckGoSearch(),
        enabled=backend in {"duckduckgo", "ddg", "free"},
    )
    results = await service.search(payload.query, max_results=payload.max_results)
    return WebSearchResponse(
        query=payload.query,
        results=[
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "retrieved_at": item.retrieved_at,
            }
            for item in results
        ],
        retrieved_at=(
            results[0].retrieved_at if results else "2026-10-22T00:00:00+00:00"
        ),
        provider=backend,
        provider_error=service.last_error.args[0] if service.last_error else None,
    )
