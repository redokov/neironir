"""Static Bearer API-key helpers for the machine-to-machine (M2M) access.

Three pure helpers used by :func:`neironir.auth.dependencies.
require_documents_auth`:

* :func:`parse_api_keys` — split the ``NEIRONIR_API_KEYS`` env string
  into a normalised key set (comma-separated, whitespace stripped,
  empty entries dropped).
* :func:`is_valid_api_key` — constant-time membership check
  (:func:`secrets.compare_digest` per key, no timing leak).
* :func:`get_bearer_token` — extract the token from the
  ``Authorization: Bearer <token>`` header.

Key values are never logged: callers must not include them in
messages, logs or responses (NFR-001).
"""

from __future__ import annotations

import secrets

from fastapi import Request


def parse_api_keys(raw: str) -> frozenset[str]:
    """Split comma-separated, strip whitespace, drop empties.

    An empty (or all-whitespace) ``raw`` yields an empty set, which
    means M2M access is disabled (NFR-004) while the UI keeps working.
    """
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_valid_api_key(token: str, keys: frozenset[str]) -> bool:
    """Constant-time comparison (``secrets.compare_digest`` per key).

    Every configured key is compared — no early exit on length or on
    a match — so a timing side channel cannot reveal how close a
    candidate token is to a real key (NFR-001).
    """
    if not token or not keys:
        return False
    return any(secrets.compare_digest(token, key) for key in keys)


def get_bearer_token(request: Request) -> str | None:
    """Return the token from ``Authorization: Bearer <token>`` or None.

    Returns the sentinel ``""`` (empty string) for a malformed scheme
    (e.g. ``Basic xyz``, bare ``Bearer``, empty token). Returns ``None``
    only when the ``Authorization`` header is absent entirely.
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    token = parts[1].strip()
    return token or ""


__all__ = [
    "get_bearer_token",
    "is_valid_api_key",
    "parse_api_keys",
]
