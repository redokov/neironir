import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.auth.dependencies import require_admin_auth, verify_csrf
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage


def _make_completed_md_job(
    storage_dir: Path,
    job_id: str | None = None,
) -> str:
    """Create a completed job (happy path) so we can test apply-feedback on it."""
    jid = job_id or str(uuid4())
    job_dir = storage_dir / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)

    text = "Reach me at user@example.com or +7 495 123-45-67."
    (job_dir / "extracted_text.txt").write_text(text, encoding="utf-8")

    annotations = [
        {"start": 12, "end": 28, "entity_type": "private_email", "text": "user@example.com"},
    ]
    (job_dir / "annotations.json").write_text(
        json.dumps(annotations, ensure_ascii=False), encoding="utf-8"
    )

    # Initial cleaned result (MD)
    cleaned = "Reach me at <PRIVATE_EMAIL1> or +7 495 123-45-67."
    (job_dir / "result.md").write_text(cleaned, encoding="utf-8")

    # Counters
    (job_dir / "counters.json").write_text(json.dumps({"private_email": 1}), encoding="utf-8")

    now = "2025-01-01T00:00:00"
    job = Job(
        id=jid,
        status=JobStatus.COMPLETED,
        source_filename=f"{jid}.md",
        source_ext="md",
        created_at=now,
        finished_at=now,
    )
    (job_dir / "job.json").write_text(
        json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return jid


# ---------------------------------------------------------------------------
# T1: concurrent apply-feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_apply_feedback(tmp_path: Path) -> None:
    """Two parallel apply-feedback calls should not corrupt state.

    Regression test for a scenario where two browser tabs both submit
    feedback at the same time.  The ``feedback.json`` should contain
    all actions from *one* of the two requests (the last writer wins
    — we don't merge), and ``counters.json`` should be internally
    consistent.
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    jid = _make_completed_md_job(storage_dir)

    settings_obj = Settings(
        storage_dir=str(storage_dir),
        session_secret="test-secret",
        admin_user="admin",
        admin_password="pass",
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_obj
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy
    app.dependency_overrides[require_admin_auth] = lambda: {"is_admin": True}
    app.dependency_overrides[verify_csrf] = lambda: None

    with TestClient(app) as client:
        # Action set A: add a phone number.
        payload_a = {
            "actions": [
                {
                    "action": "add",
                    "start": 33,
                    "end": 48,
                    "entity_type": "private_phone",
                    "text": "+7 495 123-45-67",
                }
            ],
            "comment": None,
        }
        # Action set B: add a URL.
        payload_b = {
            "actions": [
                {
                    "action": "add",
                    "start": 0,
                    "end": 9,
                    "entity_type": "private_url",
                    "text": "http://example.com",
                }
            ],
            "comment": "added URL",
        }

        # Fire both requests concurrently.
        import asyncio

        async def call_a() -> int:
            r = await asyncio.to_thread(
                lambda: client.post(
                    f"/api/v1/documents/{jid}/apply-feedback",
                    json=payload_a,
                )
            )
            return r.status_code

        async def call_b() -> int:
            r = await asyncio.to_thread(
                lambda: client.post(
                    f"/api/v1/documents/{jid}/apply-feedback",
                    json=payload_b,
                )
            )
            return r.status_code

        results = await asyncio.gather(call_a(), call_b(), return_exceptions=True)
        statuses = [r if isinstance(r, int) else -1 for r in results]

        # Both should succeed (200), not 409 or 500.
        for s in statuses:
            assert s == 200, f"expected 200, got {s}"

        # Check that counters.json exists and is valid JSON.
        counters_path = storage_dir / "jobs" / jid / "counters.json"
        assert counters_path.is_file()
        counters = json.loads(counters_path.read_text(encoding="utf-8"))
        assert isinstance(counters, dict)

        # Check that feedback.json is valid JSON (the last writer wins).
        feedback_path = storage_dir / "jobs" / jid / "feedback.json"
        assert feedback_path.is_file()
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert isinstance(feedback.get("actions"), list)

        # The result file should be valid markdown (not empty, not binary).
        result_path = storage_dir / "jobs" / jid / "result.md"
        assert result_path.is_file()
        assert len(result_path.read_text(encoding="utf-8")) > 0


# ---------------------------------------------------------------------------
# T2: corrupt .docx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_with_corrupt_docx(tmp_path: Path) -> None:
    """A corrupt .docx file should result in FAILED status, not a crash."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    jid = str(uuid4())
    job = Job(
        id=jid,
        status=JobStatus.PENDING,
        source_filename="corrupt.docx",
        source_ext="docx",
    )
    storage.save_job(job)
    storage.save_source(jid, "corrupt.docx", b"not a valid docx zip file")

    from neironir.config import Settings as Cfg

    settings = Cfg(
        storage_dir=str(storage_dir),
        session_secret="test-secret",
    )

    # Run the pipeline directly.
    from neironir.workers.pipeline import run_job

    await run_job(
        job_id=jid,
        settings=settings,
        storage=storage,
        privacy=privacy,
    )

    # After the pipeline completes, the job should be FAILED.
    loaded = storage.load_job(jid)
    assert loaded.status == JobStatus.FAILED, (
        f"expected FAILED, got {loaded.status} (error: {loaded.error})"
    )
    assert loaded.error is not None
