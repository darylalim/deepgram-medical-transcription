import io
import os
import re
import wave
from collections.abc import Callable
from concurrent.futures import as_completed
from typing import Any

import streamlit as st
from deepgram import DeepgramClient
from dotenv import load_dotenv

from nova.config import (
    AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS,
    DEFAULT_DIARIZE,
    DEFAULT_DICTATION,
    DEFAULT_LANGUAGE,
    DEFAULT_MEASUREMENTS,
    DEFAULT_SMART_FORMAT,
    LANGUAGES as _LANGUAGES,
    MAX_FILE_SIZE,
    MAX_KEYTERMS,
    MAX_UPLOADS,
    REDACT_GROUPS as _REDACT_GROUPS,
    has_audio_extension,
)
from nova.results import (
    diarized_segments as _diarized_segments,
    first_alternative as _first_alternative,
    transcript_text as _transcript_text,
)
from nova.transcribe import build_options, transcribe_batch

load_dotenv()

MAX_RECORDING_SECONDS = 10 * 60  # 10 minutes
MAX_PLAYBACK_BYTES = 25 * 1024 * 1024  # larger uploads skip inline playback (memory)
OUTPUT_HEIGHT = 400  # fixed height (px) of the Transcript/JSON output panel

# Per-speaker label colors for diarized transcripts, cycled by speaker index.
# These are native Markdown color directives (:blue-background[...]) — no raw
# HTML/CSS — so they stay test-safe plain strings. 'rainbow' is excluded: it is
# not a valid background-highlight color.
_SPEAKER_COLORS = ("blue", "green", "violet", "orange", "red", "gray")

_AUDIO_TYPES = [ext.lstrip(".") for ext in _AUDIO_EXTENSIONS]
_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}

# Inline Markdown metacharacters, escaped so transcript text renders literally.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]~])")


