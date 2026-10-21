from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(default="1", min_length=1, max_length=50)
    procedure_id: str | None = Field(default=None, max_length=80)
    procedure_key: str | None = Field(default=None, max_length=200)
    scope: str = Field(default="General", min_length=1, max_length=100)
    authorized: bool = False


class AccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorized: bool


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(strict=True, min_length=1, max_length=16000)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    choice_document_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


class KnowledgeAnswer(BaseModel):
    status: Literal["answered", "clarification_required", "conflict", "insufficient_evidence"]
    answer: str
    conversation_id: str
    intent: Literal["identify", "steps", "information"]
    sources: list[dict] = Field(default_factory=list)
    options: list[dict] = Field(default_factory=list)
    documentation_sufficient: bool = False
    knowledge_type: str = "documented_evidence"
    retrieval: dict = Field(default_factory=dict)
