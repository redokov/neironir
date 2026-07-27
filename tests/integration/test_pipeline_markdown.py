"""End-to-end pipeline test for the markdown format."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage
from neironir.workers.pipeline import run_job


async def test_run_job_redacts_markdown(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    settings = Settings()
    privacy = MockPrivacyFilterClient()

    job_id = uuid4()
    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename="notes.md",
        source_ext="md",
    )
    storage.save_job(job)

    source_text = "Reach me at user@example.com or at +7 495 123-45-67."
    storage.save_source(job_id, "notes.md", source_text.encode("utf-8"))

    await run_job(job_id, settings=settings, storage=storage, privacy=privacy)

    final = storage.load_job(job_id)
    assert final.status == JobStatus.COMPLETED
    assert final.error is None
    assert final.finished_at is not None

    result_path = storage.job_dir(job_id) / "result.md"
    cleaned = result_path.read_text(encoding="utf-8")
    assert "<PRIVATE_EMAIL1>" in cleaned
    assert "<PRIVATE_PHONE1>" in cleaned
    assert "user@example.com" not in cleaned
    assert "+7 495 123-45-67" not in cleaned
