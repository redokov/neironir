"""HTTP-level integration tests for the admin API."""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neironir.admin.training import reset_training_state
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.domain.job import Job, JobStatus
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage

# Override auth dependencies so integration tests don't need real cookies.
from neironir.auth.dependencies import require_admin_auth, verify_csrf
from neironir.auth.session import SESSION_PAYLOAD_KEY, sign_session_cookie


def _write_feedback_job(
    storage_dir: Path,
    *,
    status: JobStatus,
    with_feedback: bool,
    with_annotations: bool = True,
    feedback_actions: list[dict] | None = None,
    text: str = "Reach me at user@example.com",
    annotations: list[dict] | None = None,
    error: str | None = None,
) -> str:
    """Persist a synthetic completed job (and optionally feedback)."""
    job_id = str(uuid4())
    job_dir = storage_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    job = Job(
        id=job_id,
        status=status,
        source_filename=f"{job_id}.md",
        source_ext="md",
        created_at=now,
        finished_at=now if status in {JobStatus.COMPLETED, JobStatus.FAILED} else None,
        error=error,
    )
    (job_dir / "job.json").write_text(
        json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (job_dir / "extracted_text.txt").write_text(text, encoding="utf-8")
    if with_annotations:
        default_annotation = {
            "start": 12,
            "end": 28,
            "entity_type": "private_email",
            "text": "user@example.com",
        }
        (job_dir / "annotations.json").write_text(
            json.dumps(annotations or [default_annotation], ensure_ascii=False),
            encoding="utf-8",
        )
    if with_feedback:
        (job_dir / "feedback.json").write_text(
            json.dumps(
                {"job_id": job_id, "actions": feedback_actions or [], "comment": None},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return job_id


class _SettingsHandle:
    """Helper that exposes a mutable settings override for tests.

    The admin endpoints call ``get_settings()`` indirectly through the
    FastAPI dependency graph, which ``dependency_overrides`` redirects
    to a single :class:`Settings` instance.  Setting ``privacy_filter_cmd``
    on this handle affects the value the API will see.
    """

    def __init__(self, base):
        self._base = base

    def set_cmd(self, cmd: str) -> None:
        self._base.privacy_filter_cmd = cmd


@pytest.fixture
def client_and_storage(
    tmp_path: Path,
) -> Generator[tuple[TestClient, Path, _SettingsHandle], None, None]:
    """Build a FastAPI client with overridden storage and mock privacy."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    # Build a settings object rooted at the tmp storage so the admin
    # endpoints (which read ``settings.storage_dir`` directly) operate
    # on the same directory as the per-job storage we just built.
    real_settings = get_settings().model_copy(
        update={
            "storage_dir": str(storage_dir),
            "session_secret": "test-secret-for-admin-tests",
        }
    )
    settings_handle = _SettingsHandle(real_settings)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy
    # Bypass auth for integration tests — the e2e tests cover auth properly.
    app.dependency_overrides[require_admin_auth] = lambda: {"is_admin": True, "user": "test"}
    app.dependency_overrides[verify_csrf] = lambda: None

    reset_training_state()

    with TestClient(app) as client:
        yield client, storage_dir, settings_handle

    reset_training_state()
    shutil.rmtree(storage_dir, ignore_errors=True)


class TestAdminStats:
    def test_stats_returns_zero_baseline(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _storage, _h = client_and_storage
        r = client.get("/api/v1/admin/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_jobs"] == 0
        assert body["completed_jobs"] == 0
        assert body["failed_jobs"] == 0
        assert body["jobs_with_feedback"] == 0
        assert isinstance(body["by_day"], dict)

    def test_stats_counts_real_jobs(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, _h = client_and_storage
        _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=True)
        _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=False)
        _write_feedback_job(storage, status=JobStatus.FAILED, with_feedback=False, error="x")

        body = client.get("/api/v1/admin/stats").json()
        assert body["total_jobs"] == 3
        assert body["completed_jobs"] == 2
        assert body["failed_jobs"] == 1
        assert body["jobs_with_feedback"] == 1
        assert sum(body["by_day"].values()) == 3

    def test_stats_period_parameter(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, _h = client_and_storage
        _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=False)

        body = client.get("/api/v1/admin/stats?period=week&days=90").json()
        assert body["total_jobs"] == 1

    def test_stats_days_validation(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        r = client.get("/api/v1/admin/stats?days=0")
        assert r.status_code == 422


class TestAdminDocuments:
    def test_documents_empty(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        r = client.get("/api/v1/admin/documents")
        assert r.status_code == 200
        assert r.json() == []

    def test_documents_lists_jobs_with_feedback(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, _h = client_and_storage
        _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=True)
        _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=False)

        rows = client.get("/api/v1/admin/documents").json()
        assert len(rows) == 1
        row = rows[0]
        assert row["detected_spans"] == 1
        assert row["status"] == "completed"

    def test_documents_limit(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, _h = client_and_storage
        for _ in range(3):
            _write_feedback_job(storage, status=JobStatus.COMPLETED, with_feedback=True)

        r = client.get("/api/v1/admin/documents?limit=2")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_documents_detail(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, _h = client_and_storage
        job_id = _write_feedback_job(
            storage,
            status=JobStatus.COMPLETED,
            with_feedback=True,
            feedback_actions=[
                {
                    "action": "add",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                }
            ],
        )
        r = client.get(f"/api/v1/admin/documents/{job_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["job_id"] == job_id
        assert body["text"] == "Reach me at user@example.com"
        assert len(body["annotations"]) == 1
        assert body["feedback"] is not None
        assert body["feedback"]["actions"][0]["entity_type"] == "private_email"

    def test_documents_detail_404(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        missing = uuid4()
        r = client.get(f"/api/v1/admin/documents/{missing}")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"


class TestAdminTrainingEndpoints:
    def test_status_initial(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        body = client.get("/api/v1/admin/training/status").json()
        assert body["status"] == "idle"
        assert body["pid"] is None
        assert body["progress"] == {
            "epoch": 0,
            "total_epochs": 0,
            "loss": None,
            "eta_seconds": None,
        }

    def test_stop_when_idle(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        r = client.post("/api/v1/admin/training/stop")
        assert r.status_code == 200
        body = r.json()
        assert body["signal_sent"] is False

    def test_start_marks_failed_when_no_feedback(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        # No feedback jobs → dataset is empty, state ends up ``failed``.
        r = client.post("/api/v1/admin/training/start?epochs=1")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"
        assert body["error"] is not None

    def test_start_builds_dataset_and_returns_state(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, settings_handle = client_and_storage
        _write_feedback_job(
            storage,
            status=JobStatus.COMPLETED,
            with_feedback=True,
            feedback_actions=[
                {
                    "action": "add",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                }
            ],
        )

        # Build a one-shot shell command: ``<python> -c "<script>"``.
        # We quote the script via JSON so that embedded quotes survive
        # ``shlex.split`` on Windows.
        import json as _json
        script = "print('epoch=1 loss=0.1')"
        cmd = f"{sys.executable} -c {_json.dumps(script)}"
        settings_handle.set_cmd(cmd)

        r = client.post("/api/v1/admin/training/start?epochs=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] in {"running", "completed"}, f"unexpected state: {body!r}"
        assert body["progress"]["total_epochs"] == 1
        assert body["dataset_path"] is not None

        for _ in range(50):
            cur = client.get("/api/v1/admin/training/status").json()
            if cur["status"] != "running":
                break
            time.sleep(0.1)
        assert cur["status"] in {"completed", "failed"}

    def test_start_409_when_already_running(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, storage, settings_handle = client_and_storage
        _write_feedback_job(
            storage,
            status=JobStatus.COMPLETED,
            with_feedback=True,
            feedback_actions=[
                {
                    "action": "add",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                }
            ],
        )

        # Sleep long enough that the second POST finds the state still
        # in ``RUNNING``.
        import json as _json
        script = "import time; time.sleep(30)"
        cmd = f"{sys.executable} -c {_json.dumps(script)}"
        settings_handle.set_cmd(cmd)

        r1 = client.post("/api/v1/admin/training/start?epochs=1")
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["status"] == "running", body1

        r2 = client.post("/api/v1/admin/training/start?epochs=1")
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "training_in_progress"

        stop = client.post("/api/v1/admin/training/stop").json()
        assert stop["signal_sent"] is True

        for _ in range(50):
            cur = client.get("/api/v1/admin/training/status").json()
            if cur["status"] != "running":
                break
            time.sleep(0.1)


class TestAdminUI:
    def test_admin_html_served(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        client, _s, _h = client_and_storage
        # The AdminUIAuthMiddleware requires a valid signed cookie.
        session_value = sign_session_cookie(
            {SESSION_PAYLOAD_KEY: True, "user": "test"},
            secret=_h._base.session_secret,
        )
        client.cookies.set("neironir_session", session_value)
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Админка" in r.text

    def test_admin_ui_api_path_removed(
        self, client_and_storage: tuple[TestClient, Path, _SettingsHandle]
    ) -> None:
        """The /api/v1/admin/ui endpoint has been removed in favour of
        ``GET /admin`` served via the ui router + middleware."""
        client, _s, _h = client_and_storage
        r = client.get("/api/v1/admin/ui")
        # Should 404 since the route no longer exists.
        assert r.status_code == 404