def _escape_markdown(text: str) -> str:
    """Backslash-escape inline Markdown metacharacters so text renders verbatim."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _playback_source(value: object) -> bytes | str | None:
    """Keep URLs and small audio for inline playback; drop large upload bytes (memory)."""
    if isinstance(value, bytes):
        return value if len(value) <= MAX_PLAYBACK_BYTES else None
    return value if isinstance(value, str) else None


def _completion_toast(n_ok: int, total: int) -> None:
    """Fire a one-shot toast summarizing the finished batch."""
    if n_ok == total:
        st.toast(f"Transcribed {n_ok} item(s)", icon=":material/check_circle:")
    elif n_ok:
        st.toast(
            f"Transcribed {n_ok}/{total}; {total - n_ok} failed",
            icon=":material/warning:",
        )
    else:
        st.toast(f"All {total} item(s) failed", icon=":material/error:")


def _transcribe_batch(
    api_key: str,
    items: list[tuple[str, dict[str, Any]]],
    method: str,
    **opts: Any,
):
    """Transcribe a batch via the shared core, owning the Streamlit-side concerns.

    Thin UI adapter over `nova.transcribe.transcribe_batch`: it builds the playback
    sources up front, drives a live `st.status` progress region (label + bar), renders
    one `st.error` per failed item, writes results to session state, and fires a
    completion `st.toast`. `**opts` is the `_feature_opts` dict (its keys match
    `build_options` exactly); the module-global `DeepgramClient`/`as_completed` are
    passed as seams so the existing test patch points keep intercepting them.
    """
    options = build_options(**opts)
    total = len(items)
    sources = {
        i: _playback_source(kwargs.get("request", kwargs.get("url")))
        for i, (_, kwargs) in enumerate(items)
    }

    with st.status(f"Transcribing 0/{total}...", expanded=True) as status:
        progress = st.progress(0.0)

        def _on_progress(done: int, t: int) -> None:
            progress.progress(done / t)
            status.update(label=f"Transcribing {done}/{t}...")

        results = transcribe_batch(
            api_key,
            items,
            method,
            options=options,
            client_cls=DeepgramClient,
            as_completed_fn=as_completed,
            on_progress=_on_progress,
        )
        ok = [r for r in results if r.error is None]
        status.update(
            label=f"Transcribed {len(ok)}/{total}", state="complete", expanded=False
        )

    for r in results:
        if r.error is not None:
            st.error(f"Transcription failed for {r.label}: {r.error}")

    # Always overwrite (even when empty) so a fully-failed run clears stale results.
    st.session_state["responses"] = [(r.label, r.response) for r in ok]
    st.session_state["audio_sources"] = [sources[r.index] for r in ok]

    _completion_toast(len(ok), total)


def _process_inputs(api_key: str, files: list[tuple[str, bytes]], **opts) -> None:
    """Transcribe files with a shared client and store results in session state."""
    items = [(name, {"request": data}) for name, data in files]
    _transcribe_batch(api_key, items, "transcribe_file", **opts)


def _process_urls(api_key: str, urls: list[str], **opts) -> None:
    """Transcribe remote audio URLs with a shared client and store results in session state."""
    items = [(url, {"url": url}) for url in urls]
    _transcribe_batch(api_key, items, "transcribe_url", **opts)


def _parse_urls(text: str) -> tuple[list[str], list[str]]:
    """Parse newline-separated text into (valid_urls, invalid_urls)."""
    raw = [line.strip() for line in text.splitlines()]
    urls = [u for u in raw if u]
    valid = [u for u in urls if u.startswith(("http://", "https://"))]
    invalid = [u for u in urls if not u.startswith(("http://", "https://"))]
    return valid, invalid


def _feature_opts() -> dict[str, Any]:
    """Read the current Features-tab control values from session state."""
    return {
        "keyterms": st.session_state.get("keyterms", []),
        "language": st.session_state.get("language", DEFAULT_LANGUAGE),
        "smart_format": st.session_state.get("smart_format", DEFAULT_SMART_FORMAT),
        "dictation": st.session_state.get("dictation", DEFAULT_DICTATION),
        "measurements": st.session_state.get("measurements", DEFAULT_MEASUREMENTS),
        "diarize": st.session_state.get("diarize", DEFAULT_DIARIZE),
        "redact": st.session_state.get("redact", []),
    }


def _run(api_key: str, uploaded_files: list, recording: Any, url_text: str) -> None:
    """Validate and transcribe whichever input is provided (priority: upload, record, url)."""
    present = [
        name
        for name, ok in (
            ("Upload", bool(uploaded_files)),
            ("Record", recording is not None),
            ("URL", bool(url_text.strip())),
        )
        if ok
    ]
    if len(present) > 1:
        chosen, *ignored = present
        st.info(
            f"Multiple inputs detected; transcribing {chosen} and ignoring "
            f"{', '.join(ignored)} (priority: Upload > Record > URL)."
        )
    if uploaded_files:
        if len(uploaded_files) > MAX_UPLOADS:
            st.error(f"Too many files. Maximum is {MAX_UPLOADS} per batch.")
            return
        oversized = [f.name for f in uploaded_files if f.size > MAX_FILE_SIZE]
        if oversized:
            st.error(f"Skipped (exceeds 2 GiB): {', '.join(oversized)}")
        valid = [
            (f.name, f.getvalue()) for f in uploaded_files if f.size <= MAX_FILE_SIZE
        ]
        if valid:
            _process_inputs(api_key, valid, **_feature_opts())
    elif recording is not None:
        audio_bytes = recording.getvalue()
        try:
            with wave.open(io.BytesIO(audio_bytes)) as wf:
                framerate = wf.getframerate()
                if not framerate:
                    raise wave.Error("zero framerate")
                duration = wf.getnframes() / framerate
        except (wave.Error, EOFError):
            st.error("Could not read the recording.")
            return
        if duration > MAX_RECORDING_SECONDS:
            st.error("Recording exceeds the 10-minute limit.")
        else:
            _process_inputs(api_key, [("Recording", audio_bytes)], **_feature_opts())
    elif url_text.strip():
        valid, invalid = _parse_urls(url_text)
        if invalid:
            st.error(f"Invalid URL(s): {', '.join(invalid)}")
        elif len(valid) > MAX_UPLOADS:
            st.error(f"Too many URLs. Maximum is {MAX_UPLOADS} per batch.")
        else:
            no_ext = [u for u in valid if not has_audio_extension(u)]
            if no_ext:
                st.warning(
                    f"Unrecognized audio extension (supported: {', '.join(_AUDIO_TYPES)}): {', '.join(no_ext)}"
                )
            _process_urls(api_key, valid, **_feature_opts())


def _display_audio(name: str, source: bytes | str) -> None:
    """Render an audio player for a transcribed source (file/recording bytes or remote URL)."""
    if isinstance(source, bytes):
        mime = _AUDIO_MIME.get(os.path.splitext(name)[1].lower(), "audio/wav")
        st.audio(source, format=mime)
    else:
        st.audio(source)


def _result_metrics(response: Any) -> tuple[float | None, float | None]:
    """Extract (duration_seconds, confidence) for a response; either may be None.

    Confidence is the alternative-level value Deepgram reports for the transcript.
    Both reads are getattr/type-guarded so a results-less response yields (None, None).
    """
    duration = getattr(getattr(response, "metadata", None), "duration", None)
    alt = _first_alternative(response)
    confidence = getattr(alt, "confidence", None) if alt is not None else None
    return (
        duration if isinstance(duration, (int, float)) else None,
        confidence if isinstance(confidence, (int, float)) else None,
    )


def _display_metrics(response: Any) -> None:
    """Render Duration / Confidence metric cards above a transcript, when available."""
    duration, confidence = _result_metrics(response)
    if duration is None and confidence is None:
        return
    with st.container(horizontal=True):
        if duration is not None:
            st.metric("Duration", f"{duration:.1f} s", border=True)
        if confidence is not None:
            st.metric("Confidence", f"{confidence * 100:.1f}%", border=True)


def _speaker_label(speaker: object) -> object:
    """1-based display label for an integer speaker; non-int speakers pass through."""
    return speaker + 1 if isinstance(speaker, int) else speaker


def _display_transcript(response: Any) -> None:
    """Render one result's metrics then transcript (Markdown-escaped so it shows verbatim).

    With diarization, render one color-highlighted labeled line per speaker run
    (1-based, so the first speaker reads "Speaker 1", colored by speaker index);
    otherwise the flat transcript, or a notice when the response carries no results.
    """
    _display_metrics(response)
    segments = _diarized_segments(response)
    if segments:
        for speaker, text in segments:
            color = (
                _SPEAKER_COLORS[speaker % len(_SPEAKER_COLORS)]
                if isinstance(speaker, int)
                else "gray"
            )
            label = _speaker_label(speaker)
            st.markdown(
                f":{color}-background[**Speaker {label}:**] {_escape_markdown(text)}"
            )
        return
    transcript = _transcript_text(response)
    if transcript is None:
        st.caption(NO_TRANSCRIPT)
        return
    st.markdown(_escape_markdown(transcript))


def _display_json(response: Any) -> None:
    """Render one result's raw JSON (shape-agnostic; serializes any Pydantic response)."""
    st.json(response.model_dump_json())


