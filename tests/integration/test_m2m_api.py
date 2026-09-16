"""HTTP-level integration tests for the machine-to-machine (M2M) API (F15).

Red stage (TDD): these tests fail until T08–T11 implement the Bearer
auth guard, the router wiring and the JSON download negotiation.
They encode the M2M contract from ``design.md`` §6/§9/§10.2:

* happy path: upload with a key → poll → download (binary + JSON);
* 401 for wrong keys / non-Bearer schemes / unconfigured keys,
  always with ``WWW-Authenticate: Bearer`` and never leaking the key;
* Bearer precedence over session cookies (Q2/TD-002);
* content negotiation on ``/download`` (exact ``application/json`` match);
* 404/409/400/413 error contract;
* regression: requests without an Authorization header keep working
  (FR-003 — the UI flow must not change).
"""

from __future__ import annotations

import base64
import shutil
import time
from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage

TEST_SECRET = "test-secret-for-m2m-api-tests"
TEST_USER = "testadmin"
TEST_PASSWORD = "testpass123"
API_KEYS = "test-key-1,test-key-2"
VALID_AUTH = {"Authorization": "Bearer test-key-1"}


def _make_client(
    tmp_path: Path,
    api_keys: str,
) -> Generator[tuple[TestClient, LocalStorage], None, None]:
    """Build a TestClient with mock privacy and the given API key config."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    real_settings = Settings(
        storage_dir=str(storage_dir),
        session_secret=TEST_SECRET,
        admin_user=TEST_USER,
        admin_password=TEST_PASSWORD,
        api_keys=api_keys,
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy
    # Keep the admin-UI middleware in sync with the overridden settings.
    app.state.settings = real_settings

    with TestClient(app) as client:
        yield client, storage

    shutil.rmtree(storage_dir, ignore_errors=True)


@pytest.fixture
def client_and_storage(tmp_path: Path) -> Generator[tuple[TestClient, LocalStorage], None, None]:
    """Client with two configured API keys (mock privacy filter)."""
    yield from _make_client(tmp_path, API_KEYS)


@pytest.fixture
def no_keys_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Client with an empty ``api_keys`` — M2M disabled (NFR-004)."""
    for client, _storage in _make_client(tmp_path, ""):
        yield client


