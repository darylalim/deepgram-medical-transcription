"""Bearer-token auth dependency, always required (even on loopback)."""

import secrets

from fastapi import Header

from api import settings
from api.schemas import ApiError

_BEARER_PREFIX = "Bearer "
_WWW_AUTH = {"WWW-Authenticate": "Bearer"}


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Reject the request unless a configured bearer token is presented.

    503 when the server has no tokens configured (fail-closed), 401 for a missing or
    invalid token. Constant-time comparison avoids leaking token bytes via timing.
    """
    tokens = settings.auth_tokens()
    if not tokens:
        raise ApiError(
            503,
            "not_configured",
            "missing_auth_tokens",
            "Server has no API_AUTH_TOKENS configured.",
        )
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise ApiError(
            401,
            "unauthorized",
            "missing_token",
            "Missing bearer token.",
            headers=_WWW_AUTH,
        )
    presented = authorization[len(_BEARER_PREFIX) :]
    # Materialize all comparisons (no short-circuit) so total time does not reveal which
    # configured token matched.
    matches = [secrets.compare_digest(presented, token) for token in tokens]
    if not any(matches):
        raise ApiError(
            401,
            "unauthorized",
            "invalid_token",
            "Invalid bearer token.",
            headers=_WWW_AUTH,
        )
