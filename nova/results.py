"""Deepgram response walkers for the Streamlit UI.

Pure getattr-guarded reads with no streamlit imports, kept separate from the renderer
so they can be unit-tested directly. Speaker values are Deepgram's native 0-based
integers; the +1 display offset lives only in the Streamlit renderer.
"""

from typing import Any


def first_alternative(response: Any) -> Any | None:
    """Return the first channel's first alternative, or None if the response lacks one.

    Pre-recorded calls return a `ListenV1Response` (with `results`); a callback/async
    call would instead yield a `ListenV1AcceptedResponse` that has only `request_id`
    and no `results`. Guard that path plus empty channels/alternatives so callers
    degrade gracefully instead of raising.
    """
    results = getattr(response, "results", None)
    channels = getattr(results, "channels", None) or []
    if not channels:
        return None
    alternatives = getattr(channels[0], "alternatives", None) or []
    return alternatives[0] if alternatives else None


def transcript_text(response: Any) -> str | None:
    """Pull the transcript, or None if the response carries no usable results."""
    alternative = first_alternative(response)
    if alternative is None:
        return None
    return getattr(alternative, "transcript", None)


def diarized_segments(response: Any) -> list[tuple[Any, str]] | None:
    """Group words into consecutive (speaker, text) runs when diarization labeled them.

    Returns None when the response has no per-word integer speaker labels (diarize off,
    or no usable results), so the caller falls back to the flat transcript.
    """
    alternative = first_alternative(response)
    if alternative is None:
        return None
    words = getattr(alternative, "words", None) or []
    if not words or not isinstance(getattr(words[0], "speaker", None), int):
        return None
    segments: list[tuple[Any, list[str]]] = []
    for word in words:
        speaker = getattr(word, "speaker", None)
        token = getattr(word, "punctuated_word", None) or getattr(word, "word", "")
        # A word whose speaker is missing/non-int (rare mid-stream) continues the
        # current run instead of opening a bogus "Speaker None" segment; the words[0]
        # gate guarantees a run already exists by then.
        new_run = not segments or (
            isinstance(speaker, int) and speaker != segments[-1][0]
        )
        if new_run:
            segments.append((speaker, [token]))
        else:
            segments[-1][1].append(token)
    return [(speaker, " ".join(tokens)) for speaker, tokens in segments]
