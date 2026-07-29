"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from neironir.admin.router import router as admin_router
from neironir.api import jobs, rules, ui
from neironir.api.auth import router as auth_router
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.auth.middleware import AdminUIAuthMiddleware
from neironir.auth.max_body_size import MaxBodySizeMiddleware
from neironir.config import Settings


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    app = FastAPI(title="neironir", version="0.0.1")

    # Keep the health endpoint from phase 0/2. The dependency injection
    # for the rest of the app is wired via ``Depends`` so the route
    # signature does not need to change.
    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Auth router (login / logout / whoami) is mounted BEFORE the
    # admin / rules routers so the login page itself never requires
    # a session. CSRF verification is skipped for GET /login by
    # design (no session exists yet).
    app.include_router(auth_router)

    app.include_router(ui.router)
    app.include_router(jobs.router)
    app.include_router(jobs.meta_router)
    app.include_router(rules.router)
    app.include_router(admin_router)

    # Serve the frontend's static assets (CSS, JS). The ``index.html``
    # itself is served by ``ui.router`` at ``GET /`` so it can be
    # resolved relative to the configured ``frontend_dir``.
    settings = get_settings()

    # Validate authentication configuration: if the session secret is
    # set (auth middleware is active), the admin password must be
    # non-empty.  If you must disable authentication entirely, set
    # ``NEIRONIR_SESSION_SECRET`` to empty — admin/rules endpoints
    # become unreachable.
    if settings.session_secret and not settings.admin_password:
        raise ValueError(
            "NEIRONIR_ADMIN_PASSWORD must be a non-empty string when "
            "NEIRONIR_SESSION_SECRET is configured. To disable "
            "authentication, set NEIRONIR_SESSION_SECRET to empty."
        )

    # Store settings on app.state so the admin-UI middleware and the
    # CSRF dependency can read the configured cookie names / secret
    # without re-parsing the environment on every request.
    app.state.settings = settings

    # Reject oversized uploads before FastAPI parses the request body.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_file_size)

    # Guard ``GET /admin`` so an unauthenticated browser session is
    # bounced to /login instead of getting a blank dashboard. JSON
    # endpoints under /api/v1/admin and /api/v1/rules are protected
    # via ``Depends(require_admin_auth)`` on the routers themselves.
    if settings.session_secret:
        app.add_middleware(
            AdminUIAuthMiddleware,
            cookie_name=settings.session_cookie_name,
            secret=settings.session_secret,
            max_age=settings.session_max_age,
        )

    # Security headers middleware — applied to every response.
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'"
        )
        return response

    _mount_static(app, settings)

    # Touch the dependency factories so misconfiguration (e.g. an
    # invalid privacy_filter_mode) surfaces at startup time rather than
    # on the first request. Storage construction is cheap.
    get_storage(settings=settings)
    get_privacy(settings=settings)

    # Honour the configured log level. ``logging.basicConfig`` is only
    # applied if the application has not already configured logging —
    # uvicorn and pytest typically do it for us.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level)

    return app


def _mount_static(app: FastAPI, settings: Settings) -> None:
    """Mount the frontend static directory if it exists.

    The directory may be absent during isolated backend test runs
    (e.g. when running unit tests in CI). In that case we skip the
    mount rather than fail application construction.
    """
    frontend_dir = settings.frontend_path
    if frontend_dir.is_dir():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


app = create_app()
