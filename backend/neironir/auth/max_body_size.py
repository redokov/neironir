"""ASGI middleware that rejects oversized request bodies early.

FastAPI's ``UploadFile`` reads the entire request body into memory before
the route handler runs, so a ``Content-Length: 100MB`` request would
consume 100 MB of RAM before the pipeline's ``max_file_size`` check
kicks in.  This middleware reads the ``Content-Length`` header **before**
the downstream app processes the body and returns ``413 Payload Too Large``
if the declared size exceeds the limit.

Caveats
-------

* Relies on the ``Content-Length`` header.  Chunked transfer encoding
  (no ``Content-Length``) is not caught here — the size check in the
  upload route handler still protects against those.

* Inspects all unsafe-method requests (POST, PUT, PATCH, DELETE)
  regardless of path.  Prevents oversized payloads from reaching any
  endpoint, not just the document upload one.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``Content-Length`` exceeds ``max_bytes``."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # Guard all unsafe-method requests (POST, PUT, PATCH, DELETE)
        # against oversized payloads. Chunked encoding without
        # Content-Length is not caught here — the endpoint-level
        # checks still protect against those.
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                except (ValueError, TypeError):
                    length = -1
                if length > self._max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": {
                                "code": "payload_too_large",
                                "message": (
                                    f"File size ({length} bytes) exceeds the "
                                    f"maximum allowed ({self._max_bytes} bytes)."
                                ),
                            }
                        },
                    )
        return await call_next(request)
