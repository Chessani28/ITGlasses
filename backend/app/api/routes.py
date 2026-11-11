from fastapi import APIRouter, Depends, Request

from backend.app.api.auth import require_auth
from backend.app.api.schemas import (AssistantRequest, AssistantResponse, ChatRequest, ChatResponse,
                                    HealthResponse, StatusResponse)
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


@router.post("/api/v1/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
async def chat(payload: ChatRequest, request: Request, provider: LLMProvider = Depends(get_provider)):
    from backend.app.voice.service import release_whisper_for_chat
    await release_whisper_for_chat(request)
    await provider.ensure_available()
    answer = await provider.generate(payload.message)
    return ChatResponse(answer=answer, provider=provider.name, model=provider.model)


@router.post("/api/v1/assistant/chat", response_model=AssistantResponse, dependencies=[Depends(require_auth)])
async def assistant_chat(payload: AssistantRequest, request: Request):
    from backend.app.voice.service import release_whisper_for_chat
    await release_whisper_for_chat(request)
    try:
        from backend.app.knowledge.routes import service as get_knowledge_service
        knowledge = get_knowledge_service(request)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("assistant").exception("knowledge service unavailable: %s", exc)
        knowledge = None
    provider = request.app.state.provider
    from backend.app.assistant.service import UnifiedAssistantService
    if not hasattr(request.app.state, "assistant"):
        request.app.state.assistant = UnifiedAssistantService(knowledge, provider)
    else:
        request.app.state.assistant.knowledge = knowledge
        request.app.state.assistant.provider = provider
    result = await request.app.state.assistant.chat(payload.message, payload.session_id)
    return AssistantResponse(**result)
