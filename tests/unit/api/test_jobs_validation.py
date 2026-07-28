"""HTTP-level validation tests for the ``/api/v1/documents`` routes.

These tests pin the response shape and HTTP status codes that the
upload/status/download handlers return on the error path. They use
``TestClient`` and a stripped-down app — the storage and privacy
dependencies are overridden so the tests do not touch the filesystem
or the model.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage


@pytest.fixture
def client(tmp_path: Path) -> Generator[tuple[TestClient, LocalStorage, Settings], None, None]:
    """A FastAPI client with overridden storage, privacy, and settings."""
    # Clear the lru_cache that powers ``get_settings``. The cache is
    # process-wide; without this reset, a previous test that mutated
    # the cached ``Settings`` instance would leak its mutations into
    # unrelated tests.
    from neironir.api.dependencies import _settings_cache

    _settings_cache.cache_clear()

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    # Use an isolated settings instance with a tiny ``max_file_size``
    # so the size-cap test doesn't need to upload megabytes.
    settings = Settings(max_file_size=8)

    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client, storage, settings

    shutil.rmtree(storage_dir, ignore_errors=True)
    _settings_cache.cache_clear()


# ---------------------------------------------------------------------------
# upload validation
# ---------------------------------------------------------------------------


def test_upload_without_file_returns_422(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, _settings = client
    response = test_client.post("/api/v1/documents/")
    # FastAPI's built-in validation produces 422 on a missing required
    # body parameter.
    assert response.status_code == 422


def test_upload_with_unsupported_extension_returns_400(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, _settings = client
    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("script.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400
    body = response.json()["detail"]
    assert body["code"] == "unsupported_format"
    assert ".md" in body["message"] and ".docx" in body["message"]


def test_upload_with_txt_extension_returns_400(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    """Plain text is not in the supported set — same code as ``.exe``."""
    test_client, _storage, _settings = client
    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_format"


def test_upload_with_no_extension_returns_400(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    """A file with no extension has no supported suffix — 400."""
    test_client, _storage, _settings = client
    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("README", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_format"


def test_upload_over_max_size_returns_413(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, settings = client
    # Settings fixture caps ``max_file_size`` at 8 bytes.
    assert settings.max_file_size == 8
    big = b"x" * 32
    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", big, "text/markdown")},
    )
    assert response.status_code == 413
    body = response.json()["detail"]
    assert body["code"] == "file_too_large"
    assert str(settings.max_file_size) in body["message"]


def test_upload_with_uppercase_extension_is_accepted(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    """``.MD`` (upper-case) must be treated the same as ``.md``."""
    test_client, _storage, _settings = client
    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("NOTE.MD", b"hello", "text/markdown")},
    )
    assert response.status_code == 202
    assert response.json()["source_ext"] == "md"


# ---------------------------------------------------------------------------
# status / download validation
# ---------------------------------------------------------------------------


def test_get_unknown_job_returns_404(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, _settings = client
    missing = uuid4()
    response = test_client.get(f"/api/v1/documents/{missing}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_get_malformed_job_id_returns_422(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, _settings = client
    response = test_client.get("/api/v1/documents/not-a-uuid")
    # FastAPI rejects malformed path parameters with 422.
    assert response.status_code == 422


def test_download_unknown_job_returns_404(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, _settings = client
    missing = uuid4()
    response = test_client.get(f"/api/v1/documents/{missing}/download")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_download_pending_job_returns_409(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, storage, _settings = client
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.PENDING,
    )
    storage.save_job(job)

    response = test_client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "job_not_ready"
    # The error message must mention the actual status.
    assert "pending" in body["message"].lower()


def test_download_processing_job_returns_409(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, storage, _settings = client
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.PROCESSING,
    )
    storage.save_job(job)

    response = test_client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_not_ready"


def test_download_failed_job_returns_409(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, storage, _settings = client
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.FAILED,
        error="boom",
    )
    storage.save_job(job)

    response = test_client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# happy-path sanity (regression of the validation contract)
# ---------------------------------------------------------------------------


def test_upload_markdown_returns_202(
    client: tuple[TestClient, LocalStorage, Settings],
) -> None:
    test_client, _storage, settings = client
    # The fixture caps ``max_file_size`` at 8 to drive the 413 case
    # without uploading megabytes. The happy-path test bypasses the
    # cap by raising the limit on the same instance — the fixture
    # uses dependency overrides, so the mutation is visible to the
    # app under test.
    settings.max_file_size = 1_048_576

    response = test_client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", b"hello world", "text/markdown")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"pending", "processing", "completed"}
    assert body["source_filename"] == "note.md"
    assert body["source_ext"] == "md"
    assert body["error"] is None
