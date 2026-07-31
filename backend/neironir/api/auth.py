"""Login / logout endpoints.

Implements variant C of the auth flow:

* ``GET  /login`` serves the static ``login.html`` page. We do *not*
  require a CSRF token here (the browser hasn't established a session
  yet) and we do *not* require a session cookie either.
* ``POST /login`` accepts a form-encoded ``username`` / ``password``,
  validates against ``Settings.admin_user`` / ``Settings.admin_password``,
  and on success sets two cookies:

  - ``neironir_session`` — ``HttpOnly``, ``SameSite=Lax``, signed
    (see :mod:`neironir.auth.session`), TTL = ``session_max_age``.
  - ``neironir_csrf`` — *not* ``HttpOnly``, JS-readable; bound to the
    session id inside the signed cookie.

  Both cookies also get the ``Secure`` flag when the request arrived
  over HTTPS (we detect this by ``request.url.scheme == "https"``).
  The login endpoint also enforces ``Origin``/``Referer`` matching the
  request host to deflect trivial CSRF before a session exists.

* ``POST /logout`` requires both ``require_admin_auth`` and ``verify_csrf``;
  it clears both cookies and redirects to ``/login``.

* ``GET /api/v1/auth/whoami`` (optional, useful for debugging) returns
  the current session payload if any.
"""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from neironir.api.dependencies import get_settings
from neironir.auth.csrf import generate_csrf_token, new_csrf_session_id, sign_csrf_value
from neironir.auth.dependencies import (
    get_session_payload,
    require_admin_auth,
    verify_csrf,
)
from neironir.auth.session import SESSION_PAYLOAD_KEY, sign_session_cookie
from neironir.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_secure(request: Request) -> bool:
    """Return True if the request arrived over HTTPS (directly or via proxy)."""
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    return forwarded_proto == "https"


def _set_auth_cookies(
    response: Response,
    *,
    settings: Settings,
    csrf_token: str,
    csrf_sid: str,
    secure: bool,
) -> None:
    """Attach both session and CSRF cookies to a response."""
    session_value = sign_session_cookie(
        {
            SESSION_PAYLOAD_KEY: True,
            "csrf_sid": csrf_sid,
            "user": settings.admin_user,
        },
        secret=settings.session_secret,
    )

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_value,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=sign_csrf_value(csrf_sid, csrf_token, secret=settings.session_secret),
        max_age=settings.session_max_age,
        httponly=False,  # must be readable by JS for the double-submit pattern
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Drop both cookies (used by ``/logout``)."""
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _origin_matches(request: Request) -> bool:
    """Loose check that ``Origin`` or ``Referer`` host matches the request host.

    Browsers send ``Origin`` on POST. When it's absent (some legacy
    browsers / proxies) we fall back to ``Referer``. If both are
    missing, we let the request through — true CSRF is impossible in
    that case for an HTML form, and any attacker controlling the
    browser already has the password.
    """
    host = request.url.netloc
    for header_name in ("origin", "referer"):
        value = request.headers.get(header_name, "")
        if not value:
            continue
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        if parsed.netloc and parsed.netloc != host:
            return False
    return True


def _safe_next_url(request: Request) -> str:
    """Return the ``?next=`` redirect target if it is a safe local path.

    Anything that could steer the browser off this host — absolute
    URLs, protocol-relative ``//host`` URLs, backslash tricks — falls
    back to ``/admin``.  Used by both ``GET`` and ``POST /login``.
    """
    next_url = request.query_params.get("next") or "/admin"
    parsed = urlparse(next_url)
    if (
        parsed.scheme
        or parsed.netloc
        or not next_url.startswith("/")
        or next_url.startswith("//")
        or "\\" in next_url
    ):
        return "/admin"
    return next_url


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/login", include_in_schema=False)
async def get_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Serve ``login.html``.

    If the caller is already authenticated, redirect them to ``/admin``
    (or ``?next=...`` if provided) so refreshing the page is a no-op.
    """
    payload = get_session_payload(request, settings=settings)
    if payload and payload.get(SESSION_PAYLOAD_KEY):
        # Already authenticated — bounce to the post-login destination.
        return RedirectResponse(url=_safe_next_url(request), status_code=status.HTTP_302_FOUND)

    login_path = settings.frontend_path / "login.html"
    if login_path.is_file():
        return FileResponse(login_path)
    # If the static file is missing, return a minimal HTML stub so the
    # backend at least responds with 200 instead of 404.
    return Response(
        "<!doctype html><meta charset='utf-8'><title>Login</title>"
        "<form method='post'><input name='username'><input name='password' type='password'>"
        "<button>Войти</button></form>",
        media_type="text/html",
    )


@router.post("/login")
async def post_login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Validate credentials, mint a session, set cookies, redirect."""
    if not settings.session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "session_secret_not_configured",
                "message": "NEIRONIR_SESSION_SECRET is not set on the server",
            },
        )
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "admin_password_not_configured",
                "message": "NEIRONIR_ADMIN_PASSWORD is not set on the server",
            },
        )

    if not _origin_matches(request):
        # Defence-in-depth: reject POSTs whose Origin / Referer doesn't
        # match our own host. SameSite=Lax cookies already cover most
        # cases, but this is cheap and protects against naive CSRF.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "csrf_origin_check_failed", "message": "Origin mismatch"},
        )

    if username != settings.admin_user or password != settings.admin_password:
        # Use a generic message — never disclose which field was wrong.
        return RedirectResponse(
            url="/login?error=invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    csrf_sid = new_csrf_session_id()
    csrf_token = generate_csrf_token(csrf_sid)

    next_url = _safe_next_url(request)

    response = RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookies(
        response,
        settings=settings,
        csrf_token=csrf_token,
        csrf_sid=csrf_sid,
        secure=_is_secure(request),
    )
    logger.info("admin login succeeded for user %r", settings.admin_user)
    return response


@router.post(
    "/logout",
    dependencies=[Depends(require_admin_auth), Depends(verify_csrf)],
    include_in_schema=False,
)
async def post_logout(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Clear cookies and bounce back to ``/login``."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _clear_auth_cookies(response, settings)
    return response


@router.get("/api/v1/auth/whoami")
async def get_whoami(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Return the current session payload, or ``{"is_admin": False}``."""
    payload = get_session_payload(request, settings=settings)
    if payload and payload.get(SESSION_PAYLOAD_KEY):
        return {"is_admin": True, "user": payload.get("user")}
    return {"is_admin": False}


__all__ = ["router"]
