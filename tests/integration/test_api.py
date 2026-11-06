"""HTTP-level tests for the API surface (no network, in-process)."""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_storage
from neironir.domain.job import Job, JobStatus
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage


def _build_docx(tmp_path: Path, paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    path = tmp_path / "doc.docx"
    document.save(str(path))
    return path.read_bytes()


@pytest.fixture
def client_and_storage(tmp_path: Path) -> Generator[tuple[TestClient, LocalStorage], None, None]:
    """Build a FastAPI client with overridden storage and mock privacy."""
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


def test_upload_markdown_returns_202_and_starts_pipeline(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _storage = client_and_storage
    content = b"Reach me at user@example.com please."

    response = client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", content, "text/markdown")},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] in {"pending", "processing", "completed"}
    assert body["source_filename"] == "note.md"
    assert body["source_ext"] == "md"
    job_id = body["id"]

    status_response = client.get(f"/api/v1/documents/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


def test_upload_docx_returns_202_and_produces_placeholder(
    client_and_storage: tuple[TestClient, LocalStorage],
    tmp_path: Path,
) -> None:
    client, _storage = client_and_storage
    binary = _build_docx(tmp_path, ["Email: user@example.com"])

    response = client.post(
        "/api/v1/documents/",
        files={"file": ("doc.docx", binary, "application/octet-stream")},
    )

    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    status_response = client.get(f"/api/v1/documents/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


def test_upload_unknown_extension_returns_400(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _ = client_and_storage
    response = client.post(
        "/api/v1/documents/",
        files={"file": ("bad.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_format"


def test_upload_over_max_size_returns_413(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _ = client_and_storage
    # Force a tiny cap so we don't need to ship megabytes of test data.
    # We replace the app's settings dependency with a fresh instance
    # rather than mutating the cached global — the global is shared
    # across tests in the process.
    from neironir.api.dependencies import get_settings

    app = client.app
    real_settings = get_settings().model_copy(update={"max_file_size": 8})
    app.dependency_overrides[get_settings] = lambda: real_settings

    big = b"x" * 32
    response = client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", big, "text/markdown")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"


def test_get_unknown_job_returns_404(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _ = client_and_storage
    missing = uuid4()
    response = client.get(f"/api/v1/documents/{missing}")
    assert response.status_code == 404
    # FastAPI wraps ``HTTPException.detail`` in a ``detail`` key.
    assert response.json()["detail"]["code"] == "job_not_found"


def test_download_pending_job_returns_409(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.PENDING,
    )
    storage.save_job(job)

    response = client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_not_ready"


def test_download_completed_job_returns_file(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)
    storage.save_result(job.id, "md", b"cleaned content")

    response = client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 200
    assert response.content == b"cleaned content"
    # Starlette quotes the filename inside Content-Disposition.
    assert 'filename="note.cleaned.md"' in response.headers["content-disposition"]


def test_download_completed_docx_returns_file(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, storage = client_and_storage
    job = Job(
        source_filename="multi.dotted.name.docx",
        source_ext="docx",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)
    storage.save_result(job.id, "docx", b"PK\x03\x04 stub")

    response = client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 200
    assert 'filename="multi.dotted.name.cleaned.docx"' in response.headers["content-disposition"]


def test_download_non_ascii_filename_has_ascii_fallback(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """Non-ASCII names get both an ASCII fallback and an RFC 5987 parameter."""
    client, storage = client_and_storage
    job = Job(
        source_filename="ф2.docx",
        source_ext="docx",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)
    storage.save_result(job.id, "docx", b"PK\x03\x04 stub")

    response = client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 200

    disposition = response.headers["content-disposition"]
    # Plain ASCII fallback parameter (RFC 6266 §4.3): no Cyrillic, no quotes.
    assert 'filename="_2.cleaned.docx"' in disposition
    # RFC 5987 parameter carries the URL-encoded original name.
    assert f"filename*=utf-8''{quote('ф2.cleaned.docx')}" in disposition
    # The part before filename* must be pure ASCII.
    assert disposition.split("filename*=utf-8''")[0].isascii()


def test_root_returns_placeholder_html(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _ = client_and_storage
    response = client.get("/")
    assert response.status_code == 200
    assert "neironir" in response.text


def test_health_endpoint_still_works(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, _ = client_and_storage
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_submit_feedback_on_pending_job_returns_409(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """T4 regression: feedback is rejected for a job that is not COMPLETED."""
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.PENDING,
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        json={
            "actions": [
                {
                    "action": "reject",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_person",
                    "text": "Иванов",
                    "original_span_index": 0,
                }
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_not_ready"
    # feedback.json must NOT be created for a non-COMPLETED job.
    assert not (storage.job_dir(job.id) / "feedback.json").exists()


def test_submit_feedback_on_unknown_job_returns_404(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """T4 regression: feedback for a nonexistent job is 404."""
    client, _storage = client_and_storage

    response = client.post(
        f"/api/v1/documents/{uuid4()}/feedback",
        json={
            "actions": [
                {
                    "action": "reject",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_person",
                    "text": "Иванов",
                    "original_span_index": 0,
                }
            ]
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_submit_feedback_on_corrupted_job_returns_409(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """T4 regression: corrupted job.json yields 409, not a 500 crash."""
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)
    (storage.job_dir(job.id) / "job.json").write_text("{not json", encoding="utf-8")

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        json={
            "actions": [
                {
                    "action": "reject",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_person",
                    "text": "Иванов",
                    "original_span_index": 0,
                }
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_corrupted"
    assert not (storage.job_dir(job.id) / "feedback.json").exists()


def test_feedback_end_before_start_returns_422(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """T8 regression: malformed feedback is rejected on the schema (422)."""
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        json={
            "actions": [
                {
                    "action": "add",
                    "start": 10,
                    "end": 5,
                    "entity_type": "private_email",
                    "text": "x",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_feedback_unknown_action_returns_422(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        json={
            "actions": [
                {
                    "action": "delete",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_email",
                    "text": "x",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_feedback_unknown_entity_type_returns_422(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        json={
            "actions": [
                {
                    "action": "add",
                    "start": 0,
                    "end": 5,
                    "entity_type": "not_a_type",
                    "text": "x",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_download_completed_job_without_result_returns_404(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """T17 regression (#17): missing result file must be 404, not 500."""
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)
    # Deliberately do NOT save the result file.

    response = client.get(f"/api/v1/documents/{job.id}/download")
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "result_missing"


def test_download_json_negotiation_missing_result_returns_404(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """The JSON (base64) branch shares the same missing-file guard."""
    client, storage = client_and_storage
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
    )
    storage.save_job(job)

    response = client.get(
        f"/api/v1/documents/{job.id}/download",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "result_missing"