def _plain_transcript(response: Any) -> str:
    """Plain-text transcript for export: diarized 'Speaker N: ...' lines, else flat text."""
    segments = _diarized_segments(response)
    if segments:
        lines = []
        for speaker, text in segments:
            lines.append(f"Speaker {_speaker_label(speaker)}: {text}")
        return "\n".join(lines)
    return _transcript_text(response) or ""


def _transcript_download(responses: list[tuple[str, Any]]) -> None:
    """A download button for every transcript in the batch (Transcript tab only)."""
    if not responses:
        return
    blocks = [f"{name}\n{_plain_transcript(response)}" for name, response in responses]
    st.download_button(
        "Download transcript",
        "\n\n".join(blocks),
        file_name="transcripts.txt",
        mime="text/plain",
        icon=":material/download:",
    )


def _output_panel(
    responses: list[tuple[str, Any]],
    audio_sources: list[bytes | str | None],
    render: Callable[[Any], None],
) -> None:
    """Render results in a fixed-height panel.

    Empty -> placeholder. Single result -> player pinned above the scroll container.
    Multiple -> one labeled, divided block per result inside the container. A source
    of None (a large upload dropped from playback) renders a caption in place of the
    player.
    """
    if not responses:
        with st.container(height=OUTPUT_HEIGHT, border=True):
            st.caption(PLACEHOLDER)
        return

    if len(responses) == 1:
        (name, response), source = responses[0], audio_sources[0]
        if source is not None:
            _display_audio(name, source)
        else:
            st.caption(PLAYBACK_TOO_LARGE)
        with st.container(height=OUTPUT_HEIGHT, border=True):
            render(response)
        return

    with st.container(height=OUTPUT_HEIGHT, border=True):
        for i, ((name, response), source) in enumerate(
            zip(responses, audio_sources, strict=True)
        ):
            if i:
                st.divider()
            st.markdown(f"**{_escape_markdown(name)}**")
            if source is not None:
                _display_audio(name, source)
            else:
                st.caption(PLAYBACK_TOO_LARGE)
            render(response)