def _wait_for_completion(
    client: TestClient,
    job_id: str,
    headers: dict[str, str] | None = None,
    max_wait_s: float = 10.0,
) -> dict:
    """Poll the job endpoint until the pipeline reaches a terminal state."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/documents/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not complete within {max_wait_s}s")


def _upload_md(client: TestClient, headers: dict[str, str] | None = None) -> str:
    """Upload a small markdown file with an email in it; return the job id."""
    content = b"Reach me at user@example.com please."
    r = client.post(
        "/api/v1/documents/",
        files={"file": ("note.md", content, "text/markdown")},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


class TestM2MHappyPath:
    def test_upload_poll_download_binary_and_json(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job_id = _upload_md(client, VALID_AUTH)

        job = _wait_for_completion(client, job_id, VALID_AUTH)
        assert job["status"] == "completed", job
        assert job["id"] == job_id

        # --- Binary download (no explicit Accept) -----------------------
        binary = client.get(f"/api/v1/documents/{job_id}/download", headers=VALID_AUTH)
        assert binary.status_code == 200, binary.text
        assert "text/markdown" in binary.headers["content-type"]
        assert 'filename="note.cleaned.md"' in binary.headers["content-disposition"]

        result_path = storage.job_dir(UUID(job_id)) / "result.md"
        result_bytes = result_path.read_bytes()
        assert binary.content == result_bytes

        # --- JSON download (Accept: application/json) -------------------
        json_resp = client.get(
            f"/api/v1/documents/{job_id}/download",
            headers={**VALID_AUTH, "Accept": "application/json"},
        )
        assert json_resp.status_code == 200, json_resp.text
        assert "application/json" in json_resp.headers["content-type"]
        body = json_resp.json()

        assert set(body) == {
            "job_id",
            "filename",
            "ext",
            "media_type",
            "size",
            "content_base64",
        }
        assert body["job_id"] == job_id
        assert body["filename"] == "note.cleaned.md"
        assert body["ext"] == "md"
        assert body["media_type"] == "text/markdown; charset=utf-8"
        assert body["size"] == len(result_bytes)
        assert base64.b64decode(body["content_base64"]) == result_bytes

        # The mock filter redacts the email into a placeholder.
        cleaned_text = result_bytes.decode("utf-8")
        assert "user@example.com" not in cleaned_text
        assert "<PRIVATE_EMAIL" in cleaned_text


class TestM2MUnauthorized:
    def test_wrong_key_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"text", "text/markdown")},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "Bearer"
        detail = r.json()["detail"]
        assert detail["code"] == "invalid_api_key"
        # The rejected key value must never appear in the error body.
        assert "wrong-key" not in r.text

    def test_non_bearer_scheme_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"text", "text/markdown")},
            headers={"Authorization": "Basic xyz"},
        )
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "Bearer"
        assert r.json()["detail"]["code"] == "invalid_api_key"

    def test_bare_bearer_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"text", "text/markdown")},
            headers={"Authorization": "Bearer"},
        )
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "Bearer"

    def test_unconfigured_keys_reject_any_bearer(self, no_keys_client: TestClient) -> None:
        """Empty ``api_keys`` = M2M off (NFR-004): even a well-formed
        Bearer header gets 401."""
        r = no_keys_client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"text", "text/markdown")},
            headers={"Authorization": "Bearer test-key-1"},
        )
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "Bearer"
        assert r.json()["detail"]["code"] == "invalid_api_key"

    def test_get_job_with_wrong_key_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.get(
            f"/api/v1/documents/{uuid4()}",
            headers={"Authorization": "Bearer nope"},
        )
        assert r.status_code == 401, r.text

    def test_download_with_wrong_key_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.get(
            f"/api/v1/documents/{uuid4()}/download",
            headers={"Authorization": "Bearer nope"},
        )
        assert r.status_code == 401, r.text


class TestM2MBearerPrecedence:
    """Q2/TD-002: a present Authorization header always takes the API-key
    path — a valid session cookie never rescues an invalid Bearer."""

    def test_invalid_bearer_with_valid_session_returns_401(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        # Log in to obtain a genuinely valid session cookie.
        login = client.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code in (302, 303), login.text

        r = client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"text", "text/markdown")},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401, r.text
        assert r.json()["detail"]["code"] == "invalid_api_key"

    def test_valid_bearer_works_without_any_session(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id = _upload_md(client, VALID_AUTH)
        job = _wait_for_completion(client, job_id, VALID_AUTH)
        assert job["status"] == "completed"


class TestDownloadContentNegotiation:
    """Q3/TD-003: JSON only on an exact ``application/json`` match."""

    def _completed_job(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> tuple[TestClient, LocalStorage, str]:
        client, storage = client_and_storage
        job_id = _upload_md(client, VALID_AUTH)
        job = _wait_for_completion(client, job_id, VALID_AUTH)
        assert job["status"] == "completed"
        return client, storage, job_id

    @staticmethod
    def _get_without_accept(client: TestClient, url: str) -> object:
        """Send a request with the Accept header fully removed."""
        request = client.build_request("GET", url, headers=VALID_AUTH)
        request.headers.pop("accept", None)
        return client.send(request)

    def test_no_accept_header_returns_binary(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage, job_id = self._completed_job(client_and_storage)
        r = self._get_without_accept(client, f"/api/v1/documents/{job_id}/download")
        assert r.status_code == 200, r.text
        result_bytes = (storage.job_dir(UUID(job_id)) / "result.md").read_bytes()
        assert r.content == result_bytes
        assert "content-disposition" in r.headers

    def test_wildcard_accept_returns_binary(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage, job_id = self._completed_job(client_and_storage)
        r = client.get(
            f"/api/v1/documents/{job_id}/download",
            headers={**VALID_AUTH, "Accept": "*/*"},
        )
        assert r.status_code == 200, r.text
        result_bytes = (storage.job_dir(UUID(job_id)) / "result.md").read_bytes()
        assert r.content == result_bytes

    def test_jsonx_accept_returns_binary(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        """Exact match only — ``application/jsonx`` must NOT be JSON."""
        client, storage, job_id = self._completed_job(client_and_storage)
        r = client.get(
            f"/api/v1/documents/{job_id}/download",
            headers={**VALID_AUTH, "Accept": "application/jsonx"},
        )
        assert r.status_code == 200, r.text
        result_bytes = (storage.job_dir(UUID(job_id)) / "result.md").read_bytes()
        assert r.content == result_bytes

    def test_json_with_q_parameter_returns_json(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _storage, job_id = self._completed_job(client_and_storage)
        r = client.get(
            f"/api/v1/documents/{job_id}/download",
            headers={**VALID_AUTH, "Accept": "application/json; q=0.9"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "content_base64" in body
        assert "filename" in body

    def test_json_with_wildcard_fallback_returns_json(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        """``application/json, */*`` — the explicit entry wins."""
        client, _storage, job_id = self._completed_job(client_and_storage)
        r = client.get(
            f"/api/v1/documents/{job_id}/download",
            headers={**VALID_AUTH, "Accept": "application/json, */*"},
        )
        assert r.status_code == 200, r.text
        assert "content_base64" in r.json()


