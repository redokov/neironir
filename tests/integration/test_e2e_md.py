"""End-to-end test of the HTTP API for a markdown upload.

Drives the full upload → poll → download loop with a real
``TestClient`` and the mock privacy client. The flow mirrors what a
browser does in :mod:`frontend.app.js`.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_storage
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage


@pytest.fixture
def client_and_storage(tmp_path: Path) -> Generator[tuple[TestClient, LocalStorage], None, None]:
    """Build a FastAPI client with overridden storage and privacy deps."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy

    with TestClient(app) as client:
        yield client, storage

    shutil.rmtree(storage_dir, ignore_errors=True)


def test_e2e_markdown_upload_poll_download(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """A markdown file is redacted and downloadable end-to-end."""
    client, _storage = client_and_storage
    source_text = (
        "# Notes\n"
        "\n"
        "Email: alice@example.com\n"
        "Phone: +7 495 123-45-67\n"
        "Site: https://example.com/page\n"
    )

    # 1. Upload
    upload_response = client.post(
        "/api/v1/documents/",
        files={"file": ("notes.md", source_text.encode("utf-8"), "text/markdown")},
    )
    assert upload_response.status_code == 202
    job = upload_response.json()
    job_id = job["id"]
    assert job["source_filename"] == "notes.md"
    assert job["source_ext"] == "md"

    # 2. Poll until the job is no longer pending. The mock client is
    # synchronous, so a few iterations are plenty.
    final_status: str | None = None
    for _ in range(20):
        status_response = client.get(f"/api/v1/documents/{job_id}")
        assert status_response.status_code == 200
        final_status = status_response.json()["status"]
        if final_status in {"completed", "failed"}:
            break
    assert final_status == "completed"

    # 3. Download
    download_response = client.get(f"/api/v1/documents/{job_id}/download")
    assert download_response.status_code == 200
    cleaned = download_response.content.decode("utf-8")

    # The original entities must be gone; the placeholders must be present.
    assert "alice@example.com" not in cleaned
    assert "+7 495 123-45-67" not in cleaned
    assert "https://example.com/page" not in cleaned
    assert "<PRIVATE_EMAIL1>" in cleaned
    assert "<PRIVATE_PHONE1>" in cleaned
    assert "<PRIVATE_URL1>" in cleaned

    # The download filename follows the ``<stem>.cleaned.<ext>`` rule.
    assert 'filename="notes.cleaned.md"' in download_response.headers["content-disposition"]


def test_e2e_markdown_without_sensitive_data_round_trips(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """A clean document is preserved as-is, with no placeholder injected.

    Note: ``starlette.testclient`` may normalise the line endings of
    multipart uploads, so we don't compare bytes-for-bytes against the
    original payload. Instead we check the substantive invariants: the
    status completes, no placeholder leaks in, and every paragraph
    text is preserved.
    """
    client, _storage = client_and_storage
    source_text = "# Hello\n\nJust a plain markdown document, no PII here.\n"
    source_paragraphs = ["# Hello", "", "Just a plain markdown document, no PII here."]

    upload_response = client.post(
        "/api/v1/documents/",
        files={"file": ("hello.md", source_text.encode("utf-8"), "text/markdown")},
    )
    assert upload_response.status_code == 202
    job_id = upload_response.json()["id"]

    # Poll for completion
    for _ in range(20):
        status_response = client.get(f"/api/v1/documents/{job_id}")
        if status_response.json()["status"] in {"completed", "failed"}:
            break
    assert status_response.json()["status"] == "completed"

    download_response = client.get(f"/api/v1/documents/{job_id}/download")
    assert download_response.status_code == 200
    cleaned = download_response.content.decode("utf-8")
    assert "<PRIVATE" not in cleaned
    for paragraph in source_paragraphs:
        assert paragraph in cleaned
