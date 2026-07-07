# Deepgram Medical Transcription

[![CI](https://github.com/darylalim/deepgram-medical-transcription/actions/workflows/ci.yml/badge.svg)](https://github.com/darylalim/deepgram-medical-transcription/actions/workflows/ci.yml)

Streamlit application for medical transcription using Deepgram Nova, built on a framework-free core (`nova/`) that handles option building, batching, and response parsing.

## Setup

1. Install dependencies: `uv sync`
2. Create your env file: `cp .env.example .env`, then set:
   - `DEEPGRAM_API_KEY` — your Deepgram key (the UI also prompts inline if unset)

## Usage

```bash
uv run streamlit run streamlit_app.py
```

If `DEEPGRAM_API_KEY` is not set, the app prompts for it inline.

**Select audio** from the input tabs at the top:

- **Upload** — up to 100 audio files (mp3, m4a, wav, flac, ogg; max 2 GiB each)
- **Record** — record from microphone (max 10 minutes)
- **URL** — transcribe from HTTP/HTTPS URLs (up to 100 per batch)

A **Features** panel in the left sidebar holds the request options, closed by a **Run** button. If you populate more than one input tab, Run transcribes a single one by priority — **Upload, then Record, then URL** — and shows a notice naming which ran and which were ignored.

- **Language** — English variants (Nova-3 Medical is English-only)
- **Keyterm Prompting** — type specialized vocabulary (drug names, procedures, names), Enter to add each, up to 100, to boost recognition
- **Smart Format** (on by default) — punctuation, paragraph breaks, and entity formatting
- **Diarize** (off by default) — labels speaker turns as Speaker 1, Speaker 2, … in the transcript (speakers are numbered, not named by role)
- **Dictation** (off by default) — turns spoken commands like "period" / "new paragraph" into punctuation (also enables punctuation)
- **Measurements** (off by default) — abbreviates spoken units (e.g. "five milligrams" → "5 mg")
- **Redact** (none by default) — replaces selected information with redaction tags. Use **PII** to de-identify (names, locations, IDs); note **PHI** strips clinical content itself (conditions, drugs, injuries)

Progress is shown live in a status panel, with a toast when the batch finishes. Once a request completes, the full-width **Transcript** and **JSON** tabs display the response. Each transcript is topped with **Duration** and **Confidence** metric cards and a **Download transcript** button; a single result also offers a **Download subtitles (SRT)** button (timestamped, speaker-labeled cues) and an audio player pinned above the scrollable text. Multiple results are labeled and divided per file. With **Diarize** on, the transcript is split into color-coded `Speaker 1:`, `Speaker 2:`, … lines. (Large uploads — over 25 MB — show a notice instead of the inline player to limit memory; recordings and URLs always have one.)

The app ships **light and dark themes** (self-hosted Inter UI type, JetBrains Mono for the JSON view) — switch modes via the Settings menu.

## Architecture

- **`nova/`** — the framework-free core (no Streamlit imports): `config` (constants), `transcribe` (`build_options` + `transcribe_batch`), `results` (response walkers), `subtitles` (`to_srt` — SRT subtitle export). Speakers are Deepgram's native 0-based integers here.
- **`streamlit_app.py`** — the Streamlit UI; a thin adapter over `nova/` that adds widgets, session state, and the renderers (which display speakers 1-based).

## Sample Audio

Medical dictation practice files from [NCH Software](https://www.nch.com.au/scribe/practice.html):

- [Chris Smith Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-4.mp3)
- [Janet Jones Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-5.mp3)
- [John Finton Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-6.mp3)

## Testing

```bash
uv run pytest         # tests
uv run ruff check .   # lint
uv run ruff format .  # format
uv run ty check .     # type check
```

Tests mock the Deepgram client — no real API calls. The core is tested directly (`tests/test_transcribe.py`, `tests/test_results.py`, `tests/test_subtitles.py`), the Streamlit adapter in `tests/test_streamlit_app.py`, the dev hooks in `tests/test_hooks.py`, and the CI workflow itself in `tests/test_ci_workflow.py`.

**Continuous integration** — `.github/workflows/ci.yml` (GitHub Actions) runs these same four gates on every push to `main` and every pull request, plus `uv sync --locked` to catch lockfile drift. It needs no secrets: tests mock Deepgram, so CI never calls the API.

## Claude Code hooks

The repo ships **Claude Code hooks** in `.claude/` (shared via `settings.json`) that run these checks automatically while you work: they format, lint, and type-check edited Python, block edits to secret files (`.env`, `.streamlit/secrets.toml`), and run the test suite when a turn finishes. Newly added hooks need approval before firing (`/hooks`). Personal overrides go in `.claude/settings.local.json` (gitignored). See CLAUDE.md for the full breakdown.
