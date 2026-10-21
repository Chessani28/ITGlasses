from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.app.api.routes import router
from backend.app.core.config import settings
from backend.app.providers.base import LLMProvider, ProviderTimeoutError, ProviderUnavailableError, ModelUnavailableError, ProviderResponseError, ProviderOutputLimitError
from backend.app.providers.mock import MockLLM
from backend.app.providers.ollama import OllamaProvider
from backend.app.knowledge.routes import router as knowledge_router
from backend.app.knowledge.models import KnowledgeError


def error_response(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(provider: LLMProvider | None = None, knowledge=None, voice=None) -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version)
    app.state.provider = provider if provider is not None else (
        OllamaProvider(settings) if settings.provider == "ollama" else MockLLM()
    )
    app.include_router(router)
    app.state.knowledge = knowledge
    app.include_router(knowledge_router)
    from backend.app.voice.routes import router as voice_router
    app.state.voice = voice
    app.include_router(voice_router)

    @app.middleware("http")
    async def upload_limit(request: Request, call_next):
        if request.url.path.startswith("/api/v1/documents") and request.method in {"POST", "PUT"} and "multipart" in request.headers.get("content-type", ""):
            length = request.headers.get("content-length", "")
            if not length.isdecimal():
                return error_response(411, "length_required", "Document uploads require Content-Length.")
            if int(length) > 10 * 1024 * 1024 + 65536:
                return error_response(413, "file_size", "Document upload exceeds 10 MiB plus metadata allowance.")
        return await call_next(request)

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
        return error_response(exc.status_code, "http_error", "The requested resource or method is unavailable.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return error_response(500, "internal_error", "An internal error occurred.")

    from backend.app.frontend import mount_frontend
    mount_frontend(app)
    return app


app = create_app()
