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
2. `_TRANSCRIBE_OPTS` — shared dict of fixed Deepgram API options (model only). Per-batch options are merged on top: `smart_format` (default on), `profanity_filter`/`numerals` (default off) always; `keyterm`/`language` only when set. The defaults live in `DEFAULT_SMART_FORMAT`/`DEFAULT_PROFANITY_FILTER`/`DEFAULT_NUMERALS`/`DEFAULT_LANGUAGE` constants shared by the widgets and `_feature_opts` (single source of truth). `_MARKDOWN_SPECIAL`/`_escape_markdown(text)` backslash-escape inline Markdown metacharacters so transcript text renders verbatim.
3. `_LANGUAGES` — ordered map of supported language codes (English variants only, per Nova-3 Medical) to display labels
4. `_transcribe_batch(api_key, items, method, keyterms=None, language=None, smart_format=DEFAULT_SMART_FORMAT, profanity_filter=DEFAULT_PROFANITY_FILTER, numerals=DEFAULT_NUMERALS)` — creates one shared `DeepgramClient` for a batch, transcribes each item (always sending `smart_format`/`profanity_filter`/`numerals`; adding `keyterm`/`language` only when set), isolates per-item errors via `st.error`, then **unconditionally** writes `st.session_state["responses"]` and the parallel `["audio_sources"]` (playable source per result, in input order) — so a fully-failed run clears stale results. `_playback_source` keeps URLs/small audio but stores `None` for upload bytes over `MAX_PLAYBACK_BYTES` (25 MB) to bound session memory; `None` sources render no player.
5. `_process_inputs(api_key, files, **opts)` — wraps `_transcribe_batch` for file uploads
6. `_process_urls(api_key, urls, **opts)` — wraps `_transcribe_batch` for remote audio URLs
7. `_feature_opts()` — reads the Features-tab control values from `st.session_state` (by widget `key`, falling back to the `DEFAULT_*` constants) and returns the kwargs dict passed to `_process_inputs`/`_process_urls`. The control widgets render before the Run button in the same tab, so their keyed values are set when Run fires.
8. `_run(api_key, uploaded_files, recording, url_text)` — the Run button's handler: if more than one input is populated it `st.info`s which one runs and which are ignored, then validates and transcribes the highest-priority input, **Upload → Record → URL** (file count/size; recording duration via a guarded `wave.open` that `st.error`s on unreadable audio; URL protocol/extension), via `_process_inputs`/`_process_urls` with `**_feature_opts()`.
9. `_display_audio(name, source)` — renders an `st.audio` player; for bytes it picks the MIME from the name's extension via `_AUDIO_MIME` (default `audio/wav`), for a URL string it passes the URL through.
10. `_display_transcript(response)` / `_display_json(response)` — minimal per-result renderers: Markdown-escaped transcript via `st.markdown`, raw JSON via `st.json`. No metrics, highlighting, expanders, or downloads. `_output_panel(responses, audio_sources, render)` is the shared per-tab body (pinned players + fixed-height container or placeholder).
11. Layout:
   - **Audio input tabs** (full width, above): **Upload** (≤100 files, 2 GB each — mp3/m4a/wav/flac/ogg), **Record** (`st.audio_input`, ≤10 min), **URL** (HTTP/HTTPS, ≤100 per batch). Each holds only its input widget. (These render before the columns, so the Run handler below can read `uploaded_files`/`recording`/`url_text`.)
   - **Left column** — a single **Features** tab holding the shared controls (single `key`s): **Language** `st.selectbox` (from `_LANGUAGES`), **Smart Format** `st.toggle` (default on), **Keyterm Prompting** `st.multiselect` (`accept_new_options=True`, capped at `MAX_KEYTERMS`=100), **Profanity Filter** `st.toggle` (default off), **Numerals** `st.toggle` (default off), and a single primary full-width **Run** button at the bottom. Run is enabled when an API key and at least one input are present; on click it validates and transcribes whichever input is populated, priority **Upload → Record → URL** (via `_process_inputs`/`_process_urls` with `**_feature_opts()`).
   - **Right column** — **Transcript** and **JSON** tabs, each rendered by `_output_panel(...)`: empty → `PLACEHOLDER`; a **single** result → its `_display_audio` player pinned above a fixed-height scrollable `st.container(height=OUTPUT_HEIGHT, border=True)` (=400 px) holding the text; **multiple** results → one labeled, `st.divider`-separated block per result (bold `name` + player + body) inside the container. `_display_transcript` feeds the Transcript tab, `_display_json` the JSON tab. (Rendering the same audio in both tabs is fine — Streamlit auto-disambiguates by position.)

## Testing

Tests mock `DeepgramClient` — no real API calls.

- `conftest.py` (root) — adds repo root to `sys.path` so tests can `import streamlit_app`
- `tests/conftest.py` — shared fixtures (`mock_deepgram_cls`, `mock_st`)
- `tests/test_streamlit_app.py`:
  - `_parse_urls()` — valid/invalid protocols, blank lines, mixed input
  - `_process_inputs()` / `_process_urls()` — client reuse, option passing, keyterm and language pass-through (and omission when unset), smart_format (off path), profanity_filter and numerals (on path) toggles, session state (responses + audio_sources), large-upload playback drop, partial failure with audio_sources alignment, total failure clears stale results, input-order preservation under reversed completion, error format
  - `_run()` — input priority (upload → record → url); multi-input `st.info` notice (and none for single input); no-input no-op; validation branches: too-many-files, oversized-file skip, recording-too-long, exact-duration-boundary accepted, unreadable recording, invalid URL, no-extension warning (message + mixed + query-string) (uses `mock_upload`/`wav_bytes` from `tests/helpers.py`)
  - `_feature_opts()` — all-default (empty), fully-populated, and partial session state
  - `_display_audio()` — MIME from extension for bytes, default wav without extension, URL passed through
  - `_display_transcript()` / `_display_json()` / `_output_panel()` — Markdown-escaped transcript (incl. metacharacters), raw JSON, the minimal contract (no highlighting/expander/metrics/downloads), and the panel's placeholder / single / multi-labeled / None-source behavior

## Dependencies

Managed by uv via `pyproject.toml` + `uv.lock`.

Runtime: **deepgram-sdk** (v5), **streamlit**, **python-dotenv**

Dev: **ruff**, **ty**, **pytest**

**deepgram-sdk** notes: options are keyword args (not `PrerecordedOptions`), API key passed explicitly to `DeepgramClient`, responses are Pydantic models.
