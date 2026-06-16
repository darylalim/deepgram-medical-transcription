"""FastAPI app: routes, exception handlers, request-id + body-size middleware, the
process-global spend gate, and fail-closed startup.

Both endpoints validate fail-fast (structural errors reject the whole request before any
Deepgram call), then run the shared `nova.transcribe.transcribe_batch` off the event loop
via `run_in_threadpool`, gated by a process-wide semaphore so N concurrent requests cannot
multiply into 5xN upstream calls.
"""

import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from api import settings
from api.auth import require_token
from api.schemas import (
    ApiError,
    BatchResponse,
    BatchSummary,
    ErrorDetail,
    ErrorEnvelope,
    ItemError,
    ItemOut,
    Segment,
    TranscriptionOptions,
    UrlBatchRequest,
)
from nova.config import MAX_FILE_SIZE, MAX_UPLOADS, MODEL, has_audio_extension
from nova.results import diarized_segments, transcript_text, word_list
from nova.transcribe import ItemResult, build_options, transcribe_batch

logger = logging.getLogger("api")
# PHI hygiene: the SDK sends keyterm/redact as upstream query params, so DEBUG URL logging
# would leak them. Keep the HTTP/SDK loggers quiet regardless of root level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("deepgram").setLevel(logging.WARNING)

# Process-global spend governor: <=N concurrent upstream calls across ALL requests. Must be
# a threading semaphore — the calls run in executor worker threads, not on the event loop.
GLOBAL_SEMAPHORE = threading.BoundedSemaphore(settings.global_max_concurrency())

# Chunk size for the capped streamed read of multipart uploads.
_READ_CHUNK = 1024 * 1024

# (status, type, code, message) for an over-budget request body — emitted from both the
# Content-Length precheck (as an _envelope) and the streamed read (as an ApiError).
_TOO_LARGE = (
    413,
    "payload_too_large",
    "request_body_too_large",
    "Request body exceeds the configured limit; use URL batches for bulk audio.",
)

# Pydantic error `type`s that are already machine-readable envelope codes.
_DOMAIN_CODES = frozenset(
    {
        "invalid_language",
        "invalid_redact_group",
        "too_many_keyterms",
        "no_urls",
        "too_many_urls",
        "invalid_url_scheme",
    }
)

