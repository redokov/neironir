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
    """Constant-time membership check over the **whole** key set.

    T15 (#20): ``any(genexpr)`` short-circuits at the first match, so the
    total verification time depends on *which* key matched — an attacker
    measuring latency can binary-search the key order.  The result is
    accumulated with ``|`` over every key so all comparisons always run
    and the timing profile is independent of the matching position.
    """
    if not token or not keys:
        return False
    result = 0
    for key in keys:
        # ``==`` keeps the compare_digest contract (bool), ``|`` never
        # short-circuits — unlike ``or``.
        result |= 1 if secrets.compare_digest(token, key) else 0
    return bool(result)


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
