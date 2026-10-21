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


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class StatusResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    provider: str
    model: str
    mock: bool
    rag_enabled: bool = False
    local_only: bool = True
