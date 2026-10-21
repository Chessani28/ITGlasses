from fastapi import APIRouter, Depends, Request

from backend.app.api.schemas import ChatRequest, ChatResponse, HealthResponse, StatusResponse
from backend.app.providers.base import LLMProvider

router = APIRouter()


def get_provider(request: Request) -> LLMProvider:
    return request.app.state.provider


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/api/v1/status", response_model=StatusResponse)
async def status(provider: LLMProvider = Depends(get_provider)):
    available = await provider.is_available()
    return StatusResponse(
        status="ready" if available else "unavailable",
        provider=provider.name, model=provider.model, mock=provider.name == "mock",
    )


@router.post("/api/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, provider: LLMProvider = Depends(get_provider)):
    await provider.ensure_available()
    answer = await provider.generate(payload.message)
    return ChatResponse(answer=answer, provider=provider.name, model=provider.model)
