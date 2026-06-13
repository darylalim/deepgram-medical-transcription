"""Environment-derived configuration, loaded from the same `.env` as the Streamlit app.

Every value is read fresh from `os.environ` on each call so tests can monkeypatch the
environment without reimporting the app. `GLOBAL_MAX_CONCURRENCY` is the exception — the
process-global semaphore in `api.main` is sized once at import.
"""

import os

from dotenv import load_dotenv

from nova.config import MAX_CONCURRENCY, MAX_FILE_SIZE

load_dotenv()

# Hosts that keep the API off the network. Note 0.0.0.0 is NOT loopback (binds all
# interfaces), so it intentionally trips the fail-closed startup check in api.main.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Default request-body budget: one maximal file plus multipart-framing headroom. Because
# file bytes are materialized for the SDK, this doubles as a per-request RAM bound, which
# is why bulk multipart is unsupported and URL batches are the sanctioned bulk path.
_REQUEST_HEADROOM = 16 * 1024 * 1024


def deepgram_api_key() -> str:
    return os.environ.get("DEEPGRAM_API_KEY", "")


def auth_tokens() -> list[str]:
    """Comma-separated bearer tokens; each consumer gets its own revocable token."""
    raw = os.environ.get("API_AUTH_TOKENS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def api_host() -> str:
    return os.environ.get("API_HOST", "127.0.0.1")


def is_loopback() -> bool:
    return api_host() in _LOOPBACK_HOSTS


def max_request_bytes() -> int:
    return int(
        os.environ.get("MAX_REQUEST_BYTES", str(MAX_FILE_SIZE + _REQUEST_HEADROOM))
    )


def deepgram_timeout_seconds() -> int:
    """Upstream per-call timeout; mainly reclaims semaphore slots from hung calls."""
    return int(os.environ.get("DEEPGRAM_TIMEOUT_SECONDS", "600"))


def global_max_concurrency() -> int:
    return int(os.environ.get("GLOBAL_MAX_CONCURRENCY", str(MAX_CONCURRENCY)))
