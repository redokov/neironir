"""FastAPI ``Depends`` helpers for protected endpoints.

Two guards are exported:

* :func:`require_admin_auth` — use as ``Depends`` on every endpoint
  that mutates admin/rules state. Reads the ``neironir_session``
  cookie, verifies the signature + TTL, and checks ``is_admin=True``.

* :func:`verify_csrf` — use as ``Depends`` on every non-GET endpoint
  behind ``require_admin_auth``. Compares the ``X-CSRF-Token`` header
  against the ``neironir_csrf`` cookie.

Both guards return ``None`` on success and raise ``HTTPException`` on
failure. They share state via the request scope, so a router that
needs both can list them in the ``dependencies=[...]`` argument of
``APIRouter`` or per-endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from neironir.api.dependencies import get_settings  # re-used
from neironir.auth.csrf import verify_csrf_token
from neironir.auth.session import (
    SESSION_PAYLOAD_KEY,
    SessionExpiredError,
    SessionInvalidError,
    read_session_cookie,
)
from neironir.config import Settings

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthenticated", "message": detail},
        headers={"WWW-Authenticate": "Cookie"},
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "forbidden", "message": detail},
    )


def get_session_payload(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)] | None = None,
) -> dict[str, Any] | None:
    """Return the decoded session payload or ``None`` if missing/invalid.

    Never raises — useful for endpoints that have *optional* admin
    privileges (e.g. ``GET /api/v1/health`` doesn't care, but a future
    ``GET /api/v1/admin/whoami`` would).

    When called from a route handler, ``settings`` is automatically
    injected by FastAPI via ``Depends``. When called directly (e.g. from
    middleware), pass ``None`` and the function falls back to
    ``request.app.state.settings``.
    """
    if settings is None:
        settings = getattr(request.app.state, "settings", None)
        if settings is None:
            return None

    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        return read_session_cookie(
            raw, secret=settings.session_secret, max_age=settings.session_max_age
        )
    except (SessionExpiredError, SessionInvalidError):
        return None


def require_admin_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Ensure the caller has a valid admin session cookie.

    Returns the decoded session payload on success. Raises 401 otherwise.
    """
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise _unauthorized("admin session cookie is missing")

    try:
        payload = read_session_cookie(
            raw, secret=settings.session_secret, max_age=settings.session_max_age
        )
    except SessionExpiredError as exc:
        raise _unauthorized(str(exc)) from exc
    except SessionInvalidError as exc:
        raise _unauthorized(str(exc)) from exc

    if not payload.get(SESSION_PAYLOAD_KEY):
        raise _unauthorized("admin session is not authorised")

    return payload


def verify_csrf(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Ensure the request carries a matching CSRF token.

    Skipped for safe methods (GET / HEAD / OPTIONS) per RFC 7231. For
    unsafe methods:

    1. Reads the session cookie to extract ``csrf_sid``.
    2. Unsings the ``X-CSRF-Token`` header and verifies the
       signature is valid.
    3. Checks the ``csrf_sid`` embedded in the CSRF token matches
       the one from the session cookie (binding).

    Raises 403 on any mismatch.
    """
    if request.method.upper() not in _UNSAFE_METHODS:
        return

    header_token = request.headers.get(settings.csrf_header_name)
    cookie_token = request.cookies.get(settings.csrf_cookie_name)

    # Extract csrf_sid from the session cookie to verify binding.
    csrf_sid = _csrf_sid_from_session(request, settings)

    if not verify_csrf_token(
        header_token=header_token,
        cookie_token=cookie_token,
        header_csrf_sid=csrf_sid,
        secret=settings.session_secret,
    ):
        raise _forbidden("CSRF token missing or mismatched")


def _csrf_sid_from_session(request: Request, settings: Settings) -> str | None:
    """Extract ``csrf_sid`` from the session cookie, if present."""
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        payload = read_session_cookie(
            raw, secret=settings.session_secret, max_age=settings.session_max_age
        )
    except (SessionExpiredError, SessionInvalidError):
        return None
    return payload.get("csrf_sid")


__all__ = [
    "get_session_payload",
    "require_admin_auth",
    "verify_csrf",
]