class TestM2MErrorContract:
    def test_download_unknown_job_returns_404(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.get(f"/api/v1/documents/{uuid4()}/download", headers=VALID_AUTH)
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "job_not_found"

    def test_download_pending_job_returns_409(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, storage = client_and_storage
        job = Job(source_filename="note.md", source_ext="md", status=JobStatus.PENDING)
        storage.save_job(job)

        r = client.get(f"/api/v1/documents/{job.id}/download", headers=VALID_AUTH)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "job_not_ready"

    def test_upload_unsupported_extension_returns_400(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        r = client.post(
            "/api/v1/documents/",
            files={"file": ("bad.exe", b"binary", "application/octet-stream")},
            headers=VALID_AUTH,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsupported_format"

    def test_upload_too_large_returns_413(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        # Shrink the size cap via the settings override.
        app = client.app
        current = app.dependency_overrides[get_settings]()
        assert isinstance(current, Settings)
        app.dependency_overrides[get_settings] = lambda: current.model_copy(
            update={"max_file_size": 8}
        )

        r = client.post(
            "/api/v1/documents/",
            files={"file": ("note.md", b"x" * 32, "text/markdown")},
            headers=VALID_AUTH,
        )
        assert r.status_code == 413
        assert r.json()["detail"]["code"] == "file_too_large"


class TestM2MRegressionNoAuthorizationHeader:
    """FR-003/TD-007: requests without an Authorization header must keep
    working exactly as before (the UI flow never sends one)."""

    def test_upload_without_authorization_still_accepted(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id = _upload_md(client)  # no auth headers at all
        job = _wait_for_completion(client, job_id)
        assert job["status"] == "completed"

    def test_download_without_authorization_still_binary(
        self, client_and_storage: tuple[TestClient, LocalStorage]
    ) -> None:
        client, _ = client_and_storage
        job_id = _upload_md(client)
        job = _wait_for_completion(client, job_id)
        assert job["status"] == "completed"

        r = client.get(f"/api/v1/documents/{job_id}/download")
        assert r.status_code == 200, r.text
        assert "text/markdown" in r.headers["content-type"]

    def test_requests_without_header_pass_when_keys_unconfigured(
        self, no_keys_client: TestClient
    ) -> None:
        """M2M off must not affect header-less clients at all."""
        r = no_keys_client.get("/api/v1/documents/")
        # Listing is not an endpoint — 405 is fine, what matters is that
        # it is NOT a 401 (auth never triggers without the header).
        assert r.status_code != 401
