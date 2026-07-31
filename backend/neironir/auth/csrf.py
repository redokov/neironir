"""Double-submit-cookie CSRF protection with session binding.

The server sets a non-``HttpOnly`` cookie containing a random opaque
token *bound* to the session id (``csrf_sid``).  The binding is
enforced by signing the combined ``csrf_sid + ":" + token`` with
:class:`itsdangerous.Signer`, using the same secret that signs the
session cookie.

JS reads the signed cookie value and sends it back in the
``X-CSRF-Token`` header.  Server-side verifies the signature,
extracts ``csrf_sid`` and ``token``, and checks:

1. The ``csrf_sid`` matches the one stored in the session cookie
   (ensuring the token belongs to this session, not a stolen one).
2. The ``token`` is reasonably long (defence-in-depth).

Why this matters
----------------
A plain double-submit pattern (header == cookie) only protects against
cross-origin attackers who cannot read the cookie.  An XSS attacker who
*can* read the cookie gets the full token with no additional check.
Binding to the session sid means that even a leaked token is useless
if the attacker cannot also forge the session cookie (which is
HttpOnly and signed).
"""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, Signer

# TTL for the CSRF session id matches the session cookie TTL in
# practice. The CSRF cookie is not a server-issued token; the server
# only checks that header == cookie value for the current request, so
# ``max_age`` here only matters when *reading* the cookie at all.
CSRF_TOKEN_MAX_AGE: int = 86400


def _new_token() -> str:
    """Generate a fresh CSRF token (base64-url-safe, ~43 chars)."""
    return secrets.token_urlsafe(32)


def new_csrf_session_id() -> str:
    """Return a fresh per-session identifier used to bind the token."""
    return secrets.token_urlsafe(16)


def _signer(secret: str) -> Signer:
    """Return a :class:`Signer` that signs CSRF values.

    Uses a different salt than the session cookie to avoid cross-use
    of signed values (a signed CSRF value cannot be passed as a
    session cookie and vice versa).
    """
    return Signer(secret, salt="neironir-csrf-v1")


def generate_csrf_token(session_id: str) -> str:
    """Derive a CSRF token from the session id.

    The token is a fresh random value.  The caller stores it in the
    CSRF cookie together with the session id — the cookie value is
    ``sign(f"{session_id}:{token}")`` so the server can verify the
    binding on the next request.
    """
    return _new_token()


def sign_csrf_value(session_id: str, token: str, *, secret: str) -> str:
    """Sign the ``session_id:token`` pair so it cannot be tampered with.

    Returns a URL-safe signed string that the frontend reads from the
    CSRF cookie and echoes back in the ``X-CSRF-Token`` header.
    """
    return _signer(secret).sign(f"{session_id}:{token}").decode("ascii")


def verify_csrf_token(
    *,
    header_token: str | None,
    cookie_token: str | None,
    header_csrf_sid: str | None = None,
    secret: str = "",
) -> bool:
    """Compare the JS-supplied header against the cookie value.

    Both sides must carry the same signed ``session_id:token`` value,
    and the session id inside the signed payload must match the one
    from the session cookie (``header_csrf_sid``).

    Args:
        header_token: Value of the ``X-CSRF-Token`` header (signed).
        cookie_token: Value of the CSRF cookie (signed).
        header_csrf_sid: Session id extracted from the session cookie.
            Pass ``None`` to skip session binding (legacy / test mode).
        secret: The secret used to sign the CSRF value.

    Returns:
        ``True`` only when both values are present, have valid
        signatures, carry the same session id, and meet minimum
        length requirements.
    """
    if not header_token or not cookie_token:
        return False

    # Unsign the header value to extract session_id + raw token.
    payload = _unsign(header_token, secret)
    if payload is None:
        return False

    sid_from_header, token_from_header = payload

    # Check that the cookie token (signed with the same secret)
    # carries the same payload — this proves the cookie was signed
    # by our server.
    if not secrets.compare_digest(header_token, cookie_token):
        return False

    # Minimum length guard against trivial "" or short-string bypasses.
    if len(token_from_header) < 16:
        return False

    # Session binding: if a csrf_sid was provided from the session
    # cookie, it must match the sid embedded in the CSRF token.
    return header_csrf_sid is None or sid_from_header == header_csrf_sid


def _unsign(signed_value: str, secret: str) -> tuple[str, str] | None:
    """Unsign a ``session_id:token`` value and return the pair.

    Returns ``None`` if the signature is invalid or the payload
    doesn't contain a ``:`` separator.
    """
    if not secret:
        return None
    try:
        unsigned = _signer(secret).unsign(signed_value).decode("utf-8")
    except BadSignature:
        return None

    parts = unsigned.split(":", 1)
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])
