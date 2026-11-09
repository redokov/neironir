"""Signed session cookie.

We use ``itsdangerous.URLSafeTimedSerializer`` so we get:

* tamper detection (HMAC-SHA1 over the payload + timestamp),
* built-in TTL via ``max_age``,
* URL-safe encoding (no need for extra JSON / base64 gymnastics).

The cookie value is a JSON string ``{"is_admin": true, "csrf_sid": "...",
"jti": "..."}`` where ``csrf_sid`` is a per-session identifier used by
the CSRF layer (see :mod:`neironir.auth.csrf`) and ``jti`` is a random
session identifier that enables server-side revocation on logout
(spec 007 FR-005, design §4).

Revocation
----------
A process-local, bounded set of revoked jti values is maintained until
the session TTL has elapsed.  No external dependency and no separate
persistence schema are introduced.  Old cookies that do not carry a
``jti`` field remain readable until the migration window closes; new
logout revocations always work because every new session carries a jti.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Key in the cookie payload that marks a logged-in admin session.
SESSION_PAYLOAD_KEY = "is_admin"

# Key in the cookie payload that carries the per-session revocation id.
JTI_PAYLOAD_KEY = "jti"

# Maximum number of entries kept in the revoked-jti registry.  Each
# entry expires after ``session_max_age`` seconds, so the set is bounded
# by the product of (max sessions per TTL window) × TTL.  We use a
# generous upper bound of 4096 to cover realistic concurrent sessions
# in a single-process MVP while preventing unbounded growth.
_MAX_REVOKED_JTI = 4096


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


def new_session_jti() -> str:
    """Return a fresh random jti for the session payload.

    The value is 128 bits of entropy (base64-url-safe, ~22 chars).
    It is only stored in the signed cookie (server-side) and in the
    in-memory revoked-jti set — never logged or returned in a
    response body.
    """
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Bounded process-local revoked-jti registry
# ---------------------------------------------------------------------------
# Design (spec 007 §4): the one-process MVP keeps revoked jti values in
# a module-level dict mapping jti → expiry timestamp.  The registry is
# pruned lazily: on each write or read we evict entries whose expiry has
# passed.  A hard cap of ``_MAX_REVOKED_JTI`` entries prevents unbounded
# growth; if the cap is exceeded the oldest entries are evicted first.
# No external dependency, no persistence schema, no log of raw values.

_revoked_jti: dict[str, float] = {}


def _prune_revoked(now: float | None = None) -> None:
    """Evict expired entries from the revoked-jti registry."""
    ts = time.time() if now is None else now
    expired = [k for k, exp in _revoked_jti.items() if exp <= ts]
    for k in expired:
        del _revoked_jti[k]


def _evict_oldest(count: int) -> None:
    """Evict the ``count`` oldest (earliest-expiry) entries."""
    if not _revoked_jti or count <= 0:
        return
    sorted_keys = sorted(_revoked_jti, key=_revoked_jti.get)  # type: ignore[arg-type]
    for k in sorted_keys[:count]:
        _revoked_jti.pop(k, None)


def revoke_session_jti(jti: str, *, ttl: int) -> None:
    """Mark a session jti as revoked.

    The entry expires after ``ttl`` seconds (the session ``max_age``).
    The registry is bounded: expired entries are pruned first, and if
    the cap is still exceeded the oldest entries are evicted.

    Args:
        jti: The jti value to revoke (must be non-empty).
        ttl: Expiry in seconds, matching the session cookie ``max_age``.
    """
    if not jti:
        return
    ts = time.time()
    _prune_revoked(ts)
    _revoked_jti[jti] = ts + ttl
    if len(_revoked_jti) > _MAX_REVOKED_JTI:
        _evict_oldest(len(_revoked_jti) - _MAX_REVOKED_JTI)


def is_jti_revoked(jti: str) -> bool:
    """Return True if the jti has been revoked and its TTL has not elapsed.

    Prunes expired entries as a side-effect (bounded lazily).
    """
    if not jti:
        return False
    _prune_revoked()
    return jti in _revoked_jti


def _revoked_jti_size() -> int:
    """Return the current number of entries (for tests / diagnostics)."""
    _prune_revoked()
    return len(_revoked_jti)


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
