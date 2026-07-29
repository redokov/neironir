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

    def __init__(self, app: ASGIApp, *, cookie_name: str, secret: str, max_age: int) -> None:
        super().__init__(app)
        self._cookie_name = cookie_name
        self._secret = secret
        self._max_age = max_age

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method.upper() == "GET" and request.url.path in {"/admin", "/admin/"}:
            payload = self._read_session(request)
            if not (payload and payload.get(SESSION_PAYLOAD_KEY)):
                target = f"/login?next={request.url.path}"
                return RedirectResponse(url=target, status_code=302)
        return await call_next(request)

    def _read_session(self, request: Request) -> dict[str, Any] | None:
        raw = request.cookies.get(self._cookie_name)
        if not raw:
            return None
        try:
            return read_session_cookie(raw, secret=self._secret, max_age=self._max_age)
        except (SessionExpiredError, SessionInvalidError):
            return None


__all__ = ["AdminUIAuthMiddleware"]