"""Integration tests for the new admin output-format + apply-feedback APIs."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient
from neironir.admin.training import reset_training_state
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage


def _build_docx(path: Path, paragraphs: list[str]) -> bytes:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))
    return path.read_bytes()


@pytest.fixture
def client_and_storage(
    tmp_path: Path,
) -> Generator[tuple[TestClient, Path], None, None]:
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()
    real_settings = get_settings().model_copy(update={"storage_dir": str(storage_dir)})

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy

    reset_training_state()
    with TestClient(app) as client:
        yield client, storage_dir

    reset_training_state()
    shutil.rmtree(storage_dir, ignore_errors=True)


def _wait_completed(client: TestClient, job_id: str) -> dict:
    for _ in range(30):
        r = client.get(f"/api/v1/documents/{job_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError("job did not complete in time")


# ---------------------------------------------------------------------------
# output_format flag on upload
# ---------------------------------------------------------------------------


class TestOutputFormatOnUpload:
    def test_default_output_format_matches_source(
        self, client_and_storage: tuple[TestClient, Path]) -> None:
        client, _ = client_and_storage
        # Default (no form field) — output_ext == source_ext
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"hello", "text/markdown")},
        )
        assert r.status_code == 202
        body = r.json()
        # Either ``null`` or ``"md"`` — both are valid: null is the
        # default and gets coerced at the storage layer.
        assert body.get("output_ext") in (None, "md")

    def test_md_file_with_output_format_md(
        self, client_and_storage: tuple[TestClient, Path]) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"hello", "text/markdown")},
            data={"output_format": "md"},
        )
        assert r.status_code == 202
        assert r.json()["output_ext"] == "md"

    def test_docx_with_output_format_md_converts(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path) -> None:
        client, storage_dir = client_and_storage
        binary = _build_docx(
            tmp_path / "src.docx",
            ["Reach me at user@example.com."],
        )

        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
            data={"output_format": "md"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["output_ext"] == "md"

        job = _wait_completed(client, body["id"])
        assert job["status"] == "completed"
        # result.md exists in the job directory.
        result_path = storage_dir / "jobs" / body["id"] / "result.md"
        assert result_path.is_file()
        content = result_path.read_text(encoding="utf-8")
        assert "<PRIVATE_EMAIL1>" in content
        assert "user@example.com" not in content

    def test_docx_without_output_format_keeps_docx(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path
    ) -> None:
        client, storage_dir = client_and_storage
        binary = _build_docx(tmp_path / "src.docx", ["Email: user@example.com"])
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
        )
        assert r.status_code == 202
        job = _wait_completed(client, r.json()["id"])
        assert job["status"] == "completed"
        # result.docx exists.
        result_path = storage_dir / "jobs" / job["id"] / "result.docx"
        assert result_path.is_file()

    def test_docx_with_invalid_output_format_returns_400(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path
    ) -> None:
        client, _ = client_and_storage
        binary = _build_docx(tmp_path / "src.docx", ["Email: user@example.com"])
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
            data={"output_format": "pdf"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsupported_output_format"

    def test_md_with_output_format_docx_returns_400(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"hello", "text/markdown")},
            data={"output_format": "docx"},
        )
        assert r.status_code == 400


class TestDownloadRespectsOutputFormat:
    def test_download_returns_md_when_output_format_md(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path
    ) -> None:
        client, _ = client_and_storage
        binary = _build_docx(tmp_path / "src.docx", ["Email: user@example.com"])
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
            data={"output_format": "md"},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        dl = client.get(f"/api/v1/documents/{job_id}/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("text/markdown")
        assert 'filename="contract.cleaned.md"' in dl.headers["content-disposition"]

    def test_download_returns_docx_by_default(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path
    ) -> None:
        client, _ = client_and_storage
        binary = _build_docx(tmp_path / "src.docx", ["Email: user@example.com"])
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        dl = client.get(f"/api/v1/documents/{job_id}/download")
        assert dl.status_code == 200
        assert "officedocument.wordprocessingml.document" in dl.headers["content-type"]
        assert 'filename="contract.cleaned.docx"' in dl.headers["content-disposition"]


# ---------------------------------------------------------------------------
# apply-feedback endpoint
# ---------------------------------------------------------------------------


class TestApplyFeedbackEndpoint:
    def test_apply_feedback_requires_completed_job(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, _ = client_and_storage
        # Create a job but don't wait for completion (mock is sync so
        # this is essentially immediate).  We rely on the status check
        # inside the handler.
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"user@example.com", "text/markdown")},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        payload = {
            "actions": [
                {
                    "action": "confirm",
                    "start": 0,
                    "end": 16,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
            "comment": None,
        }
        r2 = client.post(f"/api/v1/documents/{job_id}/apply-feedback", json=payload)
        assert r2.status_code == 200
        body = r2.json()
        assert body["applied"] >= 0
        assert body["kept"] == 1

    def test_apply_feedback_adds_new_placeholder(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, storage_dir = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"user@example.com", "text/markdown")},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        # Add a new phone (an offset that maps to plain text).  The
        # source text is 16 chars; offset 14..16 is "om" — a plain
        # suffix that's not part of the email placeholder.
        payload = {
            "actions": [
                {
                    "action": "add",
                    "start": 14,
                    "end": 16,
                    "entity_type": "private_phone",
                    "text": "om",
                }
            ],
            "comment": None,
        }
        r2 = client.post(f"/api/v1/documents/{job_id}/apply-feedback", json=payload)
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["added"] == 1, body

        result_path = storage_dir / "jobs" / job_id / "result.md"
        content = result_path.read_text(encoding="utf-8")
        assert "<PRIVATE_EMAIL1>" in content
        assert "<PRIVATE_PHONE1>" in content

    def test_apply_feedback_rejects_false_positive(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, storage_dir = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"user@example.com", "text/markdown")},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        payload = {
            "actions": [
                {
                    "action": "reject",
                    "start": 0,
                    "end": 16,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
            "comment": None,
        }
        r2 = client.post(f"/api/v1/documents/{job_id}/apply-feedback", json=payload)
        assert r2.status_code == 200
        body = r2.json()
        assert body["rejected"] == 1

        result_path = storage_dir / "jobs" / job_id / "result.md"
        content = result_path.read_text(encoding="utf-8")
        assert "user@example.com" in content
        assert "<PRIVATE_EMAIL1>" not in content

    def test_apply_feedback_404_for_missing_job(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, _ = client_and_storage
        missing = uuid4()
        payload = {"actions": [], "comment": None}
        r = client.post(f"/api/v1/documents/{missing}/apply-feedback", json=payload)
        assert r.status_code == 404

    def test_apply_feedback_saves_feedback_file(
        self, client_and_storage: tuple[TestClient, Path]
    ) -> None:
        client, storage_dir = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("doc.md", b"user@example.com", "text/markdown")},
        )
        job_id = r.json()["id"]
        _wait_completed(client, job_id)

        payload = {"actions": [], "comment": "looks good"}
        client.post(f"/api/v1/documents/{job_id}/apply-feedback", json=payload)

        feedback_path = storage_dir / "jobs" / job_id / "feedback.json"
        assert feedback_path.is_file()
        data = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert data["comment"] == "looks good"

    def test_apply_feedback_on_docx_with_output_ext_md(
        self, client_and_storage: tuple[TestClient, Path], tmp_path: Path
    ) -> None:
        """Docx→md conversion produces a result.md that can be patched."""
        client, storage_dir = client_and_storage
        binary = _build_docx(
            tmp_path / "src.docx",
            ["Reach me at user@example.com."],
        )
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("contract.docx", binary, "application/octet-stream")},
            data={"output_format": "md"},
        )
        job_id = r.json()["id"]
        job = _wait_completed(client, job_id)
        assert job["status"] == "completed"
        assert job["output_ext"] == "md"

        # Reject the email.
        payload = {
            "actions": [
                {
                    "action": "reject",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
            "comment": None,
        }
        r2 = client.post(f"/api/v1/documents/{job_id}/apply-feedback", json=payload)
        assert r2.status_code == 200
        assert r2.json()["output_ext"] == "md"

        result_path = storage_dir / "jobs" / job_id / "result.md"
        content = result_path.read_text(encoding="utf-8")
        assert "user@example.com" in content
        assert "<PRIVATE_EMAIL" not in content