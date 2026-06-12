# FastAPI Service Design

**Project:** nova-medical-pipeline
**Date:** 2026-06-12
**Status:** Accepted — the five review questions resolved 2026-06-12 (§11)

---

## 1. Goals & non-goals

### Goals

- Expose the existing Nova-3 Medical transcription pipeline as an HTTP API for the named future consumers: the owner's batch jobs and, eventually, EHR-adjacent services.
- Extract a Streamlit-free shared core so the UI and the API run **the same** option-building, batch-execution, and result-parsing code — drift between front-ends becomes structurally impossible.
- Keep the existing 87-test suite passing **unchanged** at every migration step, and keep the Streamlit UI working at every step.
- Right-size auth and ops for the real threat model: a single developer brokering a paid Deepgram key over PHI-bearing audio, initially on localhost.

### Non-goals (explicitly out of scope now; see §9 for triggers)

- Async job queue / 202+poll batches (pre-specced in §9 so the sync contract coexists with it).
- Streamlit consuming the API over HTTP (Phase 1, §7).
- Multi-tenant identity, per-IP rate limiting, JWT sessions, containers, durable job storage.
- Any change to the model: `nova-3-medical` is pinned server-side and is not a request parameter.

---

## 2. Architecture overview

```
                ┌────────────────────┐
                │  streamlit_app.py  │  widgets, _run, _feature_opts, _parse_urls,
                │  (UI adapter)      │  _playback_source, _display_*, session_state,
                └─────────┬──────────┘  st.progress / st.error
                          │  in-process (passes its module-global
                          │  DeepgramClient / as_completed as seams)
                ┌─────────▼──────────┐
                │       nova/        │  config.py  — constants, single source of truth
                │  (shared core,     │  transcribe.py — build_options(), transcribe_batch()
                │  zero streamlit/   │  results.py — first_alternative(), transcript_text(),
                │  fastapi imports)  │               diarized_segments(), word_list()
                └─────────▲──────────┘
                          │  in-process (default seams resolve at call time →
                          │  patch("nova.transcribe.DeepgramClient") works)
                ┌─────────┴──────────┐
                │        api/        │  main.py — routes, handlers, lifespan, semaphore
                │  (FastAPI adapter) │  schemas.py — Pydantic, validated against nova.config
                │                    │  auth.py — bearer tokens; settings.py — env config
                └────────────────────┘
```

Both front-ends consume `nova/` in-process. The API does **not** wrap the Streamlit app, and the Streamlit app does **not** call the API (Phase 0; rationale in §7). One uv-managed repo, one venv, one `.env`, one test suite plus a new `tests/test_api.py`.

Key mechanism for test survival: `streamlit_app.py` keeps `from deepgram import DeepgramClient` and `from concurrent.futures import as_completed` at module level and passes those **module-global references at call time** into the core. Every existing `patch("streamlit_app.DeepgramClient")` / `patch("streamlit_app.as_completed")` keeps intercepting exactly what it intercepts today. API tests get their own clean mock point because the core's default seams resolve to `nova.transcribe`'s module globals **inside the function body** (not as def-time defaults — see §4.2 for why this matters).

---

## 3. Repo layout

```
nova-medical-pipeline/
├── streamlit_app.py          # UI only: widgets, _run/_feature_opts/_parse_urls, _playback_source,
│                             # _display_* renderers, _output_panel, _escape_markdown, session_state
│                             # writes; _transcribe_batch becomes a thin wrapper over nova/;
│                             # re-imports core constants under old names (test patch points intact)
├── nova/                     # NEW — shared transcription core; imports neither streamlit nor fastapi
│   ├── __init__.py           # re-exports: build_options, transcribe_batch, ItemResult,
│   │                         # transcript_text, diarized_segments, word_list, config names
│   ├── config.py             # MODEL, LANGUAGES, REDACT_GROUPS, DEFAULT_*, MAX_KEYTERMS, MAX_UPLOADS,
│   │                         # MAX_CONCURRENCY, MAX_FILE_SIZE, AUDIO_EXTENSIONS, has_audio_extension()
│   ├── transcribe.py         # build_options(), ItemResult, transcribe_batch();
│   │                         # module-level `from deepgram import DeepgramClient` = API-test mock point
│   └── results.py            # first_alternative(), transcript_text(), diarized_segments() moved
│                             # verbatim; new word_list() for the API's flattened words array
├── api/                      # NEW — FastAPI front-end
│   ├── __init__.py
│   ├── main.py               # app, GET /healthz, POST /v1/transcriptions/{files,urls}, exception
│   │                         # handlers, X-Request-ID middleware, lifespan fail-closed check,
│   │                         # process-global Deepgram semaphore, run_in_threadpool offload
│   ├── schemas.py            # TranscriptionOptions (validated against nova.config), UrlBatchRequest,
│   │                         # ItemOut, BatchSummary, BatchResponse, ErrorEnvelope
│   ├── auth.py               # require_token dependency: Bearer ∈ API_AUTH_TOKENS, compare_digest
│   └── settings.py           # env: DEEPGRAM_API_KEY, API_AUTH_TOKENS, API_HOST,
│                             # MAX_REQUEST_BYTES, DEEPGRAM_TIMEOUT_SECONDS, GLOBAL_MAX_CONCURRENCY
├── conftest.py               # UNCHANGED (repo root on sys.path → streamlit_app, nova, api importable)
├── tests/
│   ├── conftest.py           # UNCHANGED
│   ├── helpers.py            # UNCHANGED (test_api.py reuses mock_word)
│   ├── test_streamlit_app.py # UNCHANGED — all 87 tests pass as-is
│   └── test_api.py           # NEW — TestClient + patch("nova.transcribe.DeepgramClient")
├── pyproject.toml            # + fastapi, uvicorn[standard], python-multipart (runtime); + httpx (dev)
└── CLAUDE.md                 # + uvicorn command, nova/ + api/ notes, BOTH mock points documented
```

