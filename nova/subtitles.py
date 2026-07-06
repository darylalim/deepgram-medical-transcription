"""Build SRT subtitles from a Deepgram response, independent of Streamlit.

Pure getattr-guarded reads with no streamlit import, mirroring `nova.results`, so the
builder is unit-testable on its own. Per-word `start`/`end` timestamps drive the cue
timings; consecutive words are grouped into a cue until a speaker change, a
sentence-ending token, or a max word-count / duration cap. This is where the
"timestamps on diarized turns" value lives — the on-screen transcript stays untouched.
Speaker numbers follow the renderer's 1-based display convention.
"""

from typing import Any

from nova.results import first_alternative

_SENTENCE_END = (".", "?", "!")
MAX_CUE_WORDS = 12  # cap cue length so subtitle lines stay readable
MAX_CUE_SECONDS = 6.0  # cap cue duration for the same reason


def _format_timestamp(seconds: float) -> str:
    """Format a second offset as an SRT timestamp ``HH:MM:SS,mmm``."""
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, millis = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _cues(alternative: Any) -> list[tuple[float, float, Any, str]]:
    """Group an alternative's words into (start, end, speaker, text) subtitle cues.

    A cue flushes before a word that changes speaker, follows a sentence-ending token,
    or would exceed the word/duration caps. Words without numeric timing are skipped
    (they cannot anchor a cue); a non-integer speaker is carried as the run's label.
    """
    words = getattr(alternative, "words", None) or []
    cues: list[tuple[float, float, Any, str]] = []
    tokens: list[str] = []
    speaker: Any = None
    start: float = 0.0
    end: float = 0.0
    for word in words:
        w_start = getattr(word, "start", None)
        w_end = getattr(word, "end", None)
        if not isinstance(w_start, (int, float)) or not isinstance(w_end, (int, float)):
            continue
        w_speaker = getattr(word, "speaker", None)
        token = getattr(word, "punctuated_word", None) or getattr(word, "word", "")
        speaker_change = (
            isinstance(w_speaker, int)
            and isinstance(speaker, int)
            and w_speaker != speaker
        )
        capped = bool(tokens) and (
            len(tokens) >= MAX_CUE_WORDS or w_end - start > MAX_CUE_SECONDS
        )
        sentence_break = bool(tokens) and tokens[-1].endswith(_SENTENCE_END)
        if tokens and (speaker_change or capped or sentence_break):
            cues.append((start, end, speaker, " ".join(tokens)))
            tokens = []
        if not tokens:
            start = w_start
            speaker = w_speaker if isinstance(w_speaker, int) else None
        tokens.append(token)
        end = w_end
    if tokens:
        cues.append((start, end, speaker, " ".join(tokens)))
    return cues


def to_srt(response: Any) -> str:
    """Render a Deepgram response as SRT subtitle text; ``""`` when it carries no cues.

    Diarized cues are prefixed ``Speaker N:`` (1-based, matching the on-screen
    renderer); non-diarized cues carry just the text.
    """
    alternative = first_alternative(response)
    if alternative is None:
        return ""
    blocks = []
    for index, (start, end, speaker, text) in enumerate(_cues(alternative), start=1):
        body = f"Speaker {speaker + 1}: {text}" if isinstance(speaker, int) else text
        stamp = f"{_format_timestamp(start)} --> {_format_timestamp(end)}"
        blocks.append(f"{index}\n{stamp}\n{body}")
    return "\n\n".join(blocks)
