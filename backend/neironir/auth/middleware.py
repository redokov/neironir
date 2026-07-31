"""ASGI middleware that protects the admin UI HTML page.

The JSON API under ``/api/v1/admin/*`` and ``/api/v1/rules/*`` is
protected per-endpoint via ``Depends(require_admin_auth)``. The
admin **page** (``GET /admin``) is a static HTML file served by
``ui.router`` and is *not* an endpoint we can decorate.

Instead of refactoring ``ui.router`` (which would couple it to the
auth subsystem), we wrap the whole ASGI app with this small
middleware that:

* For ``GET /admin`` — if the caller has no valid admin cookie,
  reply with ``302 → /login?next=/admin`` and short-circuit the
  downstream app.

* For ``POST /logout`` — already protected by the route-level
  ``Depends``; the middleware leaves it alone.

* For everything else — pass through untouched.

Configuration
-------------

The middleware reads auth settings from ``request.app.state.settings``
on **every** request instead of capturing them at construction time.
This matters for two reasons:

1. ``create_app()`` validates that ``session_secret`` is set before
   mounting the middleware, but the actual secret / cookie name live
   in ``app.state.settings`` — the same object the login route uses
   through ``Depends(get_settings)``.  Capturing the secret at
   construction would silently desync the middleware from the login
   route when settings are overridden (e.g. in tests via
   ``app.dependency_overrides[get_settings]``), producing a
   ``/admin → /login → /admin`` redirect loop because the login page
   sees a valid cookie while the middleware does not.

2. Reading per-request keeps the middleware consistent with any
   runtime settings changes.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp

from neironir.auth.session import (
    SESSION_PAYLOAD_KEY,
    SessionExpiredError,
    SessionInvalidError,
    read_session_cookie,
)


class AdminUIAuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated ``GET /admin`` to ``/login``."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() == "GET" and request.url.path in {"/admin", "/admin/"}:
            settings = getattr(request.app.state, "settings", None)
            if settings is not None and bool(getattr(settings, "session_secret", "")):
                payload = self._read_session(request, settings)
                if not (payload and payload.get(SESSION_PAYLOAD_KEY)):
                    target = f"/login?next={request.url.path}"
                    return RedirectResponse(url=target, status_code=302)
        return await call_next(request)

    @staticmethod
    def _read_session(request: Request, settings: Any) -> dict[str, Any] | None:
        raw = request.cookies.get(settings.session_cookie_name)
        if not raw:
            return None
        try:
            return read_session_cookie(
                raw,
                secret=settings.session_secret,
                max_age=settings.session_max_age,
            )
        except (SessionExpiredError, SessionInvalidError):
            return None


__all__ = ["AdminUIAuthMiddleware"]
