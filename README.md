# Deepgram Medical Transcription

Transcribe medical audio with the Deepgram Nova-3 Medical model — through a **Streamlit UI** or a **FastAPI service**, both built on a shared, framework-free core (`nova/`) so they never drift.

## Setup

1. Install dependencies: `uv sync`
2. Create your env file: `cp .env.example .env`, then set:
   - `DEEPGRAM_API_KEY` — your Deepgram key (both front-ends; the UI also prompts inline if unset)
   - `API_AUTH_TOKENS` — required only for the API; comma-separated bearer tokens. Generate one with:
     ```bash
     python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```

## Streamlit UI

```bash
uv run streamlit run streamlit_app.py
```

If `DEEPGRAM_API_KEY` is not set, the app prompts for it inline.

**Select audio** from the input tabs at the top:

- **Upload** — up to 100 audio files (mp3, m4a, wav, flac, ogg; max 2 GiB each)
- **Record** — record from microphone (max 10 minutes)
- **URL** — transcribe from HTTP/HTTPS URLs (up to 100 per batch)

Below the input, a **Features** panel (left) holds the request options, with a **Run** button at the bottom. If you populate more than one input tab, Run transcribes a single one by priority — **Upload, then Record, then URL** — and shows a notice naming which ran and which were ignored.

- **Language** — English variants (Nova-3 Medical is English-only)
- **Smart Format** (on by default) — punctuation, paragraph breaks, and entity formatting
- **Keyterm Prompting** — type specialized vocabulary (drug names, procedures, names), Enter to add each, up to 100, to boost recognition
- **Diarize** (off by default) — labels speaker turns as Speaker 1, Speaker 2, … in the transcript (speakers are numbered, not named by role)
- **Dictation** (off by default) — turns spoken commands like "period" / "new paragraph" into punctuation (also enables punctuation)
- **Measurements** (off by default) — abbreviates spoken units (e.g. "five milligrams" → "5 mg")
- **Redact** (none by default) — replaces selected information with redaction tags. Use **PII** to de-identify (names, locations, IDs); note **PHI** strips clinical content itself (conditions, drugs, injuries)

Progress is shown live in a status panel, with a toast when the batch finishes. Once a request completes, the **Transcript** and **JSON** tabs (right) display the response. Each transcript is topped with **Duration** and **Confidence** metric cards and a **Download transcript** button. A single result shows an audio player pinned above the scrollable text; multiple results are labeled and divided per file. With **Diarize** on, the transcript is split into color-coded `Speaker 1:`, `Speaker 2:`, … lines. (Large uploads — over 25 MB — show a notice instead of the inline player to limit memory; recordings and URLs always have one.)

## API

```bash
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

All `/v1` routes require a bearer token from `API_AUTH_TOKENS`. On a loopback bind, interactive docs are served at `/docs`; binding a non-loopback host requires `API_AUTH_TOKENS` (the server refuses to start otherwise) and disables the docs.

| Method & path | Body | Purpose |
|---|---|---|
| `GET /healthz` | — | Liveness (no auth, never calls Deepgram) |
| `POST /v1/transcriptions/urls` | JSON | Transcribe 1–100 remote audio URLs |
| `POST /v1/transcriptions/files` | multipart | Transcribe 1–100 uploaded files |

```bash
curl -X POST localhost:8000/v1/transcriptions/urls \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com/visit.mp3"], "diarize": true, "keyterms": ["metformin"]}'
```

Both endpoints accept the same feature options as the UI (`smart_format`, `diarize`, `dictation`, `measurements`, `keyterms`, `language`, `redact`) plus `include_words` / `include_raw`; the model is pinned server-side. The response is `200` whenever the batch ran — each item carries its own `status`, `transcript`, `segments` (0-based speakers), and optional `words`/`raw`, so one failure never fails the batch. Requests are **synchronous**: set generous client read timeouts (minutes per GB) and prefer several smaller batches over one large multipart upload (URL batches are the sanctioned bulk path).

## Architecture

- **`nova/`** — the shared core (no Streamlit/FastAPI imports): `config` (constants), `transcribe` (`build_options` + `transcribe_batch`), `results` (response walkers). Speakers are Deepgram's native 0-based integers here.
- **`streamlit_app.py`** — the Streamlit UI; a thin adapter over `nova/` that adds widgets, session state, and the renderers (which display speakers 1-based).
- **`api/`** — the FastAPI service; bearer auth, fail-fast validation, per-item isolation, a process-wide concurrency gate, and PHI-safe logging.

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

Tests mock the Deepgram client — no real API calls. The core is tested directly (`tests/test_transcribe.py`, `tests/test_results.py`), the Streamlit adapter in `tests/test_streamlit_app.py`, and the service in `tests/test_api.py` (auth, option pass-through, batch isolation, validation, size limits, and the error envelope).
