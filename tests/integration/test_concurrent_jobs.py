"""Run several jobs through the pipeline at the same time.

The pipeline is invoked from FastAPI's ``BackgroundTasks``, which
schedules coroutines on the same event loop. We submit N uploads
sequentially (the loop schedules them as tasks that then run
"concurrently" from the test's perspective) and verify that all jobs
complete and the placeholder counter is independent per document.
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


def _wait_for_completion(client: TestClient, job_id: str, max_iters: int = 50) -> str:
    """Poll until the job leaves the PENDING/PROCESSING state."""
    for _ in range(max_iters):
        response = client.get(f"/api/v1/documents/{job_id}")
        assert response.status_code == 200
        status = response.json()["status"]
        if status in {"completed", "failed"}:
            return status
    raise AssertionError(f"job {job_id} did not complete in {max_iters} iterations")


def test_three_concurrent_jobs_all_complete(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """Three uploads submitted back-to-back all complete successfully."""
    client, _storage = client_and_storage
    samples = [
        ("a.md", b"alpha@example.com"),
        ("b.md", b"beta@example.com and +7 495 123-45-67"),
        ("c.md", b"https://example.com/x and 1234567890123456"),
    ]

    job_ids: list[str] = []
    for filename, body in samples:
        response = client.post(
            "/api/v1/documents/",
            files={"file": (filename, body, "text/markdown")},
        )
        assert response.status_code == 202
        job_ids.append(response.json()["id"])

    # Wait for all three to complete.
    statuses = [_wait_for_completion(client, job_id) for job_id in job_ids]
    assert statuses == ["completed", "completed", "completed"]


def test_concurrent_jobs_have_independent_placeholder_counters(
    client_and_storage: tuple[TestClient, LocalStorage],
) -> None:
    """Each document starts its own ``PRIVATE_*1>`` counter.

    This guards against an accidental future regression that would
    share the counter across jobs — a hard-to-spot bug because the
    pipeline is a single process.
    """
    client, storage = client_and_storage
    body = b"a@b.com and c@d.com and e@f.com"

    job_ids: list[str] = []
    for i in range(3):
        response = client.post(
            "/api/v1/documents/",
            files={"file": (f"f{i}.md", body, "text/markdown")},
        )
        assert response.status_code == 202
        job_ids.append(response.json()["id"])

    statuses = [_wait_for_completion(client, job_id) for job_id in job_ids]
    assert all(status == "completed" for status in statuses)

    # Each result file should have EMAIL1, EMAIL2, EMAIL3 — independent counters.
    for job_id in job_ids:
        result_path = storage.root / "jobs" / job_id / "result.md"
        text = result_path.read_text(encoding="utf-8")
        assert "<PRIVATE_EMAIL1>" in text
        assert "<PRIVATE_EMAIL2>" in text
        assert "<PRIVATE_EMAIL3>" in text
