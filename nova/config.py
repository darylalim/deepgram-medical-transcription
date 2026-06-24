"""Transcription constants — the single source of truth for the Streamlit UI."""

MODEL = "nova-3-medical"

# Nova-3 Medical supports English variants only.
LANGUAGES = {
    "en": "English",
    "en-US": "English (US)",
    "en-AU": "English (Australia)",
    "en-CA": "English (Canada)",
    "en-GB": "English (UK)",
    "en-IE": "English (Ireland)",
    "en-IN": "English (India)",
    "en-NZ": "English (New Zealand)",
}

# Feature defaults — shared by the UI widgets and option builders so they cannot drift.
DEFAULT_LANGUAGE = next(iter(LANGUAGES))
DEFAULT_SMART_FORMAT = True
DEFAULT_DICTATION = False
DEFAULT_MEASUREMENTS = False
DEFAULT_DIARIZE = False

# Redaction groups (Deepgram `redact` values) -> display labels.
# PII (de-identification) is listed first; PHI strips clinical content itself, so it
# is labeled to flag that trade-off in a medical workflow.
REDACT_GROUPS = {
    "pii": "PII — de-identify (names, locations, IDs)",
    "phi": "PHI — removes clinical content (conditions, drugs, injuries)",
    "pci": "PCI (card numbers)",
    "numbers": "Numbers",
}

MAX_KEYTERMS = 100  # client-side cap; Deepgram's real limit is 500 tokens/request
MAX_UPLOADS = 100
MAX_CONCURRENCY = 5
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GiB

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".ogg")


def has_audio_extension(url_or_name: str) -> bool:
    """True when the path (query string stripped) ends in a recognized audio extension."""
    return url_or_name.split("?")[0].lower().endswith(AUDIO_EXTENSIONS)