No build-system/packaging change: like `streamlit_app` today, `import nova` / `import api` rely on running from the repo root (root `conftest.py` handles tests). A pip-installable consumer would force a src/ layout later — deferred (§9).

---

## 4. Shared-core extraction

### 4.1 `nova/config.py` — constants

Moves from `streamlit_app.py` (current lines 15–64):

| New name (nova.config) | Old name (streamlit_app) | Value |
|---|---|---|
| `MODEL` | `_TRANSCRIBE_OPTS["model"]` | `"nova-3-medical"` |
| `LANGUAGES` | `_LANGUAGES` | 8 English variants (Nova-3 Medical is English-only) |
| `REDACT_GROUPS` | `_REDACT_GROUPS` | `pii`/`phi`/`pci`/`numbers` → display labels |
| `DEFAULT_LANGUAGE`, `DEFAULT_SMART_FORMAT`, `DEFAULT_DICTATION`, `DEFAULT_MEASUREMENTS`, `DEFAULT_DIARIZE` | same names | `"en"`, `True`, `False`, `False`, `False` |
| `MAX_KEYTERMS`, `MAX_UPLOADS`, `MAX_CONCURRENCY`, `MAX_FILE_SIZE` | same names | 100, 100, 5, 2 GiB |
| `AUDIO_EXTENSIONS` | `_AUDIO_EXTENSIONS` | `(".mp3", ".m4a", ".wav", ".flac", ".ogg")` |
| `has_audio_extension(url_or_name: str) -> bool` | inline in `_run` | `url_or_name.split("?")[0].lower().endswith(AUDIO_EXTENSIONS)` |

`streamlit_app.py` re-imports under the old names:

```python
from nova.config import (
    AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS,
    LANGUAGES as _LANGUAGES,
    REDACT_GROUPS as _REDACT_GROUPS,
    DEFAULT_LANGUAGE, DEFAULT_SMART_FORMAT, DEFAULT_DICTATION,
    DEFAULT_MEASUREMENTS, DEFAULT_DIARIZE,
    MAX_FILE_SIZE, MAX_KEYTERMS, MAX_UPLOADS, has_audio_extension,
)
```

**Ruff F401 check (verified against the file):** every re-imported name above is *referenced* in `streamlit_app.py`'s remaining UI code (widgets, `_run`, `_feature_opts`), so no unused-import violations fire. `MAX_CONCURRENCY` and `ThreadPoolExecutor` are dropped from `streamlit_app` entirely after Step 2 — no test reads `streamlit_app.MAX_CONCURRENCY` (verified: tests read only `MAX_UPLOADS` at test:521 and `MAX_RECORDING_SECONDS` at test:543, both reads, never patches).

**Deliberately staying in `streamlit_app.py`** (pure UI concerns, and one hard test constraint):
`MAX_RECORDING_SECONDS`, `MAX_PLAYBACK_BYTES` (test:229 does `patch.object(streamlit_app, "MAX_PLAYBACK_BYTES", 2)` and `_playback_source` reads that module global — it must remain), `_AUDIO_MIME`, `_AUDIO_TYPES`, `OUTPUT_HEIGHT`, `PLACEHOLDER`, `NO_TRANSCRIPT`, `_parse_urls` (newline-splitting is a textarea concern; the API takes a JSON array — judges across the panel agreed moving it is churn), `_feature_opts`, `_run`, `_playback_source`, `_escape_markdown`, `_display_audio/_display_transcript/_display_json`, `_output_panel`.

### 4.2 `nova/transcribe.py` — option builder + batch runner

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Callable

from deepgram import DeepgramClient   # ← the API tests' mock point

from nova.config import (
    MODEL, MAX_CONCURRENCY,
    DEFAULT_SMART_FORMAT, DEFAULT_DICTATION, DEFAULT_MEASUREMENTS, DEFAULT_DIARIZE,
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
    timeout_in_seconds: int | None = None,   # API-only; the UI never passes it
) -> dict[str, Any]: ...
```

`build_options` reproduces the opts dict from `streamlit_app.py` lines 97–113 with byte-identical semantics: `model=MODEL` + `smart_format` always sent; `diarize`/`measurements` only when `True`; `dictation=True` forces `punctuate=True`; `keyterm`/`language` only when truthy. The one extension is `timeout_in_seconds`: **verified** that deepgram-sdk 7.3.0's `RequestOptions` TypedDict carries `timeout_in_seconds` alongside `additional_query_parameters`, so redact and timeout merge into the **same** `request_options` dict:

```python
request_options: dict[str, Any] = {}
if redact:
    request_options["additional_query_parameters"] = {"redact": redact}   # SDK types redact as a single str
if timeout_in_seconds is not None:
    request_options["timeout_in_seconds"] = timeout_in_seconds
...
**({"request_options": request_options} if request_options else {}),
```

The UI path never passes `timeout_in_seconds`, so the existing exact-kwargs assertions — including `assert "request_options" not in kwargs` at test:181 and test:214 — stay byte-identical. The dictation→punctuate coupling and the redact escape hatch now live in exactly one place.

```python
@dataclass
class ItemResult:
    index: int
    label: str
    response: Any | None = None
    error: str | None = None     # str(exc) — no prefix; the UI adapter formats it


def transcribe_batch(
    api_key: str,
    items: list[tuple[str, dict[str, Any]]],
    method: str,                                   # "transcribe_file" | "transcribe_url"
    *,
    options: dict[str, Any],
    client_cls: type | None = None,                # None → resolve at CALL time (see below)
    as_completed_fn: Callable | None = None,       # None → resolve at CALL time
    max_concurrency: int = MAX_CONCURRENCY,
    gate: AbstractContextManager | None = None,    # optional process-global concurrency gate
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ItemResult]:
    if client_cls is None:
        client_cls = DeepgramClient        # module-global lookup at call time →
    if as_completed_fn is None:            #   patch("nova.transcribe.DeepgramClient") works
        as_completed_fn = as_completed
    ...
