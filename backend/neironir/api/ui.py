"""Routes that serve the browser-facing page.

The full UI is shipped in phase 4. For phase 3 the route returns a
placeholder HTML page so the API end-to-end (upload, status, download)
can be exercised in a browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from neironir.api.dependencies import get_settings
from neironir.config import Settings

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index(
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve the placeholder ``index.html`` from the frontend directory."""
    return FileResponse(settings.frontend_path / "index.html")


__all__ = ["router"]
