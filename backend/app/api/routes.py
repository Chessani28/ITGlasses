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
    assistant = _assistant(request, knowledge)
    # Stage "historial" 2: the conversation is saved (web with a login, devices with an owner) and survives restarts;
    # a device's open app and glasses mode share one conversation. Anything else runs in memory, as before.
    from backend.app.history.chat import chat as history_chat
    result = await history_chat(request, assistant, request.app.state.conversation_locks, payload.message,
                                payload.session_id)
    from backend.app.assistant.speech import spoken_summary
    # In voice mode the assistant says what is read aloud (always the "speech" key, possibly None); otherwise the
    # usual summary of the answer.
    speech = result.pop("speech") if "speech" in result else spoken_summary(result.get("answer"))
    return AssistantResponse(**result, speech=speech)


def _assistant(request, knowledge):
    """The one assistant of the backend (E2): created at the first question with its in-memory state, never
    recreated; only the knowledge service (built lazily) or a replaced provider are refreshed."""
    from backend.app.assistant.service import UnifiedAssistantService
    state = request.app.state
    provider = state.provider
    assistant = getattr(state, "assistant", None)
    if assistant is None:
        assistant = state.assistant = UnifiedAssistantService(knowledge, provider)
        return assistant
    # Another assistant (e.g. one a test injects) is used as it is: only attributes it has are refreshed.
    for name, value in (("knowledge", knowledge), ("provider", provider)):
        if hasattr(assistant, name) and getattr(assistant, name) is not value:
            setattr(assistant, name, value)
    return assistant
