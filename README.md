# Deepgram Medical Transcription

[![CI](https://github.com/darylalim/deepgram-medical-transcription/actions/workflows/ci.yml/badge.svg)](https://github.com/darylalim/deepgram-medical-transcription/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Streamlit application for medical transcription using Deepgram's Nova-3 Medical model (English-only), built on a framework-free core (`nova/`) that handles option building, batching, and response parsing.

> **Reference implementation — not certified for clinical use.** You are responsible for your own Deepgram BAA and PHI handling before any real patient data flows through this app. See [License](#license).

## Features

- **Batch transcription** from three input sources — upload files, record from the microphone, or transcribe remote URLs.
- **Nova-3 Medical** speech-to-text across eight English variants.
- **Keyterm prompting** — boost recognition of specialized vocabulary (drug names, procedures).
- **Speaker diarization** with color-coded per-speaker transcript lines.
- **Redaction** of PII, PHI, PCI, and numbers for de-identification.
- **Smart formatting**, spoken **dictation** commands, and **measurement** abbreviation.
- **Downloads** — plain-text transcript and timestamped, speaker-labeled **SRT** subtitles.
- **Light and dark themes** with self-hosted fonts (no third-party font CDN).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — manages the Python toolchain and dependencies (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- Python 3.12+ — `uv sync` fetches a compatible interpreter if you don't already have one.
- A Deepgram API key — create a free one at the [Deepgram Console](https://console.deepgram.com).

## Setup

1. Install dependencies: `uv sync`
2. Create your env file: `cp .env.example .env`, then set `DEEPGRAM_API_KEY`.

## Usage

```bash
uv run streamlit run streamlit_app.py
```

If `DEEPGRAM_API_KEY` is not set, the app prompts for it inline.

**Select audio** from the input tabs at the top:

- **Upload** — up to 100 audio files (mp3, m4a, wav, flac, ogg; max 200 MB each)
- **Record** — record from microphone (max 10 minutes)
- **URL** — transcribe from HTTP/HTTPS URLs (up to 100 per batch)

A **Features** panel in the left sidebar holds the request options, closed by a **Run** button. If you populate more than one input tab, Run transcribes a single one by priority — **Upload, then Record, then URL** — and shows a notice naming which ran and which were ignored.

- **Language** — English variants (Nova-3 Medical is English-only)
- **Keyterm Prompting** — type specialized vocabulary (drug names, procedures, names), Enter to add each, up to 100, to boost recognition
- **Smart Format** (on by default) — punctuation, paragraph breaks, and entity formatting
- **Diarize** (off by default) — labels speaker turns as Speaker 1, Speaker 2, … in the transcript (speakers are numbered, not named by role)
- **Dictation** (off by default) — turns spoken commands like "period" / "new paragraph" into punctuation (also enables punctuation)
- **Measurements** (off by default) — abbreviates spoken units (e.g. "five milligrams" → "5 mg")
- **Redact** (none by default) — replaces selected information with redaction tags. Four groups are selectable: **PII** de-identifies (names, locations, IDs); **PHI** removes clinical content itself (conditions, drugs, injuries); **PCI** redacts card numbers; **Numbers** redacts numeric values.

Once a request runs:

- **Live progress** — a status panel tracks the batch, with a toast when it finishes.
- **Transcript / JSON tabs** — full-width, displaying the response; multiple results are labeled and divided per file.
- **Metrics & downloads** — each transcript is topped with **Duration** and **Confidence** metric cards and a **Download transcript** (`.txt`) button; a single result also offers **Download subtitles** (`.srt`, timestamped speaker-labeled cues).
- **Audio player** — pinned above the scrollable transcript. Inline audio over 25 MB — a large upload or a long recording — shows a notice instead of the player to limit memory; remote URLs always get one.
- **Diarized view** — with **Diarize** on, the transcript is split into color-coded `Speaker 1:`, `Speaker 2:`, … lines.

The app ships light and dark themes (self-hosted Inter for UI type, JetBrains Mono for the JSON view) — switch modes via the Settings menu.

**Troubleshooting** — if transcription fails with a per-file error (rather than the app refusing to start), check that `DEEPGRAM_API_KEY` is valid and has available credit: an invalid or expired key is reported as a per-item transcription failure, not a startup error.

## Sample Audio

Medical dictation practice files from [NCH Software](https://www.nch.com.au/scribe/practice.html):

- [Chris Smith Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-4.mp3)
- [Janet Jones Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-5.mp3)
- [John Finton Medical Report](https://www.nch.com.au/scribe/practice/audio-sample-6.mp3)

## Architecture

- **`nova/`** — the framework-free core (no Streamlit imports): `config` (constants), `transcribe` (`build_options` + `transcribe_batch`), `results` (response walkers), `subtitles` (`to_srt` — SRT subtitle export). Speakers are Deepgram's native 0-based integers here.
- **`streamlit_app.py`** — the Streamlit UI; a thin adapter over `nova/` that adds widgets, session state, and the renderers (which display speakers 1-based).

## Testing

```bash
uv run pytest         # tests
uv run ruff check .   # lint
uv run ruff format .  # format
uv run ty check .     # type check
```

Tests mock the Deepgram client — no real API calls. The core is tested directly (`tests/test_transcribe.py`, `tests/test_results.py`, `tests/test_subtitles.py`), the Streamlit adapter in `tests/test_streamlit_app.py`, the dev hooks in `tests/test_hooks.py`, and the CI workflow itself in `tests/test_ci_workflow.py`.

**Continuous integration** — `.github/workflows/ci.yml` (GitHub Actions) runs these same four gates plus `uv sync --locked` across a Python 3.12 + 3.13 matrix on every push to `main`, every pull request, and manual dispatch. It needs no secrets: tests mock Deepgram, so CI never calls the API.

## Claude Code hooks

The repo ships **Claude Code hooks** in `.claude/` (shared via `settings.json`) that run these checks automatically while you work: they format, lint, and type-check edited Python, block edits to secret files (`.env`, `.streamlit/secrets.toml`), and run the test suite when a turn finishes. Newly added hooks need approval before firing (`/hooks`). Personal overrides go in `.claude/settings.local.json` (gitignored). See CLAUDE.md for the full breakdown.

## License

Released under the [MIT License](LICENSE). The software is provided "as is," without warranty of any kind; it is a reference implementation and is not certified for clinical use. Operators are responsible for their own Deepgram BAA and PHI handling before processing real patient data.
