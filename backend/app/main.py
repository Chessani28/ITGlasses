import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
import logging

logger = logging.getLogger(__name__)

from backend.app.api.routes import router
from backend.app.core.config import settings
from backend.app.providers.base import LLMProvider, ProviderTimeoutError, ProviderUnavailableError, ModelUnavailableError, ProviderResponseError, ProviderOutputLimitError
from backend.app.providers.mock import MockLLM
from backend.app.providers.ollama import OllamaProvider
from backend.app.knowledge.routes import router as knowledge_router
from backend.app.knowledge.models import KnowledgeError


def error_response(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _start_whisper_preload(app):
    """Voz 3: background Whisper load at startup when ITGLASSES_WHISPER_PRELOAD is on (off by default)."""
    from backend.app.voice.service import VoiceService, whisper_preload_enabled

    if not whisper_preload_enabled():
        return None
    if getattr(app.state, "voice", None) is None:
        app.state.voice = VoiceService()
    preload = getattr(app.state.voice, "preload_whisper", None)
    return asyncio.create_task(preload()) if preload else None


def _start_piper_preload(app):
    """Background load of the persistent Piper voice (ITGLASSES_PIPER_PERSISTENT, on by default). Never blocks."""
    from backend.app.voice.service import VoiceService, piper_persistent_enabled

    if not piper_persistent_enabled():
        return None
    if getattr(app.state, "voice", None) is None:
        app.state.voice = VoiceService()
    preload = getattr(app.state.voice, "preload_piper", None)
    return asyncio.create_task(preload()) if preload else None


def _check_password_settings():
    """Refuse to start with an invalid ITGLASSES_PASSWORD_MIN_LENGTH instead of silently using the default."""
    from backend.app.users.passwords import PasswordConfigError, min_length

    try:
        logger.info("password_policy min_length=%d", min_length())
    except PasswordConfigError as error:
        logger.error("Invalid configuration: %s", error)
        raise


def ollama_warmup_enabled(value=None):
    """ITGLASSES_OLLAMA_WARMUP: load the chat model at startup. On by default; 0/false/off/no turn it off."""
    import os

    raw = os.environ.get("ITGLASSES_OLLAMA_WARMUP") if value is None else value
    return str(raw or "").strip().lower() not in {"0", "false", "off", "no"}


# After a reboot Ollama may start after the backend: the warm-up is retried in the background.
OLLAMA_WARMUP_RETRY_SECONDS = 5
OLLAMA_WARMUP_RETRY_WINDOW_SECONDS = 120


async def _warm_up_with_retries(warm_up, interval=None, window=None):
    """Retry the warm-up every ``interval`` seconds until it succeeds or ``window`` seconds have passed.

    Never raises (each failed attempt is already logged by the provider); cancelled with the app. Logs when a
    retried warm-up finally succeeds and when it gives up (the first question then loads the model)."""
    interval = OLLAMA_WARMUP_RETRY_SECONDS if interval is None else interval
    window = OLLAMA_WARMUP_RETRY_WINDOW_SECONDS if window is None else window
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            ready = await warm_up()
        except Exception as exc:  # noqa: BLE001 - warming up is best effort
            logger.warning("ollama_warmup attempt=%d error=%s", attempt, type(exc).__name__)
            ready = False
        if ready:
            if attempt > 1:
                logger.info("ollama_warmup ready attempts=%d elapsed_s=%d", attempt,
                            round(time.monotonic() - started))
            return True
        if time.monotonic() - started + interval > window:
            logger.warning("ollama_warmup gave up attempts=%d window_s=%d; the first question loads the model",
                           attempt, window)
            return False
        await asyncio.sleep(interval)


def _start_ollama_warmup(app, enabled):
    """Background warm-up of the chat model with retries (never blocks or breaks the startup)."""
    warm_up = getattr(app.state.provider, "warm_up", None)
    if not enabled or warm_up is None or not ollama_warmup_enabled():
        return None
    return asyncio.create_task(_warm_up_with_retries(warm_up))


def create_app(provider: LLMProvider | None = None, knowledge=None, voice=None, warmup=None) -> FastAPI:
    """``warmup``: warm the chat model up at startup; by default only for the backend's own provider (None), never
    for a provider a test injects."""
    from backend.app.api.auth import require_auth
    warm = provider is None if warmup is None else warmup

    @asynccontextmanager
    async def lifespan(app):
        _check_password_settings()
        preload_task = None
        warmup_task = None
        piper_task = None
        try:
            preload_task = _start_whisper_preload(app)
            warmup_task = _start_ollama_warmup(app, warm)
            piper_task = _start_piper_preload(app)
            yield
        finally:
            for task in (preload_task, warmup_task, piper_task):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            voice_service = getattr(app.state, "voice", None)
            shutdown = getattr(voice_service, "shutdown", None)
            if shutdown:
                try:
                    await shutdown()
                except Exception:
                    logger.exception("Voice worker shutdown failed")
                    raise

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    app.state.provider = provider if provider is not None else (
        OllamaProvider(settings) if settings.provider == "ollama" else MockLLM()
    )
    from backend.app.pairing.routes import router as pairing_router
    app.include_router(pairing_router)
    from backend.app.pairing.admin import router as admin_router
    app.include_router(admin_router)
    app.include_router(router)
    app.state.knowledge = knowledge
    app.include_router(knowledge_router, dependencies=[Depends(require_auth)])
    from backend.app.api.auth import require_auth
    from backend.app.voice.routes import router as voice_router
    app.state.voice = voice
    app.include_router(voice_router, dependencies=[Depends(require_auth)])

    @app.middleware("http")
    async def upload_limit(request: Request, call_next):
        if request.url.path.startswith("/api/v1/documents") and request.method in {"POST", "PUT"} and "multipart" in request.headers.get("content-type", ""):
            length = request.headers.get("content-length", "")
            if not length.isdecimal():
                return error_response(411, "length_required", "Document uploads require Content-Length.")
            if int(length) > 10 * 1024 * 1024 + 65536:
                return error_response(413, "file_size", "Document upload exceeds 10 MiB plus metadata allowance.")
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/pairing/", "/api/v1/admin/", "/api/v1/devices/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KnowledgeError)
    async def knowledge_error(request: Request, exc: KnowledgeError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/v1/voice"):
            return error_response(422, "voice_validation", "Invalid voice request. Check documented field and size limits.")
        if request.url.path.startswith(("/api/v1/knowledge", "/api/v1/documents")):
            return error_response(422, "validation_error", "Invalid knowledge request. Check field types and documented limits.")
        # Never reflect request contents, which can contain private information.
        return error_response(422, "validation_error", "Send a JSON object with only 'message': a non-empty string of at most 4000 characters.")

    @app.exception_handler(ModelUnavailableError)
    async def model_unavailable(request: Request, exc: ModelUnavailableError):
        return error_response(503, "model_unavailable", "The configured local model is not installed or is not supported locally.")

    @app.exception_handler(ProviderOutputLimitError)
    async def output_limit(request: Request, exc: ProviderOutputLimitError):
        return error_response(502, "provider_output_limit", "The local model reached its output limit before completing a response.")

    @app.exception_handler(ProviderResponseError)
    async def invalid_response(request: Request, exc: ProviderResponseError):
        return error_response(502, "provider_response_error", "The local LLM provider returned an invalid response.")

    @app.exception_handler(ProviderUnavailableError)
    async def unavailable(request: Request, exc: ProviderUnavailableError):
        return error_response(503, "provider_unavailable", "The local LLM provider is unavailable.")

    @app.exception_handler(ProviderTimeoutError)
    async def timeout(request: Request, exc: ProviderTimeoutError):
        return error_response(504, "provider_timeout", "The local LLM provider timed out.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        response = error_response(exc.status_code, "http_error", "The requested resource or method is unavailable.")
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.exception("Internal server error: %s", exc)
        return error_response(500, "internal_error", "An internal error occurred.")

    from backend.app.frontend import mount_frontend
    mount_frontend(app)
    return app


app = create_app()
