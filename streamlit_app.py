import io
import os
import re
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import streamlit as st
from deepgram import DeepgramClient
from dotenv import load_dotenv

load_dotenv()

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_RECORDING_SECONDS = 10 * 60  # 10 minutes
MAX_UPLOADS = 100
MAX_CONCURRENCY = 5
MAX_KEYTERMS = 100  # Deepgram keyterm prompting limit
MAX_PLAYBACK_BYTES = 25 * 1024 * 1024  # larger uploads skip inline playback (memory)
OUTPUT_HEIGHT = 400  # fixed height (px) of the Transcript/JSON output panel

_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".ogg")
_AUDIO_TYPES = [ext.lstrip(".") for ext in _AUDIO_EXTENSIONS]
_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}

_TRANSCRIBE_OPTS = dict(
    model="nova-3-medical",
)

# Nova-3 Medical supports English variants only.
_LANGUAGES = {
    "en": "English",
    "en-US": "English (US)",
    "en-AU": "English (Australia)",
    "en-CA": "English (Canada)",
    "en-GB": "English (UK)",
    "en-IE": "English (Ireland)",
    "en-IN": "English (India)",
    "en-NZ": "English (New Zealand)",
}

# Feature defaults — shared by the widgets and _feature_opts so they cannot drift.
DEFAULT_LANGUAGE = next(iter(_LANGUAGES))
DEFAULT_SMART_FORMAT = True
DEFAULT_PROFANITY_FILTER = False
DEFAULT_NUMERALS = False

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


def _transcribe_batch(
    api_key: str,
    items: list[tuple[str, dict[str, object]]],
    method: str,
    keyterms: list[str] | None = None,
    language: str | None = None,
    smart_format: bool = DEFAULT_SMART_FORMAT,
    profanity_filter: bool = DEFAULT_PROFANITY_FILTER,
    numerals: bool = DEFAULT_NUMERALS,
):
    """Transcribe a batch of audio sources in parallel; preserve input order in results."""
    client = DeepgramClient(api_key=api_key)
    transcribe = getattr(client.listen.v1.media, method)
    opts = {
        **_TRANSCRIBE_OPTS,
        "smart_format": smart_format,
        "profanity_filter": profanity_filter,
        "numerals": numerals,
        **({"keyterm": keyterms} if keyterms else {}),
        **({"language": language} if language else {}),
    }
    total = len(items)
    progress = st.progress(0.0, f"Transcribing 0/{total}...")

    sources = {
        i: _playback_source(kwargs.get("request", kwargs.get("url")))
        for i, (_, kwargs) in enumerate(items)
    }
    indexed: list[tuple[int, str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = {
            executor.submit(transcribe, **kwargs, **opts): (i, label)
            for i, (label, kwargs) in enumerate(items)
        }
        for done, future in enumerate(as_completed(futures), start=1):
            i, label = futures[future]
            progress.progress(done / total, f"Transcribing {done}/{total}...")
            try:
                indexed.append((i, label, future.result()))
            except Exception as e:
                st.error(f"Transcription failed for {label}: {e}")

    progress.empty()

    # Always overwrite (even when empty) so a fully-failed run clears stale results.
    indexed.sort(key=lambda r: r[0])
    st.session_state["responses"] = [(label, resp) for _, label, resp in indexed]
    st.session_state["audio_sources"] = [sources[i] for i, _, _ in indexed]


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
        "profanity_filter": st.session_state.get(
            "profanity_filter", DEFAULT_PROFANITY_FILTER
        ),
        "numerals": st.session_state.get("numerals", DEFAULT_NUMERALS),
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
            st.error(f"Skipped (exceeds 2 GB): {', '.join(oversized)}")
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
            no_ext = [
                u
                for u in valid
                if not u.split("?")[0].lower().endswith(_AUDIO_EXTENSIONS)
            ]
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


def _display_transcript(response: Any) -> None:
    """Render one result's transcript (Markdown-escaped so it shows verbatim)."""
    transcript = response.results.channels[0].alternatives[0].transcript
    st.markdown(_escape_markdown(transcript))


def _display_json(response: Any) -> None:
    """Render one result's raw JSON."""
    st.json(response.model_dump_json())


def _output_panel(
    responses: list[tuple[str, Any]],
    audio_sources: list[bytes | str | None],
    render: Callable[[Any], None],
) -> None:
    """Render results in a fixed-height panel.

    Empty -> placeholder. Single result -> player pinned above the scroll container.
    Multiple -> one labeled, divided block per result inside the container. A source
    of None (e.g. a large upload dropped from playback) renders no player.
    """
    if not responses:
        with st.container(height=OUTPUT_HEIGHT, border=True):
            st.caption(PLACEHOLDER)
        return

    if len(responses) == 1:
        (name, response), source = responses[0], audio_sources[0]
        if source is not None:
            _display_audio(name, source)
        with st.container(height=OUTPUT_HEIGHT, border=True):
            render(response)
        return

    with st.container(height=OUTPUT_HEIGHT, border=True):
        for i, ((name, response), source) in enumerate(zip(responses, audio_sources)):
            if i:
                st.divider()
            st.markdown(f"**{_escape_markdown(name)}**")
            if source is not None:
                _display_audio(name, source)
            render(response)


PLACEHOLDER = "Select audio above and run your request to see the response here..."

st.title("Nova Medical Pipeline")

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
            "Profanity Filter",
            value=DEFAULT_PROFANITY_FILTER,
            help="Indicates whether to remove profanity from the transcript.",
            key="profanity_filter",
        )
        st.toggle(
            "Numerals",
            value=DEFAULT_NUMERALS,
            help='Converts numbers from written format to numerical format (e.g., "nine hundred" becomes "900").',
            key="numerals",
        )
        if st.button(
            "Run",
            disabled=not api_key
            or not (uploaded_files or recording is not None or url_text.strip()),
            type="primary",
            use_container_width=True,
            key="run",
        ):
            _run(api_key, uploaded_files, recording, url_text)

with right_col:
    responses = st.session_state.get("responses", [])
    audio_sources = st.session_state.get("audio_sources", [])
    tab_transcript, tab_json = st.tabs(["Transcript", "JSON"])
    with tab_transcript:
        _output_panel(responses, audio_sources, _display_transcript)
    with tab_json:
        _output_panel(responses, audio_sources, _display_json)
