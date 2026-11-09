"""HTTP-level tests for the API surface (no network, in-process)."""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Generator
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

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


# Spec 007 T3: the client-side flow must present the one-time job access
# token (returned only in the creation response) on every follow-up call.
# Synthetic jobs in this file set ``access_token_digest`` to the SHA-256 of
# this ASCII ``_FIXTURE_JOB_TOKEN`` (see ``_fixture_digest``), so the header
# below always matches the stored digest.  The production token-verification
# path is untouched; only the synthetic-fixture token/digest pair is aligned.
_FIXTURE_JOB_TOKEN = "tok-api-test"


def _fixture_digest() -> str:
    """Return the SHA-256 hex digest of ``_FIXTURE_JOB_TOKEN`` (test helper)."""
    return hashlib.sha256(_FIXTURE_JOB_TOKEN.encode("utf-8")).hexdigest()


JOB_TOKEN_HEADERS = {"X-Job-Token": _FIXTURE_JOB_TOKEN}


async def _loopback_peer(request, call_next):
    """Patch TestClient's synthetic peer to loopback (spec 007 TD-001)."""
    request.scope["client"] = ("127.0.0.1", 80)
    return await call_next(request)


@pytest.fixture
def client_and_storage(tmp_path: Path) -> Generator[tuple[TestClient, LocalStorage], None, None]:
    """Build a FastAPI client with overridden storage and mock privacy.

    The peer is patched to loopback (spec 007 FR-001/FR-002) so that
    header-less UI-style requests in this file still pass the fail-closed
    documents auth that TestClient's synthetic peer would otherwise fail.
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    app = create_app()
    app.middleware("http")(_loopback_peer)
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
    # Spec 007 T3: the raw token is returned exactly once, on creation.
    assert isinstance(body["access_token"], str) and body["access_token"]
    real_token_headers = {"X-Job-Token": body["access_token"]}

    status_response = client.get(
        f"/api/v1/documents/{job_id}", headers=real_token_headers
    )
    assert status_response.status_code == 200
    # The status response must never echo the raw token.
    assert "access_token" not in status_response.json()


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
    creation = response.json()
    job_id = creation["id"]
    assert isinstance(creation["access_token"], str) and creation["access_token"]
    real_token_headers = {"X-Job-Token": creation["access_token"]}

    status_response = client.get(
        f"/api/v1/documents/{job_id}", headers=real_token_headers
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.get(
        f"/api/v1/documents/{job.id}/download", headers=JOB_TOKEN_HEADERS
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    storage.save_result(job.id, "md", b"cleaned content")

    response = client.get(
        f"/api/v1/documents/{job.id}/download", headers=JOB_TOKEN_HEADERS
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    storage.save_result(job.id, "docx", b"PK\x03\x04 stub")

    response = client.get(
        f"/api/v1/documents/{job.id}/download", headers=JOB_TOKEN_HEADERS
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    storage.save_result(job.id, "docx", b"PK\x03\x04 stub")

    response = client.get(
        f"/api/v1/documents/{job.id}/download", headers=JOB_TOKEN_HEADERS
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        headers=JOB_TOKEN_HEADERS,
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    (storage.job_dir(job.id) / "job.json").write_text("{not json", encoding="utf-8")

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        headers=JOB_TOKEN_HEADERS,
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        headers=JOB_TOKEN_HEADERS,
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        headers=JOB_TOKEN_HEADERS,
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.post(
        f"/api/v1/documents/{job.id}/feedback",
        headers=JOB_TOKEN_HEADERS,
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    # Deliberately do NOT save the result file.

    response = client.get(
        f"/api/v1/documents/{job.id}/download", headers=JOB_TOKEN_HEADERS
    )
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
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)

    response = client.get(
        f"/api/v1/documents/{job.id}/download",
        headers={"Accept": "application/json", **JOB_TOKEN_HEADERS},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "result_missing"


# ---------------------------------------------------------------------------
# Spec 007 T3: per-job opaque access token enforcement
# ---------------------------------------------------------------------------


def _completed_tokened_job(storage: LocalStorage) -> Job:
    job = Job(
        source_filename="note.md",
        source_ext="md",
        status=JobStatus.COMPLETED,
        access_token_digest=_fixture_digest(),
    )
    storage.save_job(job)
    storage.save_result(job.id, "md", b"cleaned content")
    return job


def _upload_and_get_token(client: TestClient) -> tuple[str, str]:
    """Upload via the public API; return ``(job_id, raw_token)``."""
    response = client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", b"user@example.com", "text/markdown")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["id"], body["access_token"]


class TestJobTokenEnforcement:
    def test_upload_response_carries_raw_token(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job_id, token = _upload_and_get_token(client)
        assert token
        # The raw token must NOT be persisted — job.json holds the digest only.
        stored = storage.load_job(UUID(job_id))
        assert stored.access_token_digest is not None
        assert stored.access_token_digest != token
        assert len(stored.access_token_digest) == 64

    def test_missing_token_returns_404(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id, _token = _upload_and_get_token(client)
        response = client.get(f"/api/v1/documents/{job_id}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "job_not_found"

    def test_wrong_token_returns_404_indistinguishable(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id, _token = _upload_and_get_token(client)
        wrong = client.get(
            f"/api/v1/documents/{job_id}",
            headers={"X-Job-Token": "wrong-token"},
        )
        missing = client.get(f"/api/v1/documents/{job_id}")
        assert wrong.status_code == missing.status_code == 404
        assert wrong.json() == missing.json()
        unknown = client.get(f"/api/v1/documents/{uuid4()}")
        assert unknown.json() == wrong.json()

    def test_valid_token_allows_status_and_download(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id, token = _upload_and_get_token(client)
        headers = {"X-Job-Token": token}
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            body = client.get(f"/api/v1/documents/{job_id}", headers=headers).json()
            if body["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert body["status"] == "completed"
        dl = client.get(f"/api/v1/documents/{job_id}/download", headers=headers)
        assert dl.status_code == 200

    def test_download_with_wrong_token_returns_404(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job = _completed_tokened_job(storage)
        response = client.get(
            f"/api/v1/documents/{job.id}/download",
            headers={"X-Job-Token": "bad"},
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "job_not_found"

    def test_legacy_job_without_digest_returns_404_for_non_admin(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job = Job(
            source_filename="note.md",
            source_ext="md",
            status=JobStatus.COMPLETED,
        )
        storage.save_job(job)  # no digest — legacy job

        for path in ("", "/download", "/annotations", "/preview"):
            response = client.get(f"/api/v1/documents/{job.id}{path}")
            assert response.status_code == 404, path
            assert response.json()["detail"]["code"] == "job_not_found"

    def test_legacy_job_without_digest_is_admin_only(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        """A valid admin session bypasses the token check (explicit bypass)."""
        client, storage = client_and_storage
        job = _completed_tokened_job(storage)
        from neironir.auth.session import sign_session_cookie

        st = client.app.state.settings
        raw = sign_session_cookie(
            {"is_admin": True, "csrf_sid": "x"}, secret=st.session_secret
        )
        client.cookies.set(st.session_cookie_name, raw)
        try:
            response = client.get(f"/api/v1/documents/{job.id}")
            assert response.status_code == 200
        finally:
            client.cookies.clear()

    def test_feedback_and_apply_feedback_require_token(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job = _completed_tokened_job(storage)
        payload = {"actions": []}
        for headers in (None, {"X-Job-Token": "bad"}, {"X-Job-Token": "bad2"}):
            response = client.post(
                f"/api/v1/documents/{job.id}/feedback",
                json=payload,
                headers=headers,
            )
            assert response.status_code == 404, (headers, response.text)

    def test_download_rejects_access_token_query_param(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        """Spec 008 FR-001: a token passed as ``?access_token=...`` never
        authorizes a request — the URL fallback was removed so tokens do
        not leak into browser history, access logs or the Referer header.
        Even a *valid* token in the query string gets the same 404 as a
        missing one."""
        client, storage = client_and_storage
        job = _completed_tokened_job(storage)
        token = "tok-frontend"
        job.access_token_digest = hashlib.sha256(token.encode()).hexdigest()
        storage.save_job(job)

        response = client.get(
            f"/api/v1/documents/{job.id}/download?access_token={token}"
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "job_not_found"
