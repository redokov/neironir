"""Static regression tests for the spec 008 tokenless download.

The download button must fetch the cleaned file from script with the
``X-Job-Token`` header (Blob + ``URL.createObjectURL``) — the one-time
job token must never be embedded in a URL, where it would leak into
browser history, server access logs and the Referer header.

See `.ai/sdd/specs/008-remove-access-token-query-fallback/` (FR-002).
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
APP_JS = FRONTEND_DIR / "app.js"


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_no_access_token_in_frontend_urls() -> None:
    src = _app_source()
    assert "access_token=" not in src, "job token must never be embedded in a URL"
    assert "?access_token" not in src, "query-string token fallback must be gone"


def test_download_is_bound_to_click_handler() -> None:
    src = _app_source()
    assert '$.download.addEventListener("click", downloadResult)' in src, (
        "the download link must be driven by a script click handler"
    )


def test_download_fetches_with_header_and_blob() -> None:
    src = _app_source()
    assert "URL.createObjectURL" in src, "blob download must use createObjectURL"
    assert "URL.revokeObjectURL" in src, "object URL must be revoked after the click"
    match = re.search(r"function downloadResult\b.*?\n  \}", src, re.S)
    assert match, "downloadResult function not found"
    block = match.group(0)
    assert "e.preventDefault()" in block, (
        "click handler must prevent the default link navigation"
    )
    assert "headers: jobTokenHeaders()" in block, (
        "download fetch must send the X-Job-Token header"
    )
    assert "/download" in block
