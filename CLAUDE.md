# CLAUDE.md

## Project Overview

Transcribe medical audio with the Deepgram Nova-3 Medical model.

A Streamlit-free core (`nova/`) is consumed **in-process** by the Streamlit UI (`streamlit_app.py`): it builds options, runs batches, and parses responses, keeping that logic framework-free and unit-testable independent of the UI.

## Commands

```bash
uv sync                                                          # Install dependencies
uv run streamlit run streamlit_app.py                           # Run the Streamlit UI
uv run ruff check .                                              # Lint
uv run ruff format .                                            # Format
uv run ty check .                                               # Type check
uv run pytest                                                    # Test
```

When working with Python, invoke the relevant `/astral:<skill>` for uv, ty, and ruff to ensure best practices are followed.

## Architecture

### Core — `nova/` (imports no streamlit)

- **`config.py`** — the single source of truth for constants: `MODEL` (`nova-3-medical`), `LANGUAGES` (8 English variants — Nova-3 Medical is English-only), `REDACT_GROUPS` ordered **PII-first** (`pii`/`phi`/`pci`/`numbers`; PHI labeled to flag that it strips clinical content), `DEFAULT_LANGUAGE`/`DEFAULT_SMART_FORMAT`/`DEFAULT_DICTATION`/`DEFAULT_MEASUREMENTS`/`DEFAULT_DIARIZE`, `MAX_KEYTERMS`/`MAX_UPLOADS`/`MAX_CONCURRENCY`/`MAX_FILE_SIZE`, `AUDIO_EXTENSIONS`, and `has_audio_extension()`.
- **`transcribe.py`**:
  - `build_options(*, keyterms=None, language=None, smart_format, dictation, measurements, diarize, redact=None)` — the kwargs dict for the Deepgram call. `model` + `smart_format` are **always** sent; off-by-default features are sent only when enabled — `diarize`/`measurements`, and `dictation` (which also forces `punctuate=True`) — plus `keyterm`/`language` only when truthy. `redact` (typed as a single `str` by the SDK) goes through `request_options["additional_query_parameters"]={"redact":[...]}`, which is omitted entirely when unset.
  - `ItemResult` dataclass `{index, label, response, error}` — `error` is `str(exc)` with no prefix; the calling adapter owns presentation.
  - `transcribe_batch(api_key, items, method, *, options, client_cls=None, as_completed_fn=None, max_concurrency=MAX_CONCURRENCY, on_progress=None)` — one shared client per batch via `ThreadPoolExecutor`; merges `options` onto each item; captures per-item exceptions (one failure never aborts the batch); sorts results back into input order. **`client_cls`/`as_completed_fn` default to `None` and resolve to this module's globals at *call time*** (not def-time) so `tests/test_transcribe.py` can `patch("nova.transcribe.DeepgramClient")` while the in-process UI wrapper injects its own globals as seams. `on_progress(done, total)` fires once per completion.
- **`results.py`** — getattr-guarded response walkers (no `st.*`): `first_alternative`, `transcript_text` (→ `.transcript` or `None`), and `diarized_segments` (groups `alternatives[0].words` into consecutive `(speaker, text)` runs, gated on an integer `words[0].speaker`; `text` uses `punctuated_word` falling back to `word`). **Speakers are Deepgram's native 0-based ints throughout the core; the `+1` display offset lives only in the Streamlit renderer.**
- **`subtitles.py`** — `to_srt(response)` builds SRT subtitle text from `alternatives[0].words` (via `first_alternative`), grouping words into cues on a speaker change / sentence-ending token / `MAX_CUE_WORDS` (12) / `MAX_CUE_SECONDS` (6.0) cap, and skipping words without numeric `start`/`end`. Diarized cues carry a **1-based** `Speaker N:` prefix (the display `+1` is applied here, matching the renderer); a results-less response yields `""`. Streamlit-free and never calls `model_dump_json`, so it's unit-tested directly and safe to import at module load.

### Streamlit UI — `streamlit_app.py`

Re-imports the core constants under their old underscore names (`_LANGUAGES`, `_REDACT_GROUPS`, `_AUDIO_EXTENSIONS`, the `DEFAULT_*`/`MAX_*` names, `has_audio_extension`, `build_options`, `transcribe_batch`), aliases the walkers (`from nova.results import transcript_text as _transcript_text, diarized_segments as _diarized_segments, first_alternative as _first_alternative`), and imports the SRT builder (`from nova.subtitles import to_srt as _to_srt`) — preserving every existing test patch point. (`first_alternative` backs the per-result Confidence metric.)

Visual theming lives in `.streamlit/config.toml` — shared `[theme]` settings plus `[theme.light]` and `[theme.dark]` palettes (one clinical-slate family, inverted) anchored on `primaryColor = "#2563EB"`; defining both modes unlocks the in-app theme switcher, and the semantic color keys (`blueColor`…`grayColor`) back the diarized speaker highlights. Typography loads **Inter** (`font`, UI/headings) and **JetBrains Mono** (`codeFont`, JSON view) from Google Fonts with a system-stack fallback, and `headingFontSizes = ["2.25rem"]` tames Streamlit's oversized default h1. Pure config, so it never touches the test suite. `st.set_page_config(page_title=…, page_icon="🩺", layout="wide")` is the first Streamlit command.

