# CLAUDE.md

## Project Overview

Transcribe audio files with the Deepgram Nova-3 Medical model.

## Commands

```bash
uv sync                              # Install dependencies
uv run streamlit run streamlit_app.py # Run app
uv run ruff check .                   # Lint
uv run ruff format .                  # Format
uv run ty check .                     # Type check
uv run pytest                         # Test
```

## Architecture

Single-file Streamlit app (`streamlit_app.py`):

1. Loads `DEEPGRAM_API_KEY` from `.env` via python-dotenv; prompts inline if missing
2. `_TRANSCRIBE_OPTS` — shared dict of fixed Deepgram API options (model only). Per-batch options are merged on top: `smart_format` (default on) is always sent; the off-by-default features are sent only when enabled — `diarize`, `measurements`, and `dictation` (which also forces `punctuate=True`, since dictation requires it), plus `keyterm`/`language`. `redact` (a list of groups) is sent as repeated query params via `request_options={"additional_query_parameters": {"redact": [...]}}` because the SDK types `redact` as a single `str`. The defaults live in `DEFAULT_SMART_FORMAT`/`DEFAULT_DICTATION`/`DEFAULT_MEASUREMENTS`/`DEFAULT_DIARIZE`/`DEFAULT_LANGUAGE` constants shared by the widgets and `_feature_opts` (single source of truth); `redact` has no default constant (empty list, like `keyterms`). `_REDACT_GROUPS` maps the redaction group codes to display labels, ordered **PII-first** (de-identification) with PHI labeled to flag that it strips clinical content (`pii`/`phi`/`pci`/`numbers`). `_MARKDOWN_SPECIAL`/`_escape_markdown(text)` backslash-escape inline Markdown metacharacters so transcript text renders verbatim.
3. `_LANGUAGES` — ordered map of supported language codes (English variants only, per Nova-3 Medical) to display labels
4. `_transcribe_batch(api_key, items, method, keyterms=None, language=None, smart_format=DEFAULT_SMART_FORMAT, dictation=DEFAULT_DICTATION, measurements=DEFAULT_MEASUREMENTS, diarize=DEFAULT_DIARIZE, redact=None)` — creates one shared `DeepgramClient` for a batch, transcribes each item (always sending `smart_format`; adding `diarize`/`measurements`/`dictation`(+`punctuate`)/`keyterm`/`language`/`redact` only when set), isolates per-item errors via `st.error`, then **unconditionally** writes `st.session_state["responses"]` and the parallel `["audio_sources"]` (playable source per result, in input order) — so a fully-failed run clears stale results. `_playback_source` keeps URLs/small audio but stores `None` for upload bytes over `MAX_PLAYBACK_BYTES` (25 MB) to bound session memory; `None` sources render no player.
5. `_process_inputs(api_key, files, **opts)` — wraps `_transcribe_batch` for file uploads
6. `_process_urls(api_key, urls, **opts)` — wraps `_transcribe_batch` for remote audio URLs
7. `_feature_opts()` — reads the Features-tab control values from `st.session_state` (by widget `key`, falling back to the `DEFAULT_*` constants) and returns the kwargs dict passed to `_process_inputs`/`_process_urls`. The control widgets render before the Run button in the same tab, so their keyed values are set when Run fires.
8. `_run(api_key, uploaded_files, recording, url_text)` — the Run button's handler: if more than one input is populated it `st.info`s which one runs and which are ignored, then validates and transcribes the highest-priority input, **Upload → Record → URL** (file count/size; recording duration via a guarded `wave.open` that `st.error`s on unreadable audio; URL protocol/extension), via `_process_inputs`/`_process_urls` with `**_feature_opts()`.
9. `_display_audio(name, source)` — renders an `st.audio` player; for bytes it picks the MIME from the name's extension via `_AUDIO_MIME` (default `audio/wav`), for a URL string it passes the URL through.
10. `_first_alternative(response)` is the shared getattr-guard walk that returns the first channel's first alternative, or `None` for a results-less `ListenV1AcceptedResponse` / empty channels/alternatives. Both `_transcript_text(response)` (→ `.transcript` or `None`) and `_diarized_segments(response)` build on it: the latter groups `alternatives[0].words` into consecutive `(speaker, text)` runs when diarization labeled them (gated on an **integer** `words[0].speaker`, which also keeps mocked/unlabeled words on the flat path; a later word lacking an integer speaker continues the current run rather than starting a "Speaker None" segment), else `None`. `_display_transcript(response)` / `_display_json(response)` are minimal per-result renderers: with diarization, one Markdown-escaped `**Speaker N:** …` line per run (**1-based**, so the first speaker reads "Speaker 1"); otherwise the flat Markdown-escaped transcript via `st.markdown` (or `st.caption(NO_TRANSCRIPT)` when `_transcript_text` is `None`); raw JSON via `st.json(response.model_dump_json())` (shape-agnostic — serializes either response type). No metrics, highlighting, expanders, or downloads. `_output_panel(responses, audio_sources, render)` is the shared per-tab body (pinned players + fixed-height container or placeholder).
11. Layout:
   - **Audio input tabs** (full width, above): **Upload** (≤100 files, 2 GB each — mp3/m4a/wav/flac/ogg), **Record** (`st.audio_input`, ≤10 min), **URL** (HTTP/HTTPS, ≤100 per batch). Each holds only its input widget. (These render before the columns, so the Run handler below can read `uploaded_files`/`recording`/`url_text`.)
   - **Left column** — a single **Features** tab holding the shared controls (single `key`s): **Language** `st.selectbox` (from `_LANGUAGES`), **Smart Format** `st.toggle` (default on), **Keyterm Prompting** `st.multiselect` (`accept_new_options=True`, capped at `MAX_KEYTERMS`=100), **Diarize** `st.toggle` (default off), **Dictation** `st.toggle` (default off), **Measurements** `st.toggle` (default off), **Redact** `st.multiselect` (from `_REDACT_GROUPS`, default none), and a single primary full-width **Run** button at the bottom. Run is enabled when an API key and at least one input are present; on click it validates and transcribes whichever input is populated, priority **Upload → Record → URL** (via `_process_inputs`/`_process_urls` with `**_feature_opts()`).
   - **Right column** — **Transcript** and **JSON** tabs, each rendered by `_output_panel(...)`: empty → `PLACEHOLDER`; a **single** result → its `_display_audio` player pinned above a fixed-height scrollable `st.container(height=OUTPUT_HEIGHT, border=True)` (=400 px) holding the text; **multiple** results → one labeled, `st.divider`-separated block per result (bold `name` + player + body) inside the container. `_display_transcript` feeds the Transcript tab, `_display_json` the JSON tab. (Rendering the same audio in both tabs is fine — Streamlit auto-disambiguates by position.)

