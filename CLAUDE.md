# CLAUDE.md

## Project Overview

Transcribe medical audio with the Deepgram Nova-3 Medical model.

A Streamlit-free shared core (`nova/`) is consumed **in-process** by two front-ends — the Streamlit UI (`streamlit_app.py`) and a FastAPI service (`api/`). Both build options, run batches, and parse responses through the same core code, so the two front-ends cannot drift.

## Commands

```bash
uv sync                                                          # Install dependencies
uv run streamlit run streamlit_app.py                           # Run the Streamlit UI
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-access-log  # Run the API
uv run ruff check .                                              # Lint
uv run ruff format .                                            # Format
uv run ty check .                                               # Type check
uv run pytest                                                    # Test
```

## Architecture

### Shared core — `nova/` (imports neither streamlit nor fastapi)

- **`config.py`** — the single source of truth for constants: `MODEL` (`nova-3-medical`), `LANGUAGES` (8 English variants — Nova-3 Medical is English-only), `REDACT_GROUPS` ordered **PII-first** (`pii`/`phi`/`pci`/`numbers`; PHI labeled to flag that it strips clinical content), `DEFAULT_LANGUAGE`/`DEFAULT_SMART_FORMAT`/`DEFAULT_DICTATION`/`DEFAULT_MEASUREMENTS`/`DEFAULT_DIARIZE`, `MAX_KEYTERMS`/`MAX_UPLOADS`/`MAX_CONCURRENCY`/`MAX_FILE_SIZE`, `AUDIO_EXTENSIONS`, and `has_audio_extension()`.
- **`transcribe.py`**:
  - `build_options(*, keyterms=None, language=None, smart_format, dictation, measurements, diarize, redact=None, timeout_in_seconds=None)` — the kwargs dict for the Deepgram call. `model` + `smart_format` are **always** sent; off-by-default features are sent only when enabled — `diarize`/`measurements`, and `dictation` (which also forces `punctuate=True`) — plus `keyterm`/`language` only when truthy. `redact` (typed as a single `str` by the SDK) goes through `request_options["additional_query_parameters"]={"redact":[...]}`; `timeout_in_seconds` (API-only — the UI never passes it) merges into the **same** `request_options` dict, which is omitted entirely when both are unset.
  - `ItemResult` dataclass `{index, label, response, error}` — `error` is `str(exc)` with no prefix; the calling adapter owns presentation.
  - `transcribe_batch(api_key, items, method, *, options, client_cls=None, as_completed_fn=None, max_concurrency=MAX_CONCURRENCY, gate=None, on_progress=None)` — one shared client per batch via `ThreadPoolExecutor`; merges `options` onto each item; captures per-item exceptions (one failure never aborts the batch); sorts results back into input order. **`client_cls`/`as_completed_fn` default to `None` and resolve to this module's globals at *call time*** (not def-time) so API tests can `patch("nova.transcribe.DeepgramClient")` while in-process callers inject their own globals as seams. Optional `gate` is a process-wide concurrency context manager (no-op when `None`); `on_progress(done, total)` fires once per completion.
- **`results.py`** — getattr-guarded response walkers (no `st.*`): `first_alternative`, `transcript_text` (→ `.transcript` or `None`), `diarized_segments` (groups `alternatives[0].words` into consecutive `(speaker, text)` runs, gated on an integer `words[0].speaker`), and `word_list` (flattened `{text, start, end, confidence, speaker}`; `text` uses `punctuated_word` falling back to `word`). **Speakers are Deepgram's native 0-based ints throughout the core and the API; the `+1` display offset lives only in the Streamlit renderer.**

### Streamlit UI — `streamlit_app.py`

Re-imports the core constants under their old underscore names (`_LANGUAGES`, `_REDACT_GROUPS`, `_AUDIO_EXTENSIONS`, the `DEFAULT_*`/`MAX_*` names, `has_audio_extension`, `build_options`, `transcribe_batch`) and aliases the walkers (`from nova.results import transcript_text as _transcript_text, diarized_segments as _diarized_segments`) — preserving every existing test patch point. (`first_alternative` is no longer referenced in the UI, so it is not re-imported.)

