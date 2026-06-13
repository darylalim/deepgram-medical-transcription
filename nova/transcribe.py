"""Deepgram option building and concurrent batch transcription for every front-end."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from deepgram import DeepgramClient

from nova.config import (
    DEFAULT_DIARIZE,
    DEFAULT_DICTATION,
    DEFAULT_MEASUREMENTS,
    DEFAULT_SMART_FORMAT,
    MAX_CONCURRENCY,
    MODEL,
)


def build_options(
    *,
    keyterms: list[str] | None = None,
    language: str | None = None,
    smart_format: bool = DEFAULT_SMART_FORMAT,
    dictation: bool = DEFAULT_DICTATION,
    measurements: bool = DEFAULT_MEASUREMENTS,
    diarize: bool = DEFAULT_DIARIZE,
    redact: list[str] | None = None,
    timeout_in_seconds: int | None = None,  # API-only; the UI never passes it
) -> dict[str, Any]:
    """Build the kwargs dict passed to the Deepgram transcribe call.

    `model` and `smart_format` are always sent; off-by-default features are sent only
    when enabled (Deepgram defaults them off). Dictation requires punctuation, so it
    forces `punctuate=True`. `redact` (typed as a single str by the SDK) goes through
    `request_options` as repeated query params; `timeout_in_seconds` merges into the
    same `request_options` dict, which is omitted entirely when both are unset.
    """
    request_options: dict[str, Any] = {}
    if redact:
        request_options["additional_query_parameters"] = {"redact": redact}
    if timeout_in_seconds is not None:
        request_options["timeout_in_seconds"] = timeout_in_seconds
    return {
        "model": MODEL,
        "smart_format": smart_format,
        **({"diarize": True} if diarize else {}),
        **({"measurements": True} if measurements else {}),
        # Dictation requires punctuation, so enable both together.
        **({"dictation": True, "punctuate": True} if dictation else {}),
        **({"keyterm": keyterms} if keyterms else {}),
        **({"language": language} if language else {}),
        **({"request_options": request_options} if request_options else {}),
    }


@dataclass
class ItemResult:
    """One batch item's outcome, tagged with its input index for order restoration.

    `error` holds `str(exc)` with no prefix — the calling adapter (Streamlit's
    `st.error`, the API's per-item envelope) owns how it is presented.
    """

    index: int
    label: str
    response: Any | None = None
    error: str | None = None


def transcribe_batch(
    api_key: str,
    items: list[tuple[str, dict[str, Any]]],
    method: str,
    *,
    options: dict[str, Any],
    client_cls: Callable[..., Any] | None = None,
    as_completed_fn: Callable[..., Any] | None = None,
    max_concurrency: int = MAX_CONCURRENCY,
    gate: AbstractContextManager[Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ItemResult]:
    """Transcribe a batch concurrently with one shared client; return results in input order.

    `method` is "transcribe_file" or "transcribe_url"; each item is `(label, call_kwargs)`
    and `options` (from `build_options`) is merged onto every call. `client_cls` and
    `as_completed_fn` default to this module's globals resolved **at call time** (not as
    def-time defaults), so `patch("nova.transcribe.DeepgramClient")` intercepts the
    default while in-process callers (the Streamlit wrapper) inject their own module
    globals as seams. An optional `gate` context manager wraps each upstream call for
    process-wide concurrency limiting (`None` is a no-op, leaving the in-process path
    unchanged); `on_progress(done, total)` fires once per completion (success or
    failure). Per-item exceptions are captured as `ItemResult(error=...)` so one
    failure never aborts the batch; results are sorted back into input order.
    """
    if client_cls is None:
        client_cls = DeepgramClient
    if as_completed_fn is None:
        as_completed_fn = as_completed

    client = client_cls(api_key=api_key)
    transcribe = getattr(client.listen.v1.media, method)

    def _call(call_kwargs: dict[str, Any]) -> Any:
        if gate is None:
            return transcribe(**call_kwargs, **options)
        with gate:
            return transcribe(**call_kwargs, **options)

    total = len(items)
    results: list[ItemResult] = []
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = {
            executor.submit(_call, kwargs): (i, label)
            for i, (label, kwargs) in enumerate(items)
        }
        for done, future in enumerate(as_completed_fn(futures), start=1):
            i, label = futures[future]
            if on_progress is not None:
                on_progress(done, total)
            try:
                results.append(
                    ItemResult(index=i, label=label, response=future.result())
                )
            except Exception as e:
                results.append(ItemResult(index=i, label=label, error=str(e)))

    results.sort(key=lambda r: r.index)
    return results
