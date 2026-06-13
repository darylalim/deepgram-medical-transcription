"""Pydantic request/response models plus the API error type.

The option surface is validated against `nova.config` (single source of truth), so the
API cannot accept a feature the core does not support. Domain-rule failures raise
`PydanticCustomError` whose `type` IS the machine-readable error `code` surfaced in the
envelope (see `api.main`).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from nova.config import LANGUAGES, MAX_KEYTERMS, MAX_UPLOADS, REDACT_GROUPS


class ApiError(Exception):
    """An HTTP error carrying the `{type, code, message}` envelope fields and headers."""

    def __init__(
        self,
        status_code: int,
        type: str,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.type = type
        self.code = code
        self.message = message
        self.headers = headers


class TranscriptionOptions(BaseModel):
    """Shared feature surface for both endpoints. Unknown fields are rejected, which is
    how a client-supplied `model` is refused — the model is pinned server-side."""

    model_config = ConfigDict(extra="forbid")

    smart_format: bool = True
    diarize: bool = False
    dictation: bool = False
    measurements: bool = False
    keyterms: list[str] = Field(default_factory=list)
    language: str = "en"
    redact: list[str] = Field(default_factory=list)
    include_raw: bool = False
    include_words: bool = False

    @field_validator("language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        if v not in LANGUAGES:
            raise PydanticCustomError(
                "invalid_language",
                "language must be one of {allowed}",
                {"allowed": ", ".join(LANGUAGES)},
            )
        return v

    @field_validator("redact")
    @classmethod
    def _check_redact(cls, v: list[str]) -> list[str]:
        if any(g not in REDACT_GROUPS for g in v):
            raise PydanticCustomError(
                "invalid_redact_group",
                "redact must be a subset of {allowed}",
                {"allowed": ", ".join(REDACT_GROUPS)},
            )
        return v

    @field_validator("keyterms")
    @classmethod
    def _check_keyterms(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_KEYTERMS:
            raise PydanticCustomError(
                "too_many_keyterms",
                "keyterms cannot exceed {max} items",
                {"max": MAX_KEYTERMS},
            )
        return v


class UrlBatchRequest(TranscriptionOptions):
    """JSON body for the URL endpoint. URLs are plain strings (not Pydantic HttpUrl, which
    would normalize and change the bytes sent upstream) passed to Deepgram verbatim."""

    urls: list[str]

    @field_validator("urls")
    @classmethod
    def _check_urls(cls, v: list[str]) -> list[str]:
        if not v:
            raise PydanticCustomError("no_urls", "urls must contain at least one item")
        if len(v) > MAX_UPLOADS:
            raise PydanticCustomError(
                "too_many_urls", "urls cannot exceed {max} items", {"max": MAX_UPLOADS}
            )
        if any(not u.startswith(("http://", "https://")) for u in v):
            raise PydanticCustomError(
                "invalid_url_scheme", "every url must start with http:// or https://"
            )
        return v


class ItemError(BaseModel):
    type: str
    code: str
    message: str


class Segment(BaseModel):
    speaker: int  # Deepgram's native 0-based index (the UI's +1 offset is display-only)
    text: str


class ItemOut(BaseModel):
    index: int
    name: str
    status: str  # "ok" | "error"
    transcript: str | None = None
    segments: list[Segment] | None = None
    words: list[dict[str, Any]] | None = None
    request_id: str | None = None
    duration: float | None = None
    raw: dict[str, Any] | None = None
    error: ItemError | None = None


class BatchSummary(BaseModel):
    total: int
    succeeded: int
    failed: int


class BatchResponse(BaseModel):
    model: str
    status: str  # "completed" | "partially_completed" | "failed" (== every item failed)
    summary: BatchSummary
    warnings: list[str]
    results: list[ItemOut]


class ErrorDetail(BaseModel):
    type: str
    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