## Testing

Tests mock `DeepgramClient` — no real API calls.

- `conftest.py` (root) — adds repo root to `sys.path` so tests can `import streamlit_app`
- `tests/conftest.py` — shared fixtures (`mock_deepgram_cls`, `mock_st`)
- `tests/test_streamlit_app.py`:
  - `_parse_urls()` — valid/invalid protocols, blank lines, mixed input
  - `_process_inputs()` / `_process_urls()` — client reuse, option passing, keyterm and language pass-through (and omission when unset), smart_format (off path), diarize/measurements (on path) and dictation (forces punctuate, incl. with smart_format off) toggles, off-features omitted by default, redact via `request_options` (single- and multi-group, omitted when empty), session state (responses + audio_sources), large-upload playback drop, partial failure with audio_sources alignment, total failure clears stale results, input-order preservation under reversed completion, error format
  - `_run()` — input priority (upload → record → url); multi-input `st.info` notice (and none for single input); no-input no-op; validation branches: too-many-files, oversized-file skip, recording-too-long, exact-duration-boundary accepted, unreadable recording, invalid URL, no-extension warning (message + mixed + query-string) (uses `mock_upload`/`wav_bytes` from `tests/helpers.py`)
  - `_feature_opts()` — all-default (empty), fully-populated, and partial session state
  - `_display_audio()` — MIME from extension for bytes, default wav without extension, URL passed through
  - `_display_transcript()` / `_diarized_segments()` / `_display_json()` / `_output_panel()` — Markdown-escaped transcript (incl. metacharacters), diarized `**Speaker N:**` runs (1-based labels, consecutive-speaker grouping, single-speaker run, `punctuated_word`→`word` token fallback, escaping, flat-transcript fallback when words have no integer speaker, and `_diarized_segments` returning `None` for empty words/alternatives), raw JSON, the minimal contract (no highlighting/expander/metrics/downloads), the no-results guard (`_display_transcript` renders `NO_TRANSCRIPT` for a response with missing or empty results; `_display_json` still serializes a results-less `ListenV1AcceptedResponse`-style response), and the panel's placeholder / single / multi-labeled / None-source behavior

## Dependencies

Managed by uv via `pyproject.toml` + `uv.lock`.

Runtime: **deepgram-sdk** (v7), **streamlit**, **python-dotenv**

Dev: **ruff**, **ty**, **pytest**

**deepgram-sdk** notes (v7, project pins `7.3.0`): options are keyword args (not `PrerecordedOptions`), API key passed explicitly to `DeepgramClient(api_key=...)`, responses are Pydantic models. The namespaced client path is `client.listen.v1.media.transcribe_file(request=<bytes>)` / `transcribe_url(url=<str>)`; most options (`model`, `smart_format`, `keyterm`, `language`, `dictation`, `measurements`, `diarize`, `punctuate`) are typed keyword args. `redact` is typed as a single `str`, so multiple redaction groups go through `request_options={"additional_query_parameters": {"redact": [...]}}` (repeated query params).

- **Response-type union**: the transcribe methods are typed to return `ListenV1Response | ListenV1AcceptedResponse`. The app only ever receives `ListenV1Response` (which has `results`) because it never passes `callback=`; `ListenV1AcceptedResponse` (callback/async mode) carries only `request_id` and no `results`. `_transcript_text` guards the `.results` access so the renderers degrade gracefully if that ever changes.
- **Version**: pinned to `deepgram-sdk==7.3.0` (requires Python 3.10+, satisfied by this app's 3.12 floor). The pre-recorded REST surface and the response-type union are identical from v5 through v7; the breaking changes across those majors were confined to the websocket/streaming/TTS/agent APIs this app does not use (see [`docs/Migrating-v5-to-v6.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v5-to-v6.md) / [`docs/Migrating-v6-to-v7.md`](https://github.com/deepgram/deepgram-python-sdk/blob/main/docs/Migrating-v6-to-v7.md) in the deepgram-python-sdk repo). Verified post-upgrade: `DeepgramClient(api_key=...)`, the `transcribe_file`/`transcribe_url` kwargs, and `ListenV1Response.model_dump_json()` are all unchanged.