```

**Why `None`-defaults, not `client_cls: type = DeepgramClient`:** a def-time default binds the class object when the module loads, so a later `patch("nova.transcribe.DeepgramClient")` would never affect default-arg callers — the winning design's API-test plan was broken as written (flagged by the panel). Call-time resolution inside the body fixes it: the API calls `transcribe_batch(...)` with no seams, tests patch the module attribute, done.

The body is the ThreadPoolExecutor loop from `streamlit_app.py` lines 114–140 minus all `st.*` and session-state side effects: one `client_cls(api_key=api_key)` per batch, `transcribe = getattr(client.listen.v1.media, method)`, submit all items (each call wrapped in `with gate:` when a gate is provided — `gate=None` is a no-op so the UI path is bit-identical), iterate `as_completed_fn(futures)`, call `on_progress(done, total)` per completion (success or failure), capture per-item exceptions as `ItemResult(error=str(exc))`, sort by index, return **all** items in input order.

### 4.3 `nova/results.py` — response walkers

`_first_alternative` → `first_alternative`, `_transcript_text` → `transcript_text`, `_diarized_segments` → `diarized_segments`, moved **verbatim** (they contain no `st.*` calls and no patched globals). One new function for the API:

```python
def word_list(response: Any) -> list[dict[str, Any]] | None:
    """Flattened words from alternatives[0]: {text, start, end, confidence, speaker}.
    text uses punctuated_word falling back to word (same token rule as diarized_segments).
    speaker is Deepgram's raw value (0-based int or None). None when no usable results/words."""
```

`streamlit_app.py` aliases the moved three:

```python
from nova.results import (
    diarized_segments as _diarized_segments,
    first_alternative as _first_alternative,
    transcript_text as _transcript_text,
)
```

Tests call `streamlit_app._diarized_segments(response)` etc. directly and patch nothing inside them — aliasing is invisible.

### 4.4 The rewritten `streamlit_app._transcribe_batch` wrapper

Exact current signature preserved, so `_process_inputs`/`_process_urls` and every test are untouched. Sources are built **before** the run, matching the original ordering exactly (panel-flagged fix):

```python
def _transcribe_batch(api_key, items, method, keyterms=None, language=None,
                      smart_format=DEFAULT_SMART_FORMAT, dictation=DEFAULT_DICTATION,
                      measurements=DEFAULT_MEASUREMENTS, diarize=DEFAULT_DIARIZE, redact=None):
    opts = build_options(keyterms=keyterms, language=language, smart_format=smart_format,
                         dictation=dictation, measurements=measurements, diarize=diarize,
                         redact=redact)
    total = len(items)
    progress = st.progress(0.0, f"Transcribing 0/{total}...")
    sources = {
        i: _playback_source(kwargs.get("request", kwargs.get("url")))
        for i, (_, kwargs) in enumerate(items)
    }
    results = transcribe_batch(
        api_key, items, method, options=opts,
        client_cls=DeepgramClient,      # streamlit_app module global → patch target intact
        as_completed_fn=as_completed,   # ditto (test:317)
        on_progress=lambda done, t: progress.progress(done / t, f"Transcribing {done}/{t}..."),
    )
    progress.empty()
    for r in results:
        if r.error is not None:
            st.error(f"Transcription failed for {r.label}: {r.error}")
    ok = [r for r in results if r.error is None]
    # Always overwrite (even when empty) so a fully-failed run clears stale results.
    st.session_state["responses"] = [(r.label, r.response) for r in ok]
    st.session_state["audio_sources"] = [sources[r.index] for r in ok]
```

### 4.5 Test-survival audit (traced against every patch point in the suite)

| Patch point / assertion | Location | Why it survives |
|---|---|---|
| `patch("streamlit_app.DeepgramClient")` | tests/conftest.py:10 | Wrapper resolves the module global at call time and passes it as `client_cls`; core constructs the patched class → `assert_called_once_with(api_key="test-key")`, per-batch client reuse, call counts all hold |
| Exact-kwargs assertions (options, dictation→punctuate, redact via request_options, omissions) | TestProcessInputs/TestProcessUrls | `build_options` reproduces lines 97–113 byte-for-byte; UI never sends `timeout_in_seconds` so `request_options` stays absent when redact is empty (test:181, :214) |
| `patch("streamlit_app.as_completed", lambda fs: list(fs)[::-1])` | test:317 | Wrapper passes the patched reference as `as_completed_fn`; core iterates it over the futures dict → input-order test passes |
| `patch("streamlit_app.st")` | tests/conftest.py:44 | `st.progress`/`st.error`/session-state writes all stay in the wrapper → progress-not-spinner (test:339), error format `"Transcription failed for bad.wav: API error"` (test:251, :428 — `str(exc)` matches), partial-failure alignment, total-failure-clears-stale all pass |
| `patch.object(streamlit_app, "MAX_PLAYBACK_BYTES", 2)` | test:229 | `_playback_source` and the constant stay in `streamlit_app` |
| `streamlit_app.MAX_UPLOADS` / `MAX_RECORDING_SECONDS` reads | test:521, :543 | Re-imported / unmoved names satisfy attribute reads (nothing patches them) |
| Direct calls to `streamlit_app._diarized_segments` etc. | TestDiarizedTranscript | Aliased imports; functions moved verbatim, nothing patched inside |

One invisible behavior shift, stated honestly: per-item `st.error` messages render after the batch finishes rather than interleaved as failures occur. No test pins the timing (verified — tests assert call args/counts only). Restoring liveness later is an `on_error` callback, ~5 lines.

**API tests' mock point:** `patch("nova.transcribe.DeepgramClient")` — valid because the API calls `transcribe_batch` without seams and the default resolves at call time (§4.2). CLAUDE.md will record **both** mock points so future edits respect the boundary.

---

## 5. API contract

FastAPI app in `api/main.py`; OpenAPI docs at `/docs` (generated from `schemas.py` — synthetic examples only), served unauthenticated on loopback binds and disabled outright (`docs_url=None`/`openapi_url=None` at app creation, read from `API_HOST`) on non-loopback binds (§11.3). All `/v1` routes require Bearer auth; breaking changes mint `/v2`. Every request gets a server-generated id echoed as `X-Request-ID` and embedded in error bodies.

**Fail-fast spend guarantee (contract-level):** structural validation failures — unknown language or redact group, >100 keyterms, >100 items, zero items, bad URL scheme, malformed body — reject the **whole request** with 422/400 **before any Deepgram call**. Per-item isolation applies only after the batch starts.

### 5.1 Endpoints

#### `GET /healthz` — no auth
`200 {"status": "ok"}`. Liveness only; never calls Deepgram (no spend, no key-validity oracle).

#### `POST /v1/transcriptions/urls` — `application/json`
Mirrors `_process_urls`.

```json
{
  "urls": ["https://example.com/visit1.mp3", "https://example.com/visit2.wav"],
  "smart_format": true,
  "diarize": false,
  "dictation": false,
  "measurements": false,
  "keyterms": ["metformin", "aspirin"],
  "language": "en-US",
  "redact": ["pii"],
  "include_raw": false,
  "include_words": false
}
```

- `urls`: 1–100 items (`MAX_UPLOADS`). Validated as plain strings that must start with `http://` or `https://` (same rule as `_parse_urls`) — **not** Pydantic `HttpUrl`, which normalizes URLs and would change the bytes sent upstream (e.g. signed URLs). URLs are passed to Deepgram verbatim.
- A URL whose path (query string stripped, via `nova.config.has_audio_extension`) lacks a recognized audio extension is still transcribed but listed in the top-level `warnings` array — mirrors the UI's `st.warning`-then-proceed behavior.

