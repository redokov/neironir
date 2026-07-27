"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from neironir.api import jobs, ui
from neironir.api.dependencies import get_privacy, get_settings, get_storage
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

    app.include_router(ui.router)
    app.include_router(jobs.router)

    # Serve the frontend's static assets (CSS, JS). The ``index.html``
    # itself is served by ``ui.router`` at ``GET /`` so it can be
    # resolved relative to the configured ``frontend_dir``.
    settings = get_settings()
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