# Best-effort mapping for StarletteHTTPException paths (routing-level 404/405/etc.).
_HTTP_TYPES = {
    400: "invalid_request",
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail closed: never serve a non-loopback bind without auth tokens configured.
    if not settings.is_loopback() and not settings.auth_tokens():
        raise RuntimeError(
            "Refusing to start: non-loopback API_HOST without API_AUTH_TOKENS."
        )
    yield


# OpenAPI docs expose shape (no data/keys) and are a development convenience on loopback;
# they are dropped entirely on a non-loopback bind (which also mandates TLS + audit).
_DOCS = settings.is_loopback()
app = FastAPI(
    title="Deepgram Medical Transcription API",
    version="1.0.0",
    summary="Transcribe medical audio with Deepgram Nova-3 Medical (model pinned server-side).",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _DOCS else None,
)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _envelope(
    status_code: int,
    type: str,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorDetail(type=type, code=code, message=message, request_id=request_id)
    )
    response = JSONResponse(status_code=status_code, content=body.model_dump())
    response.headers["X-Request-ID"] = request_id
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


@app.middleware("http")
async def _context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id

    # Body-size guard: uvicorn does not enforce it, so reject oversized requests up front.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_big = int(content_length) > settings.max_request_bytes()
        except ValueError:
            too_big = False
        if too_big:
            return _envelope(*_TOO_LARGE, request_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    # Sanitized access log — never bodies, filenames, keyterms, or full URLs.
    logger.info(
        "%s %s -> %s [%s]",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


@app.exception_handler(ApiError)
async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _envelope(
        exc.status_code,
        exc.type,
        exc.code,
        exc.message,
        _request_id(request),
        exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    code, message = _first_error(exc.errors())
    return _envelope(422, "validation_error", code, message, _request_id(request))


@app.exception_handler(StarletteHTTPException)
async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    type = _HTTP_TYPES.get(exc.status_code, "http_error")
    headers = getattr(exc, "headers", None)
    return _envelope(
        exc.status_code, type, type, str(exc.detail), _request_id(request), headers
    )


@app.exception_handler(Exception)
async def _handle_unexpected(request: Request, _exc: Exception) -> JSONResponse:
    # Scrub: never leak stack traces or content — to the client OR the logs. An unexpected
    # exception's message/traceback could embed request content (§6.4), so log only its
    # class and the correlation id.
    logger.error("unhandled error [%s]: %s", _request_id(request), type(_exc).__name__)
    return _envelope(
        500,
        "internal_error",
        "unexpected",
        "An unexpected error occurred.",
        _request_id(request),
    )


def _first_error(errors: Sequence[Any]) -> tuple[str, str]:
    """Turn Pydantic errors into a single (code, message) for the envelope."""
    for err in errors:
        if err.get("type") in _DOMAIN_CODES:
            return err["type"], err.get("msg", err["type"])
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = first.get("msg", "invalid request")
        return "malformed_body", f"{loc}: {msg}" if loc else msg
    return "malformed_body", "Invalid request body."


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only — never calls Deepgram (no spend, no key-validity oracle)."""
    return {"status": "ok"}


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])


@router.post("/transcriptions/urls", response_model=BatchResponse)
async def transcribe_urls(body: UrlBatchRequest) -> BatchResponse:
    api_key = _require_key()
    warnings = [
        f"No recognized audio extension (transcribed anyway): item {i}"
        for i, url in enumerate(body.urls)
        if not has_audio_extension(url)
    ]
    sendable = [(i, url, {"url": url}) for i, url in enumerate(body.urls)]
    return await _run_batch(api_key, sendable, {}, "transcribe_url", body, warnings)


@router.post("/transcriptions/files", response_model=BatchResponse)
async def transcribe_files(
    files: list[UploadFile] = File(default=[]),
    smart_format: bool = Form(default=True),
    diarize: bool = Form(default=False),
    dictation: bool = Form(default=False),
    measurements: bool = Form(default=False),
    language: str = Form(default="en"),
    include_raw: bool = Form(default=False),
    include_words: bool = Form(default=False),
    keyterms: list[str] = Form(default=[]),
    redact: list[str] = Form(default=[]),
) -> BatchResponse:
    if not files:
        raise ApiError(400, "invalid_request", "no_files", "No files provided.")
    if len(files) > MAX_UPLOADS:
        raise ApiError(
            422,
            "validation_error",
            "too_many_files",
            f"Cannot exceed {MAX_UPLOADS} files per batch.",
        )
    opts = _options_from_form(
        smart_format,
        diarize,
        dictation,
        measurements,
        language,
        include_raw,
        include_words,
        keyterms,
        redact,
    )
    api_key = _require_key()

    # Capped streamed read: the middleware precheck can be bypassed with an absent or
    # falsified Content-Length, so the request-byte budget is also enforced here, during
    # parsing, charged cumulatively across all parts (§5.1).
    budget = settings.max_request_bytes()
    consumed = 0
    sendable: list[tuple[int, str, dict[str, Any]]] = []
    errors: dict[int, ItemOut] = {}
    for i, upload in enumerate(files):
        data = await _read_capped(upload, budget - consumed)
        consumed += len(data)
        name = upload.filename or f"file{i}"
        if len(data) > MAX_FILE_SIZE:
            errors[i] = ItemOut(
                index=i,
                name=name,
                status="error",
                error=ItemError(
                    type="file_too_large",
                    code="file_too_large",
                    message="File exceeds the 2 GiB per-file limit.",
                ),
            )
        else:
            sendable.append((i, name, {"request": data}))

    return await _run_batch(api_key, sendable, errors, "transcribe_file", opts, [])


def _require_key() -> str:
    key = settings.deepgram_api_key()
    if not key:
        raise ApiError(
            503,
            "not_configured",
            "missing_deepgram_key",
            "Server has no DEEPGRAM_API_KEY configured.",
        )
    return key


async def _read_capped(upload: UploadFile, budget_left: int) -> bytes:
    """Read an upload in chunks, rejecting with 413 the moment it would exceed `budget_left`.

    The defense against an absent/falsified Content-Length: the cap is enforced while the
    bytes are consumed, so a streamed body cannot exhaust memory by bypassing the
    middleware precheck.
    """
    chunks: list[bytes] = []
    read = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        read += len(chunk)
        if read > budget_left:
            raise ApiError(*_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _options_from_form(
    smart_format: bool,
    diarize: bool,
    dictation: bool,
    measurements: bool,
    language: str,
    include_raw: bool,
    include_words: bool,
    keyterms: list[str],
    redact: list[str],
) -> TranscriptionOptions:
    """Build (and domain-validate) the shared model from multipart form fields."""
    try:
        return TranscriptionOptions(
            smart_format=smart_format,
            diarize=diarize,
            dictation=dictation,
            measurements=measurements,
            language=language,
            include_raw=include_raw,
            include_words=include_words,
            keyterms=keyterms,
            redact=redact,
        )
    except ValidationError as exc:
        code, message = _first_error(exc.errors())
        raise ApiError(422, "validation_error", code, message) from exc


async def _run_batch(
    api_key: str,
    sendable: list[tuple[int, str, dict[str, Any]]],
    errors: dict[int, ItemOut],
    method: str,
    opts: TranscriptionOptions,
    warnings: list[str],
) -> BatchResponse:
    options = build_options(
        keyterms=opts.keyterms or None,
        language=opts.language,
        smart_format=opts.smart_format,
        dictation=opts.dictation,
        measurements=opts.measurements,
        diarize=opts.diarize,
        redact=opts.redact or None,
        timeout_in_seconds=settings.deepgram_timeout_seconds(),
    )
    items = [(name, kwargs) for (_, name, kwargs) in sendable]
    results = await run_in_threadpool(
        transcribe_batch, api_key, items, method, options=options, gate=GLOBAL_SEMAPHORE
    )

    by_index: dict[int, ItemOut] = dict(errors)
    for result in results:
        original_index, name, _ = sendable[result.index]
        by_index[original_index] = _item_out(original_index, name, result, opts)

    # sendable and errors partition all input items by original index, so their sizes
    # sum to the batch total; rebuild the ordered list over that contiguous range.
    total = len(sendable) + len(errors)
    ordered = [by_index[i] for i in range(total)]
    failed = sum(1 for item in ordered if item.status == "error")
    succeeded = total - failed
    status = (
        "completed"
        if failed == 0
        else "failed"
        if succeeded == 0
        else "partially_completed"
    )
    return BatchResponse(
        model=MODEL,
        status=status,
        summary=BatchSummary(total=total, succeeded=succeeded, failed=failed),
        warnings=warnings,
        results=ordered,
    )


def _item_out(
    index: int, name: str, result: ItemResult, opts: TranscriptionOptions
) -> ItemOut:
    if result.error is not None:
        type, code = _classify_upstream(result.error)
        return ItemOut(
            index=index,
            name=name,
            status="error",
            error=ItemError(type=type, code=code, message=result.error),
        )
    response: Any = result.response
    segments = diarized_segments(response)
    metadata = getattr(response, "metadata", None)
    return ItemOut(
        index=index,
        name=name,
        status="ok",
        transcript=transcript_text(response),
        segments=[Segment(speaker=s, text=t) for s, t in segments]
        if segments
        else None,
        words=word_list(response) if opts.include_words else None,
        request_id=getattr(metadata, "request_id", None),
        duration=getattr(metadata, "duration", None),
        raw=response.model_dump()
        if opts.include_raw and response is not None
        else None,
    )


def _classify_upstream(message: str) -> tuple[str, str]:
    lowered = message.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "upstream_timeout", "deepgram_timeout"
    return "upstream_error", "deepgram_request_failed"


app.include_router(router)