#### `POST /v1/transcriptions/files` — `multipart/form-data`
Mirrors `_process_inputs`. Parts: repeated `files` (1–100; filename becomes the item `name`); option fields as discrete text parts with identical names and defaults (`smart_format`, `diarize`, `dictation`, `measurements`, `language`, `include_raw`, `include_words`, repeated `keyterms`, repeated `redact`) — parsed into the **same** `TranscriptionOptions` Pydantic model as the JSON endpoint, so the option surface cannot fork.

```bash
curl -X POST localhost:8000/v1/transcriptions/files \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@visit1.mp3" -F "files=@visit2.wav" \
  -F diarize=true -F keyterms=metformin -F keyterms=aspirin -F redact=pii
```

Size enforcement (uvicorn does **not** enforce body size, so the app must):
- **Per file > `MAX_FILE_SIZE` (2 GiB):** per-item `file_too_large` error, skip-and-continue, no Deepgram call — the API analog of `_run`'s "Skipped (exceeds 2 GB)".
- **Whole request > `MAX_REQUEST_BYTES` (default `MAX_FILE_SIZE` + 16 MiB, i.e. 2 GiB plus multipart-framing headroom; configurable):** real `413` via Content-Length precheck plus a capped streamed read during parsing (defense against absent/false Content-Length). The headroom exists because multipart boundaries and option fields add bytes — without it a legitimate maximal single file would 413. The default pins the invariant *one request carries at most one maximal file*, and since file bytes are materialized for the SDK, the budget doubles as a per-request RAM bound (§11.1). The endpoint description states this cap explicitly and names **URL batches as the sanctioned bulk path** — multi-gigabyte multipart batches are intentionally not supported in one request.

#### File/URL mixing
Structurally impossible — two endpoints. This replaces the UI's silent Upload > Record > URL priority: an API should fail loudly, not guess.

### 5.2 Feature parameters (both endpoints; validated in `api/schemas.py` against `nova.config` — single source of truth)

| Param | Default | Behavior |
|---|---|---|
| `smart_format` | `true` | Always forwarded (on or off), like the core |
| `diarize` | `false` | Forwarded only when true |
| `dictation` | `false` | Forwarded only when true; server adds `punctuate=true` — coupling lives in `build_options`, documented in OpenAPI, clients cannot decouple |
| `measurements` | `false` | Forwarded only when true |
| `keyterms` | `[]` | Max 100 (`MAX_KEYTERMS`); sent as `keyterm` only when non-empty |
| `language` | `"en"` | Closed enum = keys of `nova.config.LANGUAGES` (en, en-US, en-AU, en-CA, en-GB, en-IE, en-IN, en-NZ); 422 otherwise. Nova-3 Medical is English-only |
| `redact` | `[]` | Each ∈ {pii, phi, pci, numbers}; sent as repeated `redact` query params via `request_options={"additional_query_parameters": {"redact": [...]}}` (the 7.3.0 `redact: Optional[str]` typing workaround — verified against the installed SDK). OpenAPI description carries the UI's caveat: `pii` de-identifies; `phi` strips clinical content itself |
| `include_raw` | `false` | Each result carries the full Deepgram `model_dump()`. OpenAPI description warns: raw consumers re-couple themselves to Deepgram's schema and this project's `deepgram-sdk==7.3.0` pin — at your own risk |
| `include_words` | `false` | Each result carries the flattened `words` array (timings/confidence without forcing raw). Default-off confirmed at review: flipping off→on later is additive, on→off would break clients (§11.5) |
| `model` | — | **Not a parameter.** Pinned server-side to `nova-3-medical`. Rejects the Deepgram starter's optional `model` field: this tool's identity is the medical model, the validated option surface is specific to it, and a client-chosen model on a brokered paid key is an uncontrolled spend knob |

