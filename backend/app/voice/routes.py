"""Local transport adapters; no alternate chat or authorization implementation."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from backend.app.knowledge.models import KnowledgeError
from backend.app.knowledge.routes import service as knowledge_service
from .normalize import normalize_for_speech
from .service import MAX_AUDIO, VOICE_NAME, VoiceService, validate_audio

router = APIRouter(prefix="/api/v1/voice")


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=6000, strict=True)
    voice: Literal["es_ES-sharvard-medium"] = VOICE_NAME
    speaker: Literal[0, 1] = 0
    speed: float = Field(default=1, ge=0.75, le=1.5)
    citation_ids: list[UUID] = Field(default_factory=list, max_length=20)


def speech_service(request):
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
        raise KnowledgeError("origin_rejected", "Cross-origin speech requests are not permitted.", 403)
    if request.app.state.voice is None:
        request.app.state.voice = VoiceService()
    return request.app.state.voice


@router.get("/status")
async def status(request: Request):
    return speech_service(request).status()


@router.post("/transcribe")
async def transcribe(request: Request):
    svc = speech_service(request)
    if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
        raise KnowledgeError("audio_format", "Send audio/wav, not browser-compressed audio.", 415)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_AUDIO:
            raise KnowledgeError("audio_size", "Audio exceeds the 30-second limit.", 413)
    validate_audio(raw)
    return await svc.run("stt", bytes(raw), request)


@router.post("/synthesize")
async def synthesize(payload: SpeechRequest, request: Request):
    svc = speech_service(request)
    async def verify_sources():
        if payload.citation_ids:
            knowledge = knowledge_service(request)
            async with knowledge.lock:
                if any(not knowledge.store.evidence(str(cid)) for cid in payload.citation_ids):
                    raise KnowledgeError("source_unavailable", "A referenced source is no longer authorized or current. Ask the question again.", 409)
    await verify_sources()
    # Normalize text for speech synthesis
    speech_text = normalize_for_speech(payload.text)
    tts_payload = payload.model_dump(exclude={"citation_ids"})
    tts_payload["text"] = speech_text
    result = await svc.run("tts", tts_payload, request)
    await verify_sources()
    return Response(result["audio"], media_type="audio/wav", headers={"Cache-Control": "no-store",
        "X-Voice-Elapsed-Ms": str(result["elapsed_ms"]), "X-Voice-CPU-Seconds": str(result.get("cpu_seconds", "")),
        "X-Voice-Peak-MiB": str(result.get("peak_working_set_mib", "")), "X-Content-Type-Options": "nosniff"})