PLACEHOLDER = "Select audio above and run your request to see the response here..."
NO_TRANSCRIPT = "No transcript in this response."
PLAYBACK_TOO_LARGE = "Inline playback unavailable for files over 25 MB."

st.set_page_config(
    page_title="Deepgram Medical Transcription",
    page_icon="🩺",
    layout="wide",
)

st.title("Deepgram Medical Transcription")

api_key = os.environ.get("DEEPGRAM_API_KEY", "")
if not api_key:
    st.warning("Deepgram API key required. Get a free key at https://deepgram.com.")
    api_key = st.text_input(
        "Deepgram API Key",
        type="password",
        label_visibility="collapsed",
    )

tab_upload, tab_record, tab_url = st.tabs(["Upload", "Record", "URL"])

with tab_upload:
    uploaded_files = st.file_uploader(
        "Upload audio files",
        type=_AUDIO_TYPES,
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

with tab_record:
    recording = st.audio_input("Record a dictation", label_visibility="collapsed")

with tab_url:
    url_text = st.text_area(
        "Enter audio file URLs (one per line)",
        placeholder="https://example.com/audio.mp3\nhttps://example.com/another.mp3",
        label_visibility="collapsed",
    )


left_col, right_col = st.columns(2)

with left_col:
    (features_tab,) = st.tabs(["Features"])
    with features_tab:
        with st.form("features", border=False):
            st.selectbox(
                "Language",
                options=list(_LANGUAGES),
                format_func=lambda code: _LANGUAGES[code],
                key="language",
            )
            st.toggle(
                "Smart Format",
                value=DEFAULT_SMART_FORMAT,
                help="Smart Format improves readability by applying additional formatting. When enabled, punctuation and paragraph breaks will be applied as well as formatting of other entities, such as dates, times, and numbers.",
                key="smart_format",
            )
            st.multiselect(
                "Keyterm Prompting",
                options=[],
                accept_new_options=True,
                max_selections=MAX_KEYTERMS,
                placeholder="Add keyterms...",
                help="Boosts recognition of important words or phrases, like names, product terms, or jargon. The model pays extra attention to these; you can include up to 100 keyterms per request.",
                key="keyterms",
            )
            st.toggle(
                "Diarize",
                value=DEFAULT_DIARIZE,
                help="Detects speaker changes and labels turns as Speaker 1, Speaker 2, … in the transcript. Speakers are numbered, not named by role.",
                key="diarize",
            )
            st.toggle(
                "Dictation",
                value=DEFAULT_DICTATION,
                help='Converts spoken formatting commands into characters (e.g. "period" becomes ".", "new paragraph" starts a new line). Automatically enables punctuation.',
                key="dictation",
            )
            st.toggle(
                "Measurements",
                value=DEFAULT_MEASUREMENTS,
                help='Converts spoken measurements into abbreviated units (e.g. "five milligrams" becomes "5 mg").',
                key="measurements",
            )
            st.multiselect(
                "Redact",
                options=list(_REDACT_GROUPS),
                format_func=lambda group: _REDACT_GROUPS[group],
                placeholder="Select information to redact...",
                help="Replaces the selected information with redaction tags in the transcript. For de-identification, use PII (names, locations, IDs). Note: PHI redaction strips clinical content itself (conditions, drugs, injuries) — usually the opposite of what a medical transcript should keep.",
                key="redact",
            )
            has_input = bool(
                uploaded_files or recording is not None or url_text.strip()
            )
            run_clicked = st.form_submit_button(
                "Run",
                type="primary",
                disabled=not api_key or not has_input,
                help=None
                if (api_key and has_input)
                else "Add an API key and an upload, recording, or URL to enable transcription.",
                width="stretch",
            )
        if run_clicked:
            _run(api_key, uploaded_files, recording, url_text)

with right_col:
    responses = st.session_state.get("responses", [])
    audio_sources = st.session_state.get("audio_sources", [])
    # on_change="rerun" makes the tabs stateful so .open reflects the active tab;
    # `is not False` renders on None (non-stateful fallback) or True and skips only the
    # explicitly-hidden tab — so the JSON tab's per-response model_dump_json() is not
    # computed while the Transcript tab is showing.
    tab_transcript, tab_json = st.tabs(["Transcript", "JSON"], on_change="rerun")
    if tab_transcript.open is not False:
        with tab_transcript:
            _transcript_download(responses)
            _output_panel(responses, audio_sources, _display_transcript)
    if tab_json.open is not False:
        with tab_json:
            _output_panel(responses, audio_sources, _display_json)