Upstream per-call timeout is server configuration (`DEEPGRAM_TIMEOUT_SECONDS`, default 600 — 2 GiB files are slow), wired through `build_options(timeout_in_seconds=...)`, not a request parameter. Confirmed at review: no per-request override — the timeout's primary job is reclaiming global-semaphore slots (§6.3) from hung upstream calls, and per-request exposure has no consumer; slow-link pain is an env-var bump, not new contract surface (§11.2).

### 5.3 Response shape

`200` whenever the batch ran, **even if every item failed** — per-item isolation mirrors `_transcribe_batch`; clients must check per-item `status`. (207 Multi-Status rejected: WebDAV-flavored, poorly handled by HTTP clients.)

```json
{
  "model": "nova-3-medical",
  "status": "partially_completed",
  "summary": {"total": 2, "succeeded": 1, "failed": 1},
  "warnings": [],
  "results": [
    {
      "index": 0,
      "name": "visit1.mp3",
      "status": "ok",
      "transcript": "Patient reports chest pain.",
      "segments": [{"speaker": 0, "text": "Patient reports chest pain."}],
      "words": null,
      "request_id": "9d4c1afe-…",
      "duration": 12.4,
      "raw": null
    },
    {
      "index": 1,
      "name": "visit2.wav",
      "status": "error",
      "error": {"type": "upstream_error", "code": "deepgram_request_failed",
                "message": "ApiError: status_code 400 …"}
    }
  ]
}
```

- `status`: `completed` (all ok) | `partially_completed` | `failed` (**defined as: every item failed** — the unambiguous total-failure signal, same vocabulary the future async shape will use, §9).
- `transcript`: `nova.results.transcript_text(response)`; `null` for a results-less response (the `NO_TRANSCRIPT` case — e.g. a `ListenV1AcceptedResponse`).
- `segments`: `nova.results.diarized_segments(response)`; `null` when diarization is off or words carry no integer speaker labels. **Speakers are Deepgram's native 0-based integers — everywhere in the API (`segments`, `words`, and `raw` all agree). Only the Streamlit renderer adds +1 for display ("Speaker 1").** Stated prominently in the OpenAPI field description and pinned by a test. (This deliberately diverges from the UI's display convention to avoid an off-by-one trap between flattened and raw payloads.)
- `words` (when `include_words=true`): `nova.results.word_list(response)` — `[{"text", "start", "end", "confidence", "speaker"}]`, `punctuated_word` falling back to `word`.
- `request_id`/`duration`: getattr-guarded from `response.metadata` (same defensive style as `first_alternative`).
- `raw` (when `include_raw=true`): `response.model_dump()` — what the UI's JSON tab shows today.
- Per-item `error.message` is the upstream exception text **only** — the `"Transcription failed for {label}:"` prefix belongs to the Streamlit adapter's `st.error`, not an API payload where `name` is already a sibling field.

### 5.4 Error envelope

Adopted from the Deepgram starter and extended: the starter's numeric-redundancy is dropped but the machine-readable `code` is **kept** (panel-flagged: HTTP status + `type` alone cannot distinguish `invalid_language` from `too_many_keyterms` without parsing prose), and `request_id` is added for correlation. Enforced via handlers for `RequestValidationError`, `StarletteHTTPException`, and a catch-all — even FastAPI's native 422s wear the envelope.

```json
{"error": {"type": "validation_error", "code": "invalid_redact_group",
           "message": "redact[0]: must be one of pii, phi, pci, numbers",
           "request_id": "req_01HZX…"}}
```

| Status | `type` | Example `code`s |
|---|---|---|
| 400 | `invalid_request` | `no_files` (zero `files` parts) |
| 401 | `unauthorized` | `missing_token`, `invalid_token` (+ `WWW-Authenticate: Bearer`) |
| 413 | `payload_too_large` | `request_body_too_large` (enforced in-app, §5.1 — not "reserved for a proxy") |
| 422 | `validation_error` | `invalid_language`, `invalid_redact_group`, `too_many_keyterms`, `too_many_urls`, `too_many_files`, `invalid_url_scheme`, `malformed_body` |
| 503 | `not_configured` | `missing_deepgram_key`, `missing_auth_tokens` |
| 500 | `internal_error` | `unexpected` (message scrubbed — never stack traces or content) |

Per-item failures inside `results[]` reuse the inner `{type, code, message}` shape with types `upstream_error` (`deepgram_request_failed`), `upstream_timeout` (`deepgram_timeout` — surfaced when the per-call `timeout_in_seconds` fires), or `file_too_large`.

### 5.5 Batch semantics

- 1–100 items per request (`MAX_UPLOADS`, same as the UI), enforced fail-fast.
- Items run concurrently through `nova.transcribe.transcribe_batch`'s ThreadPoolExecutor, ≤5 in flight per request (`MAX_CONCURRENCY`) **and** ≤5 in flight process-wide across all simultaneous requests (the global gate, §6.3) — N concurrent requests cannot multiply to 5×N upstream calls.
- `results` always preserves input order regardless of completion order; one item's failure never aborts the batch.
- The request is **synchronous** — the connection stays open until the slowest item finishes. The OpenAPI description and CLAUDE.md carry explicit client guidance: *set generous read timeouts (minutes per GB of audio; a full 100-item batch can take tens of minutes), and prefer several smaller batches if your HTTP client or any intermediary enforces shorter limits.* The 202+poll shape is pre-specced for when this stops being acceptable (§9).
- Endpoints offload the blocking core via `starlette.concurrency.run_in_threadpool` so the event loop stays responsive.

---

## 6. Auth & operational concerns

### 6.1 Threat model

A single developer's local tool whose API brokers (a) a paid Deepgram key and (b) PHI-bearing audio/transcripts in transit. Risks worth paying for: key theft / spend-draining if the port leaks beyond localhost, and PHI exposure via logs, temp files, or unencrypted transport. Not worth paying for yet: multi-tenant identity, sessions, request-rate quotas.

