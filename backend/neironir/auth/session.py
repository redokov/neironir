"""Signed session cookie.

We use ``itsdangerous.URLSafeTimedSerializer`` so we get:

* tamper detection (HMAC-SHA1 over the payload + timestamp),
* built-in TTL via ``max_age``,
* URL-safe encoding (no need for extra JSON / base64 gymnastics).

The cookie value is a JSON string ``{"is_admin": true, "csrf_sid": "..."}``
where ``csrf_sid`` is a per-session identifier used by the CSRF layer
(see :mod:`neironir.auth.csrf`).
"""

from __future__ import annotations

from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Key in the cookie payload that marks a logged-in admin session.
SESSION_PAYLOAD_KEY = "is_admin"


class SessionExpiredError(Exception):
    """Raised when the cookie's TTL has elapsed."""


class SessionInvalidError(Exception):
    """Raised when the cookie signature or payload is invalid."""


def _serializer(secret: str) -> URLSafeTimedSerializer:
    """Build a serializer with a stable salt.

    Using a fixed salt keeps all session cookies in the same keyspace —
    rotating the salt would invalidate every existing session, which is
    exactly what we want when ``session_secret`` itself rotates.
    """
    return URLSafeTimedSerializer(secret, salt="neironir-session-v1")


def sign_session_cookie(payload: dict[str, Any], *, secret: str) -> str:
    """Serialize and sign the payload.

    The payload is JSON-encoded into the cookie value; ``itsdangerous``
    then appends a timestamp + HMAC. The returned string is safe to
    place inside a ``Set-Cookie`` header.
    """
    if not secret:
        raise SessionInvalidError("session secret is not configured")
    return _serializer(secret).dumps(payload)


def read_session_cookie(value: str, *, secret: str, max_age: int) -> dict[str, Any]:
    """Verify and decode a cookie value.

    Raises :class:`SessionExpiredError` if the TTL has elapsed and
    :class:`SessionInvalidError` for any other tampering.
    """
    if not secret:
        raise SessionInvalidError("session secret is not configured")
    try:
        data: dict[str, Any] = _serializer(secret).loads(value, max_age=max_age)
    except SignatureExpired as exc:
        raise SessionExpiredError("session cookie expired") from exc
    except BadSignature as exc:
        raise SessionInvalidError("session cookie signature is invalid") from exc

    if not isinstance(data, dict):
        raise SessionInvalidError("session cookie payload must be an object")
    return data