1. Loads `DEEPGRAM_API_KEY` from `.env` via python-dotenv; prompts inline if missing.
2. `_transcribe_batch(api_key, items, method, **opts)` — a thin UI adapter over `nova.transcribe.transcribe_batch` (`**opts` is the `_feature_opts()` dict — `keyterms`/`language`/`smart_format`/`dictation`/`measurements`/`diarize`/`redact` — forwarded straight into `build_options`): it builds the playback `sources` up front, drives a live `st.status` region (a nested `st.progress` bar + label updated via an `on_progress` callback, auto-collapsed to `state="complete"` on finish), renders one `st.error` per failed item, **unconditionally** writes `st.session_state["responses"]` and the parallel `["audio_sources"]` (so a fully-failed run clears stale results), and fires a one-shot completion `st.toast` (`_completion_toast` — success / partial / all-failed). It passes the module-global `DeepgramClient`/`as_completed` as seams. `_playback_source` keeps URLs/small audio but stores `None` for upload bytes over `MAX_PLAYBACK_BYTES` (25 MB); `None` sources render a `PLAYBACK_TOO_LARGE` caption instead of a player. (Note: per-item `st.error`s render after the batch finishes rather than interleaved — no test pins the timing.)
3. `_process_inputs` / `_process_urls` — wrap `_transcribe_batch` for uploads / remote URLs.
4. `_feature_opts()` — reads the Features-tab control values from `st.session_state` (by widget `key`, falling back to the `DEFAULT_*` constants).
5. `_run(api_key, uploaded_files, recording, url_text)` — the Run handler: `st.info`s when more than one input is populated, then validates and transcribes the highest-priority input, **Upload → Record → URL** (file count/size; recording duration via a guarded `wave.open`; URL protocol/`has_audio_extension`), via `_process_inputs`/`_process_urls` with `**_feature_opts()`.
6. Renderers: `_display_transcript` (a `_display_metrics` row of `st.metric` cards — Duration + alternative-level Confidence, via `_result_metrics`, rendered only when available — then, with diarization, one Markdown-escaped per-speaker line **color-highlighted** by speaker index via native `:{color}-background[**Speaker N:**]` directives from `_SPEAKER_COLORS`, **1-based** display; otherwise the flat escaped transcript, or `st.caption(NO_TRANSCRIPT)`), `_display_json` (`st.json(response.model_dump_json())` — deliberately kept minimal: no markdown/expander/download), `_transcript_download` (Transcript-tab downloads: all transcripts as `.txt` via a **deferred** zero-arg `data` callable over `_plain_transcript`, so the blob is assembled on click rather than every fragment rerun — plus, for a single result that yields timed cues, an `.srt` subtitle export via `_to_srt`), `_output_panel` (pinned players + fixed-height container or placeholder; `None` source → `PLAYBACK_TOO_LARGE` caption), `_display_audio` (MIME from extension via `_AUDIO_MIME`, default wav; URL passed through), `_escape_markdown`. Per-speaker color highlighting uses native Markdown color directives (no raw HTML/CSS), so it stays test-safe. Status callouts (`st.error`/`st.warning`/`st.info`) and the completion `st.toast` carry Material Symbol icons (`:material/…:`).
7. Layout: `layout="wide"`; below the title a `st.caption` subtitle. The **Features** controls live in `st.sidebar` (canonical home for app-level settings, freeing the full main width for output), under a `:material/tune:` caption and wrapped in an `st.form("features", border=False)` (so feature edits don't rerun until submit) — Language, Keyterm Prompting, Smart Format, Diarize, Dictation, Measurements, Redact — closed by a full-width `st.form_submit_button("Run", icon=":material/graphic_eq:", width="stretch")` carrying a `help=` hint when disabled. The main area stacks the Material-icon'd audio input tabs (Upload ≤100 files/2 GiB each; Record `st.audio_input` ≤10 min; URL HTTP/HTTPS ≤100) full-width on top, then the full-width **Transcript**/**JSON** tabs (also icon'd) rendered by `_render_output` (an `@st.fragment`, so switching tabs reruns only that panel) over `_output_panel`, with `st.tabs(…, on_change="rerun")` + an `if tab.open is not False:` guard so the hidden tab's body (notably the JSON tab's per-response `model_dump_json()`) is skipped at runtime (inert under the test mock, where `.open` is truthy).

## Configuration

The only env var is `DEEPGRAM_API_KEY` (server-side only), loaded from `.env` (gitignored; see `.env.example`) via python-dotenv; the UI also prompts for it inline if unset.

The Streamlit UI's visual theme is non-secret config in `.streamlit/config.toml` (tracked); secrets (`.env`, `.streamlit/secrets.toml`) are gitignored. The theme's `font`/`codeFont` load Inter / JetBrains Mono from Google Fonts — a client-side CSS request that carries no PHI and falls back to the system stack when blocked or offline.

## PHI logging policy (non-negotiable)

- **Never log** audio bytes, transcripts, segments, raw responses, keyterms, filenames, or full URLs. Treat every Deepgram request/response as carrying PHI.
- A **Deepgram BAA** is the operator's responsibility before real PHI flows through the app.

## Testing

Tests mock `DeepgramClient` — no real API calls. **Two mock points, by design:**

- **`streamlit_app.DeepgramClient`** — UI tests (the `_transcribe_batch` wrapper passes this module global as a seam).
- **`nova.transcribe.DeepgramClient`** — core tests (the core resolves its `None`-default seam at call time, so `test_transcribe.py` patches the module global directly).

- `conftest.py` (root) — adds repo root to `sys.path` so tests can `import streamlit_app`, `nova`.
- `tests/conftest.py` — `mock_deepgram_cls` (patches `streamlit_app.DeepgramClient`), `mock_st`.
- `tests/helpers.py` — `mock_word` (incl. `start`/`end`), `mock_upload`, `wav_bytes`.
- `tests/test_transcribe.py` — the core directly (patches `nova.transcribe.DeepgramClient`): `build_options` (the full option matrix — dictation→punctuate, redact via `request_options`, omissions) and `transcribe_batch` (single-client reuse, option merging, input order incl. reversed completion, per-item error capture, the explicit `client_cls` seam override, `on_progress`).
- `tests/test_results.py` — the walkers directly: `first_alternative`, `transcript_text`, and `diarized_segments` (0-based grouping, `punctuated_word`→`word` fallback, the None cases).
- `tests/test_subtitles.py` — `nova.subtitles` directly: `_format_timestamp` formatting, and `to_srt` (diarized speaker-prefixed cues split on speaker change, flat chunking on sentence-end, the `MAX_CUE_WORDS` cap, words without numeric timing skipped, results-less/empty → `""`).
- `tests/test_streamlit_app.py` — UI-only: `_parse_urls`; the `_transcribe_batch` wrapper via `_process_inputs`/`_process_urls` (session state, large-upload playback drop, per-item `st.error` (message + `:material/error:` icon), progress bar, `st.status` container, `_completion_toast`, input order into session state); `_run` validation branches; `_feature_opts`; `_display_audio`; the renderers (flat plain-text path, **1-based** color-highlighted diarized display, `_display_metrics`, `_transcript_download` — two buttons: a deferred `.txt` `data` callable plus a single-result `.srt`, `PLAYBACK_TOO_LARGE` caption). New `st.*` calls (`status`/`toast`/`metric`/`download_button`/dynamic-tab `.open`) degrade safely under the whole-module `mock_st` MagicMock; the lazy-tab skip is real-runtime-only (inert under the mock). The script is additionally tested under a real runtime via `streamlit.testing.v1.AppTest`, asserting what the mock can't reach: the empty state (placeholder, disabled Run, and the **Features control order** — now in `st.sidebar` — via a document-order key walk over `at.sidebar`), a diarized result (1-based color speaker lines, Duration/Confidence metrics, dropped-playback caption), and a flat transcript.

## Dependencies

Managed by uv via `pyproject.toml` + `uv.lock`.

Runtime: **deepgram-sdk** (v7), **streamlit**, **python-dotenv**

Dev: **ruff**, **ty**, **pytest**

Ruff lint config (`[tool.ruff.lint]`): selects `E`/`F`/`I`/`UP`/`B`; ignores `E501` (line length is formatter-driven); `combine-as-imports = true` (keeps the UI's aliased re-exports in one block).

**deepgram-sdk** notes (v7, project pins `7.3.1`): options are keyword args (not `PrerecordedOptions`), API key passed explicitly to `DeepgramClient(api_key=...)`, responses are Pydantic models. The namespaced client path is `client.listen.v1.media.transcribe_file(request=<bytes>)` / `transcribe_url(url=<str>)`; most options (`model`, `smart_format`, `keyterm`, `language`, `dictation`, `measurements`, `diarize`, `punctuate`) are typed keyword args. `redact` is typed as a single `str`, so multiple redaction groups go through `request_options={"additional_query_parameters": {"redact": [...]}}` (repeated query params).

- **Response-type union**: the transcribe methods are typed to return `ListenV1Response | ListenV1AcceptedResponse`. The UI only ever receives `ListenV1Response` (which has `results`) because it never passes `callback=`; `ListenV1AcceptedResponse` (callback/async mode) carries only `request_id` and no `results`. `nova.results.first_alternative` guards the `.results` access so the walkers degrade gracefully if that ever changes.
- **Version**: pinned to `deepgram-sdk==7.3.1` (requires Python 3.10+, satisfied by this app's 3.12 floor). The pre-recorded REST surface and the response-type union are identical from v5 through v7; the breaking changes across those majors were confined to the websocket/streaming/TTS/agent APIs this app does not use (see [`docs/Migrating-v5-to-v6.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v5-to-v6.md) / [`docs/Migrating-v6-to-v7.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v6-to-v7.md) in the deepgram-python-sdk repo).
