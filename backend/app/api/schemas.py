from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(strict=True, min_length=1, max_length=4000)


class Source(BaseModel):
    document: str
    page: int | None = Field(default=None, ge=1)


class ChatResponse(BaseModel):
    answer: str
    provider: str
    model: str
    sources: list[Source] = Field(default_factory=list)
    documentation_sufficient: bool = False


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(strict=True, min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)
    # A paired phone says when the question came through the glasses (voice with "Usar lentes"): the history shows
    # the glasses icon. Ignored for the web and the API token.
    via: Literal["phone", "glasses"] | None = None


class AssistantResponse(BaseModel):
    answer: str
    session_id: str
    route: Literal["social", "general", "knowledge"]
    sources: list = Field(default_factory=list)
    # What the voice reads of a long answer (title and first steps, or first sentences). None: read the answer.
    speech: str | None = None
    # Stage "historial": whether this exchange was saved in the chat history (older clients ignore it).
    history_saved: bool = False
    # The saved answer's message id, for 👍/👎 (None when the exchange was not saved).
    message_id: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class StatusResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    provider: str
    model: str
    mock: bool
    rag_enabled: bool = False
    local_only: bool = True