1. Loads `DEEPGRAM_API_KEY` from `.env` via python-dotenv; prompts inline if missing.
2. `_transcribe_batch(api_key, items, method, keyterms=None, language=None, smart_format=DEFAULT_SMART_FORMAT, dictation=DEFAULT_DICTATION, measurements=DEFAULT_MEASUREMENTS, diarize=DEFAULT_DIARIZE, redact=None)` — a thin UI adapter over `nova.transcribe.transcribe_batch`: it builds the playback `sources` up front, drives `st.progress` via an `on_progress` callback, renders one `st.error` per failed item, and **unconditionally** writes `st.session_state["responses"]` and the parallel `["audio_sources"]` (so a fully-failed run clears stale results). It passes the module-global `DeepgramClient`/`as_completed` as seams. `_playback_source` keeps URLs/small audio but stores `None` for upload bytes over `MAX_PLAYBACK_BYTES` (25 MB); `None` sources render no player. (Note: per-item `st.error`s now render after the batch finishes rather than interleaved — no test pins the timing.)
3. `_process_inputs` / `_process_urls` — wrap `_transcribe_batch` for uploads / remote URLs.
4. `_feature_opts()` — reads the Features-tab control values from `st.session_state` (by widget `key`, falling back to the `DEFAULT_*` constants).
5. `_run(api_key, uploaded_files, recording, url_text)` — the Run handler: `st.info`s when more than one input is populated, then validates and transcribes the highest-priority input, **Upload → Record → URL** (file count/size; recording duration via a guarded `wave.open`; URL protocol/`has_audio_extension`), via `_process_inputs`/`_process_urls` with `**_feature_opts()`.
6. Renderers: `_display_transcript` (with diarization, one Markdown-escaped `**Speaker N:**` line per run, **1-based** display; otherwise the flat escaped transcript, or `st.caption(NO_TRANSCRIPT)`), `_display_json` (`st.json(response.model_dump_json())`), `_output_panel` (pinned players + fixed-height container or placeholder), `_display_audio` (MIME from extension via `_AUDIO_MIME`, default wav; URL passed through), `_escape_markdown`. No metrics, highlighting, expanders, or downloads.
7. Layout: audio input tabs (Upload ≤100 files/2 GB each; Record `st.audio_input` ≤10 min; URL HTTP/HTTPS ≤100) full-width above; left **Features** tab (Language, Smart Format, Keyterm Prompting, Diarize, Dictation, Measurements, Redact) + a full-width **Run** button; right **Transcript**/**JSON** tabs via `_output_panel`.

### FastAPI service — `api/`

Consumes `nova/` in-process; holds **no** transcription logic of its own, so it cannot drift from the UI.

- **`settings.py`** — env config read fresh per call (testable): `DEEPGRAM_API_KEY`, `API_AUTH_TOKENS` (comma-separated), `API_HOST`, `MAX_REQUEST_BYTES` (default `MAX_FILE_SIZE` + 16 MiB), `DEEPGRAM_TIMEOUT_SECONDS` (600), `GLOBAL_MAX_CONCURRENCY` (5). `is_loopback()` gates the docs routes and the fail-closed startup check.
- **`auth.py`** — `require_token` dependency: a bearer token is **always required** (even on loopback). Constant-time, non-short-circuiting comparison against each configured token; **503** `missing_auth_tokens` when none are configured, **401** `missing_token`/`invalid_token` (+ `WWW-Authenticate: Bearer`) otherwise.
- **`schemas.py`** — `TranscriptionOptions` (extra fields forbidden, so a client-supplied `model` is rejected — the model is pinned server-side), validated against `nova.config`; domain-rule failures raise `PydanticCustomError` whose `type` **is** the machine-readable envelope `code`. Also `UrlBatchRequest`, the response models (`ItemOut`/`Segment`/`ItemError`/`BatchSummary`/`BatchResponse`), `ErrorEnvelope`/`ErrorDetail`, and the `ApiError` exception.
- **`main.py`**:
  - `GET /healthz` — no auth, never calls Deepgram.
  - `POST /v1/transcriptions/urls` (JSON; URLs are verbatim strings, 1–100) and `POST /v1/transcriptions/files` (multipart; 1–100 `files` parts + option form fields parsed into the same `TranscriptionOptions`). Both require bearer auth, **validate fail-fast before any Deepgram call**, then run `transcribe_batch` off the event loop via `run_in_threadpool`, gated by a process-global `BoundedSemaphore` so N concurrent requests can't multiply into 5×N upstream calls.
  - Response `{model, status, summary, warnings, results[]}` — **200 even when every item failed**; `status` is `completed` / `partially_completed` / `failed` (== every item failed). Per-item `{index, name, status, transcript, segments, words, request_id, duration, raw, error}` with **0-based speakers**; `include_words`/`include_raw` are opt-in (default off). Per-item failures carry `{type, code, message}` (`upstream_error`/`upstream_timeout`/`file_too_large`).
  - Error envelope `{error: {type, code, message, request_id}}` via handlers for `ApiError`, `RequestValidationError`, `StarletteHTTPException`, and a scrubbed catch-all 500.
  - X-Request-ID middleware (server-generated, echoed back) + body-size guard: a Content-Length **precheck plus a capped streamed read during multipart parsing** (defense against an absent/false Content-Length) → **413** `request_body_too_large`. Per-file `> MAX_FILE_SIZE` → per-item `file_too_large` (skip-and-continue). URLs lacking a recognized audio extension are transcribed but listed in `warnings`. Fail-closed startup refuses a non-loopback bind without `API_AUTH_TOKENS`; `/docs`+`/openapi.json` are disabled off-loopback.

## Configuration

Env vars live in `.env` (gitignored; see `.env.example`): `DEEPGRAM_API_KEY` (both front-ends, server-side only), `API_AUTH_TOKENS` (API only; generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`), and optional `API_HOST` / `MAX_REQUEST_BYTES` / `DEEPGRAM_TIMEOUT_SECONDS` / `GLOBAL_MAX_CONCURRENCY`.

## API client guidance (synchronous batches)

The transcription endpoints are synchronous — the connection stays open until the slowest item finishes. Set **generous read timeouts** (minutes per GB of audio; a full 100-item batch can take tens of minutes) and prefer several smaller batches if an HTTP client or intermediary enforces shorter limits. **URL batches are the sanctioned bulk path**; multi-gigabyte multipart batches are intentionally unsupported (the request-byte budget pins one maximal file per request).

## PHI logging policy (non-negotiable)

- **Never log**: audio bytes, transcripts, segments, raw responses, request/response bodies; **keyterms** (log the count); **filenames** (log per-item index + byte size); **full URLs** (log scheme+host or a hash; strip query strings).
- **Do log**: timestamp, route, status, latency, item counts, boolean feature flags, byte sizes, the server `X-Request-ID`, and Deepgram's `request_id`.
- The `httpx` and `deepgram` loggers are pinned to `WARNING` (the SDK sends `keyterm`/`redact` as query params, so DEBUG URL logging would leak them). The 500 handler logs only the exception class; per-item upstream error text returns to the authed caller but logs keep class + status. Run uvicorn with `--no-access-log` (the app emits its own sanitized line). A **Deepgram BAA** is the operator's responsibility before real PHI flows through either front-end.

## Testing

Tests mock `DeepgramClient` — no real API calls. **Two mock points, by design:**

- **`streamlit_app.DeepgramClient`** — UI tests (the `_transcribe_batch` wrapper passes this module global as a seam).
- **`nova.transcribe.DeepgramClient`** — API tests (the core resolves its `None`-default seam at call time).

- `conftest.py` (root) — adds repo root to `sys.path` so tests can `import streamlit_app`, `nova`, `api`.
- `tests/conftest.py` — `mock_deepgram_cls` (patches `streamlit_app.DeepgramClient`), `mock_st`.
- `tests/helpers.py` — `mock_word` (incl. `start`/`end`), `mock_upload`, `wav_bytes`.
- `tests/test_streamlit_app.py` — the UI surface: `_parse_urls`; `_process_inputs`/`_process_urls` (client reuse, option pass-through + omissions, dictation→punctuate, redact via `request_options`, session state, large-upload playback drop, partial/total failure, input-order under reversed completion); `_run` validation branches; `_feature_opts`; `_display_audio`; the renderers and `_diarized_segments` (called via the aliased `streamlit_app._diarized_segments`).
- `tests/test_api.py` — `TestClient` + `patch("nova.transcribe.DeepgramClient")`: auth (401/503, multi-token), every option toggle (incl. dictation→punctuate and redact + `timeout_in_seconds` merged into one `request_options`), batch order, partial failure, all-failed `status: "failed"`, **0-based segment speakers**, `include_raw`/`include_words`, fail-fast 422/400s, files/urls limits, 413 + the capped streamed read (`_read_capped`), and the error-envelope shape.

## Dependencies

Managed by uv via `pyproject.toml` + `uv.lock`.

Runtime: **deepgram-sdk** (v7), **streamlit**, **python-dotenv**, **fastapi**, **uvicorn[standard]**, **python-multipart**

Dev: **ruff**, **ty**, **pytest**, **httpx** (FastAPI `TestClient`)

**deepgram-sdk** notes (v7, project pins `7.3.0`): options are keyword args (not `PrerecordedOptions`), API key passed explicitly to `DeepgramClient(api_key=...)`, responses are Pydantic models. The namespaced client path is `client.listen.v1.media.transcribe_file(request=<bytes>)` / `transcribe_url(url=<str>)`; most options (`model`, `smart_format`, `keyterm`, `language`, `dictation`, `measurements`, `diarize`, `punctuate`) are typed keyword args. `redact` is typed as a single `str`, so multiple redaction groups go through `request_options={"additional_query_parameters": {"redact": [...]}}` (repeated query params); `RequestOptions` also carries `timeout_in_seconds` (used by the API).

- **Response-type union**: the transcribe methods are typed to return `ListenV1Response | ListenV1AcceptedResponse`. Both front-ends only ever receive `ListenV1Response` (which has `results`) because they never pass `callback=`; `ListenV1AcceptedResponse` (callback/async mode) carries only `request_id` and no `results`. `nova.results.first_alternative` guards the `.results` access so the walkers degrade gracefully if that ever changes.
- **Version**: pinned to `deepgram-sdk==7.3.0` (requires Python 3.10+, satisfied by this app's 3.12 floor). The pre-recorded REST surface and the response-type union are identical from v5 through v7; the breaking changes across those majors were confined to the websocket/streaming/TTS/agent APIs this app does not use (see [`docs/Migrating-v5-to-v6.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v5-to-v6.md) / [`docs/Migrating-v6-to-v7.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v6-to-v7.md) in the deepgram-python-sdk repo).

## Design docs

`docs/plans/2026-06-12-fastapi-service-design.md` — the accepted design this `nova/` + `api/` split was migrated to (numbered migration steps in §8). Async batches, a Streamlit HTTP-client mode, request-rate limiting, and containerization are deferred with explicit triggers in §9.