### 6.2 Deepgram key & auth

- **Deepgram key is server-side only**: `DEEPGRAM_API_KEY` from the same `.env` (python-dotenv) the Streamlit app uses; clients never send it per-request (per-request key passthrough would spread the paid key into every batch job's config and risk it landing in logs). If unset, transcription endpoints return `503 not_configured` — no API equivalent of the UI's inline key prompt.
- **Auth: static bearer tokens, always required** — even on loopback (localhost is reachable from any browser tab via DNS rebinding; one `.env` line is cheap and PHI argues for it). `API_AUTH_TOKENS` env: **comma-separated**, so the future batch job and an EHR-adjacent service each get their own revocable token instead of rotate-for-everyone. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. `api/auth.py::require_token` is a router dependency checking `Authorization: Bearer <token>` against each configured token with `secrets.compare_digest`; 401 on mismatch; 503 `not_configured` if no tokens are set. `/healthz` — plus, on loopback binds only, `/docs`/`/openapi.json` (§5) — are the only unauthenticated routes.
- **Fail-closed startup**: the lifespan hook reads `API_HOST` from `api/settings.py` (the documented run command sources the same setting) and **refuses to start** when the bind host is non-loopback and `API_AUTH_TOKENS` is unset. Caveat documented: uvicorn's actual `--host` flag is what binds, so the run command and the setting must agree.
- **Rejected from the starter — JWT session flow** (`GET /api/session` minting HS256 tokens): when the token mint itself is unauthenticated, anyone who can reach the server can mint a token, so it provides zero access control for a private deployment. It exists to bucket anonymous users of a public demo for rate limiting — the opposite of this consumer profile.

### 6.3 Spend control & rate limiting

- **The real spend governor is concurrency, not requests/minute.** A process-global `threading.BoundedSemaphore(GLOBAL_MAX_CONCURRENCY=5)` in `api/main.py` is passed as `gate=` to every `transcribe_batch` call, so each worker thread acquires it around its Deepgram call. (It must be a `threading` semaphore — the calls execute in executor worker threads, not on the event loop.) ~10 lines; closes the 5×N hole now rather than at some future trigger. The Streamlit path passes no gate and is unchanged.
- **No request-rate limiting now**: the consumers are the owner's own jobs on localhost; the starter's Caddy per-IP limits defend a public demo, and behind loopback/Tailscale all consumers share an IP anyway. Existing cost levers: the global semaphore, `MAX_CONCURRENCY=5` per request, `MAX_UPLOADS=100` per batch, fail-fast validation before any spend. Upgrade path in §9.

### 6.4 PHI logging discipline (non-negotiable)

- **NEVER log:** audio bytes, transcripts, segments, raw Deepgram responses, request/response bodies; **keyterms** (they routinely encode drug and patient names — log the *count*); **filenames** (often `lastname_dob.mp3` — log per-item *index and byte size*); **full URLs** (may embed identifiers or signed tokens — log *scheme+host* or a hash, query strings always stripped).
- **DO log:** timestamp, route, status, latency, item counts, boolean feature flags, byte sizes, the server `X-Request-ID`, and Deepgram's `request_id` (the safe correlation handle).
- **Pin the `httpx` and `deepgram` loggers to `WARNING`**: the SDK sends `keyterm` and `redact` as upstream *query parameters*, so DEBUG-level URL logging would leak them.
- uvicorn access logs disabled in favor of a sanitized log line (or restricted to method+path); the 500 handler scrubs messages; per-item upstream error text goes back to the authed caller (who sent the content) but logs keep only the exception class and status.
- **Documented caveat:** Starlette spools multipart parts >1 MB to temp files, so PHI transits `/tmp` during upload parsing — acceptable on a FileVault-encrypted Mac; mount a tmpfs if this is ever containerized.

### 6.5 Statelessness, transport, deployment

- The API is stateless: audio and transcripts live only in request-scoped memory — never on disk (beyond the Starlette spool above), never in a server-side session. The Streamlit `session_state` caching of responses/audio remains a UI-only behavior.
- Run: `uv run uvicorn api.main:app --host 127.0.0.1 --port 8000` (added to CLAUDE.md); Streamlit runs side-by-side from the same venv and `.env`. Bind loopback by default; no container — nothing requires one.
- **Any non-loopback exposure requires TLS, full stop** (PHI in transit). Lowest-friction path for personal infra: Tailscale Serve (WireGuard + automatic certs + device identity); Caddy if a public hostname is ever truly needed (adopted from the starter for TLS termination, not for rate limiting).
- Redaction caveat carried from `_REDACT_GROUPS`: `pii` de-identifies; `phi` strips clinical content itself. A **Deepgram BAA** is the operator's responsibility before sending real PHI regardless of front-end — the API adds a second door to the same upstream, not a new boundary.

---

## 7. Streamlit-as-client migration

**Phase 0 (this design, ships now): Streamlit does NOT call the API.** Both front-ends consume `nova/` in-process — Streamlit via the `_transcribe_batch` wrapper, FastAPI via `transcribe_batch` directly. Rationale:

1. The entire suite patches `streamlit_app.DeepgramClient` and `streamlit_app.as_completed` — HTTP indirection would invalidate dozens of tests for zero functional gain.
2. It would re-upload up to 100×2 GiB over loopback multipart, doubling peak memory and adding body-size failure modes.
3. The live per-item progress bar (`on_progress` per completed future) cannot be reproduced across one blocking HTTP request.
4. The UI's inline Deepgram-key prompt stops making sense (the API never accepts a client-supplied Deepgram key).

Behavioral parity is guaranteed structurally — one `build_options`, one `transcribe_batch`, one set of response walkers — which is stronger than contract discipline.

**Phase 1 (opt-in; trigger: the API gains server-side value — transcript persistence, a job queue for long batches, or centralized PHI-access audit — or the UI host must not hold the Deepgram key):** add `nova/client.py` with `transcribe_files(base_url, token, files, **opts)` / `transcribe_urls(...)` over httpx, behind a `NOVA_API_URL` env switch in `_process_inputs`/`_process_urls`. When unset (the default), the in-process path runs exactly as today, so the existing suite keeps covering the supported configuration; new client tests mock httpx. **Known rework, recorded now so it isn't rediscovered:** the renderers consume SDK Pydantic objects — `_display_json` calls `response.model_dump_json()` and `_display_transcript` walks attributes — so Phase 1 needs either a thin `RawResponse` adapter reconstructing that interface from the `include_raw` payload, or renderers reworked to consume flattened dicts. Progress also degrades to coarse per-request unless the client chunks batches — explicitly a Phase 1 decision.

**Phase 2 (only if the tool ever becomes multi-user):** server-side results/sessions, per-consumer tokens with audit, job IDs with polling (Deepgram's callback mode — the `ListenV1AcceptedResponse` path the walkers already guard — is the natural async mechanism). Out of scope by design.

---

## 8. Ordered migration steps

Gate after **every** step: `uv run pytest && uv run ruff check . && uv run ty check .` — all green, zero edits to `tests/test_streamlit_app.py`, Streamlit UI verified working. Each step is independently shippable.

1. **Extract constants + option builder.** Create `nova/__init__.py`, `nova/config.py` (table in §4.1, including `has_audio_extension`), and `nova/transcribe.py` with `build_options()` reproducing `streamlit_app.py:97–113` exactly (plus the inert `timeout_in_seconds` parameter). Rewire `streamlit_app` to import from `nova.config` under the old names (§4.1 import block; `_run` switches its inline extension check to `has_audio_extension`) and have `_transcribe_batch` call `build_options()` for its opts. Drop nothing else yet. Gate.
2. **Extract the batch runner.** Add `ItemResult` and `transcribe_batch(...)` (§4.2 — `None`-default seams resolved at call time, optional `gate`, `on_progress`) to `nova/transcribe.py`. Shrink `streamlit_app._transcribe_batch` to the wrapper in §4.4 (exact signature; sources built before the run; module-global `DeepgramClient`/`as_completed` passed as seams; `st.progress` via `on_progress`; `st.error` per failure; unconditional session-state writes). Remove the now-unused `ThreadPoolExecutor` import and `MAX_CONCURRENCY` from `streamlit_app`. Gate — watch `test_preserves_input_order_under_reversed_completion`, the partial/total-failure tests, and `test_uses_progress_bar_not_spinner`.
3. **Extract result parsing.** Move the three walkers verbatim to `nova/results.py`; add `word_list()`; alias the old underscore names in `streamlit_app` (§4.3). `_display_*`/`_escape_markdown` stay put. Gate.
4. **Add API dependencies.** `uv add fastapi 'uvicorn[standard]' python-multipart && uv add --dev httpx` (httpx 0.28.1 is already in the venv as a deepgram transitive; pin it direct for TestClient). Single dependency list, no extras — one venv for one developer. No code changes. Gate.
5. **Build the API.** Add `api/settings.py`, `api/auth.py` (multi-token `require_token`), `api/schemas.py` (`TranscriptionOptions` validated against `nova.config`; `UrlBatchRequest` with 1–100 verbatim-string URLs; `ItemOut`/`BatchSummary`/`BatchResponse`/`ErrorEnvelope`), `api/main.py` (`/healthz`; both transcription endpoints calling `build_options` + `transcribe_batch(gate=GLOBAL_SEMAPHORE)` via `run_in_threadpool`; ItemResult→ItemOut mapping with `transcript_text`/`diarized_segments`/`word_list`/optional `model_dump()`; exception handlers emitting the envelope; X-Request-ID middleware; Content-Length precheck + capped read for 413; per-item `file_too_large`; URL extension warnings; lifespan fail-closed check; logging discipline incl. httpx/deepgram logger pinning). Add `tests/test_api.py` using `TestClient` + `patch("nova.transcribe.DeepgramClient")`, reusing `tests/helpers.py::mock_word`, covering: auth 401/503; option pass-through for **every** toggle (assert mocked `transcribe_file`/`transcribe_url` kwargs incl. dictation→punctuate and redact `request_options`, with `timeout_in_seconds` merged into the same dict); batch order + partial failure + all-failed `status: "failed"`; **0-based segment speakers** (the pinning test from §5.3); `include_raw`/`include_words`; fail-fast 422s; files/urls limits; 413; error envelope shape with `code` and `request_id`. Gate + smoke-test with uvicorn + curl.
6. **Docs and ops.** Update CLAUDE.md: run command, `nova/` + `api/` architecture notes, **both mock points** (`streamlit_app.DeepgramClient` for UI tests, `nova.transcribe.DeepgramClient` for API tests), client timeout guidance for sync batches, the PHI logging policy. Generate `API_AUTH_TOKENS` into `.env`. Manual UI re-verification: upload, record, URL, all feature toggles, diarized rendering. Gate.
7. **(DEFERRED — do not build; see §9.)** Phase 1 HTTP client, async batches, rate limiting, containerization.

---

## 9. Deferred work with explicit triggers

| Item | Trigger | Pre-committed shape |
|---|---|---|
| **Async batches: `POST /v1/batches` → 202 + `GET /v1/batches/{id}`** | Any batch routinely exceeding a few minutes, or client/proxy timeouts on the sync endpoints (review 2026-06-12: deliberately kept deferred for the first build — §11.4) | Status taxonomy `running` / `completed` / `partially_completed` / `failed` (failed == all items failed — same definition as the sync `status`); `progress: {done, total}` fed by `on_progress`; per-item objects keep the **identical** `{index, name, status, …}` fields as §5.3 so sync and async contracts coexist; `DELETE /v1/batches/{id}` + TTL for PHI hygiene; post-restart polls get a distinct `batch_unknown_or_expired` code. Durable (SQLite) store only if restart-surviving jobs are actually needed; Deepgram's own `callback=` mode (the `ListenV1AcceptedResponse` path `nova/results.py` already guards) is the alternative mechanism to evaluate first |
| **Streamlit HTTP-client mode (`nova/client.py` + `NOVA_API_URL`)** | API gains persistence/queueing/audit value, or the UI host must not hold the Deepgram key | §7 Phase 1, including the `RawResponse` adapter for `_display_json` |
| **Request-rate limiting** | Non-loopback exposure or a second consumer | slowapi or reverse-proxy limits keyed on bearer token (not IP); the global semaphore already owns spend |
| **Per-consumer audit logging + TLS** | Mandatory together at any non-loopback deployment | Tailscale Serve first; Caddy for a public hostname |
| **`on_error` callback in `transcribe_batch`** | If interleaved per-item error display is missed in the UI | ~5-line addition |
| **Packaging (`src/` layout, build backend)** | A pip-installable consumer of `nova/` appears | Until then, repo-root imports match how `streamlit_app`/`conftest.py` work today |
| **Containerization** | Deployment beyond the dev Mac | tmpfs mount for the Starlette multipart spool |

---

## 10. Risks

1. **Synchronous batch endpoints**: a large batch holds one HTTP connection open for the whole run — client timeouts, no resumability, no mid-run progress (the UI keeps its live progress bar only because it stays in-process). Mitigated by the documented client-timeout guidance and the per-request/total size caps; the rework (§9 async shape) is pre-specced so adding it is additive, not breaking.
2. **Whole-file memory**: both front-ends materialize full file bytes (parity with today's `f.getvalue()`; the SDK takes `request=<bytes>`). The 413 budget bounds a single request, and the global semaphore bounds concurrent count — but not total resident bytes across concurrent requests. First fix if real workloads grow: lower `MAX_REQUEST_BYTES` or move bulk traffic to URL batches.
3. **Duplicate symbol surface**: `streamlit_app` re-imports nova names purely to preserve test patch points. Harmless but permanent until a later cleanup retargets tests at `nova.*` — the accepted cost of "existing suite unchanged".
4. **Sync core**: blocking SDK calls in worker threads; many queued batch requests park threads waiting on the global gate. Fine for one user; `AsyncDeepgramClient` or a real queue is the scaling path, isolated behind `transcribe_batch`'s seam.
5. **Static bearer tokens**: no expiry or scoping; revocation = edit `.env` and restart (per-consumer tokens make that per-consumer, not global). No PHI-access audit trail yet — becomes mandatory together with TLS at non-loopback exposure.
6. **Flattened-contract ownership**: the API owns the flattened shape's evolution; `include_raw` consumers re-couple to Deepgram's schema and the 7.3.0 pin (documented at-your-own-risk).
7. **Minor UI behavior shift**: per-item `st.error`s render after the batch instead of interleaved (no test pins the timing; `on_error` callback restores it, §9).
8. **Error-message passthrough**: per-item `upstream_error` messages embed `str(exc)` from the SDK; if Deepgram ever echoes request content in error bodies, that text reaches authed API clients (same trust domain today; worth a scrub when third parties consume the API). Logs are stricter (class + status only).
9. **Speaker-convention divergence from the UI**: the API's 0-based speakers are internally consistent (segments/words/raw all agree) but differ from the UI's displayed "Speaker 1". Documented in OpenAPI and pinned by a test; the alternative (1-based segments next to 0-based raw) was the worse trap.
10. **Bind-host check is best-effort**: the lifespan check reads `API_HOST` from settings; an operator passing a different `--host` directly to uvicorn bypasses it. Documented; the always-required token is the real backstop.

---

## 11. Review decisions (resolved 2026-06-12)

The five questions this design was reviewed under, and their resolutions — folded into the sections referenced:

1. **`MAX_REQUEST_BYTES` defaults to 2 GiB (+16 MiB framing headroom), not 4 GiB.** The multipart budget is effectively a per-request RAM budget — file bytes are materialized for the SDK — and `MAX_FILE_SIZE` + headroom pins the clean invariant of one maximal file per request. Multi-file bulk goes to URL batches, now enforceably rather than advisorily. (§5.1)
2. **`DEEPGRAM_TIMEOUT_SECONDS` stays 600 s, server-side only — no per-request override.** The timeout's real job is reclaiming global-semaphore slots from hung upstream calls; widening the API contract has no consumer asking for it. (§5.2, §6.3)
3. **`/docs` stays unauthenticated on loopback; disabled on non-loopback binds.** The OpenAPI page exposes shape, not data or keys — and bearer-authing it would lock browsers out of the main development convenience. Non-loopback exposure (already mandating TLS + audit) drops the docs routes at app creation. (§5, §6.2)
4. **Async `/v1/batches` stays deferred.** A job store is the one thing that would break statelessness: parked results mean PHI held server-side with TTLs, deletion endpoints, and restart semantics — a hygiene cost not worth paying speculatively. The owner's own scripts control client timeouts, splitting batches is a trivial workaround, and the pre-specced §9 shape keeps the later addition additive, not breaking. Revisit with evidence after the first real batch job. (§9)
5. **`include_words` stays default-off.** Words arrays run 10–50× transcript size; defaults serve the known consumer (batch jobs wanting transcripts), and the asymmetry seals it — off→on later is additive, on→off would break clients relying on words without passing the flag. (§5.2, §5.3)
6. **Remote-access path preference** — Tailscale Serve vs Caddy, worth deciding before any non-loopback need arises so the TLS story is one decision, not an incident response.
7. **Deepgram BAA status** — confirm whether a BAA is in place before any real patient audio flows through either front-end (independent of this design, but it gates real PHI).