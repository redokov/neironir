"""Unit tests for the admin statistics aggregator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from neironir.admin.stats import (
    compute_documents_stats,
    compute_jobs_with_feedback,
)
from neironir.domain.job import Job, JobStatus


def _write_job(
    jobs_dir: Path,
    *,
    job_id: str | None = None,
    status: JobStatus,
    created_at: datetime,
    finished_at: datetime | None = None,
    error: str | None = None,
    with_feedback: bool = False,
    with_annotations: bool = False,
    feedback_actions: list[dict] | None = None,
    annotations: list[dict] | None = None,
) -> str:
    """Persist a synthetic Job + optional feedback/annotations under ``jobs/{id}``.

    Returns the actual job_id used (a UUID4 string if not provided).
    """
    real_id = job_id or str(uuid4())
    job_dir = jobs_dir / real_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=real_id,
        status=status,
        source_filename=f"{real_id}.md",
        source_ext="md",
        created_at=created_at,
        finished_at=finished_at,
        error=error,
    )
    (job_dir / "job.json").write_text(
        json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if with_annotations:
        (job_dir / "annotations.json").write_text(
            json.dumps(annotations or [], ensure_ascii=False),
            encoding="utf-8",
        )
    if with_feedback:
        actions = feedback_actions or []
        (job_dir / "feedback.json").write_text(
            json.dumps(
                {"job_id": real_id, "actions": actions, "comment": None},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return real_id


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    """Return a temp storage dir with a freshly created ``jobs/`` subdir."""
    storage = tmp_path / "storage"
    (storage / "jobs").mkdir(parents=True)
    return storage


class TestComputeDocumentsStats:
    def test_returns_zeros_when_jobs_dir_missing(self, tmp_path: Path) -> None:
        storage = tmp_path / "empty"
        storage.mkdir()
        result = compute_documents_stats(storage)
        assert result.total_jobs == 0
        assert result.completed_jobs == 0
        assert result.failed_jobs == 0
        assert result.jobs_with_feedback == 0
        assert result.by_day == {}

    def test_counts_jobs_by_status(self, storage_dir: Path) -> None:
        now = datetime.now()
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=now,
            finished_at=now,
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=now,
            finished_at=now,
            with_feedback=True,
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.FAILED,
            created_at=now,
            error="boom",
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.PENDING,
            created_at=now,
        )

        result = compute_documents_stats(storage_dir)
        assert result.total_jobs == 4
        assert result.completed_jobs == 2
        assert result.failed_jobs == 1
        assert result.jobs_with_feedback == 1

    def test_bucket_by_day(self, storage_dir: Path) -> None:
        old = datetime.now() - timedelta(days=3)
        new = datetime.now()
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=old,
            finished_at=old,
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=new,
            finished_at=new,
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=new,
            finished_at=new,
        )

        result = compute_documents_stats(storage_dir, period="day")
        assert result.total_jobs == 3
        assert sum(result.by_day.values()) == 3

    def test_skips_unreadable_job_files(self, storage_dir: Path) -> None:
        jobs_dir = storage_dir / "jobs"
        (jobs_dir / "broken").mkdir()
        (jobs_dir / "broken" / "job.json").write_text("{not json", encoding="utf-8")

        result = compute_documents_stats(storage_dir)
        assert result.total_jobs == 0

    def test_since_filter(self, storage_dir: Path) -> None:
        old = datetime(2020, 1, 1)
        new = datetime(2030, 1, 1)
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=old,
            finished_at=old,
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=new,
            finished_at=new,
        )

        result = compute_documents_stats(
            storage_dir,
            since=datetime(2025, 1, 1),
        )
        assert result.total_jobs == 1


class TestComputeJobsWithFeedback:
    def test_returns_empty_when_no_jobs(self, tmp_path: Path) -> None:
        storage = tmp_path / "empty"
        storage.mkdir()
        assert compute_jobs_with_feedback(storage) == []

    def test_lists_only_jobs_with_feedback(self, storage_dir: Path) -> None:
        now = datetime.now()
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=now,
            finished_at=now,
            with_feedback=True,
            feedback_actions=[
                {
                    "action": "confirm",
                    "entity_type": "private_email",
                    "start": 0,
                    "end": 5,
                    "text": "foo@x",
                },
                {
                    "action": "add",
                    "entity_type": "private_phone",
                    "start": 6,
                    "end": 18,
                    "text": "+7 (495) 1",
                },
                {
                    "action": "reject",
                    "entity_type": "private_date",
                    "start": 19,
                    "end": 29,
                    "text": "01.01.2024",
                },
            ],
            with_annotations=True,
            annotations=[{"start": 0, "end": 5, "entity_type": "private_email", "text": "foo@x"}],
        )
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=now,
            finished_at=now,
        )

        rows = compute_jobs_with_feedback(storage_dir)
        assert len(rows) == 1
        row = rows[0]
        assert row.confirmed == 1
        assert row.added == 1
        assert row.rejected == 1
        assert row.detected_spans == 1
        assert row.corrections_by_type == {"private_email": 1}
        assert row.missed_by_type == {"private_phone": 1}
        assert row.false_positive_by_type == {"private_date": 1}

    def test_respects_limit(self, storage_dir: Path) -> None:
        now = datetime.now()
        for _ in range(5):
            _write_job(
                storage_dir / "jobs",
                status=JobStatus.COMPLETED,
                created_at=now,
                finished_at=now,
                with_feedback=True,
            )
        rows = compute_jobs_with_feedback(storage_dir, limit=2)
        assert len(rows) == 2

    def test_sorted_most_recent_first(self, storage_dir: Path) -> None:
        """Rows must be ordered by reviewer activity (finished_at), newest
        first — directory names are random UUIDs and say nothing about
        recency."""
        base = datetime.now()
        ids: dict[int, str] = {}
        for days_ago in (5, 1, 3):
            ts = base - timedelta(days=days_ago)
            ids[days_ago] = _write_job(
                storage_dir / "jobs",
                status=JobStatus.COMPLETED,
                created_at=ts,
                finished_at=ts,
                with_feedback=True,
            )

        rows = compute_jobs_with_feedback(storage_dir)
        assert [r.job_id for r in rows] == [ids[1], ids[3], ids[5]]

    def test_limit_returns_most_recent(self, storage_dir: Path) -> None:
        """The limit must keep the *most recent* N jobs, not an arbitrary
        subset in directory-iteration order."""
        base = datetime.now()
        ids: dict[int, str] = {}
        for days_ago in range(5):
            ts = base - timedelta(days=days_ago)
            ids[days_ago] = _write_job(
                storage_dir / "jobs",
                status=JobStatus.COMPLETED,
                created_at=ts,
                finished_at=ts,
                with_feedback=True,
            )

        rows = compute_jobs_with_feedback(storage_dir, limit=2)
        assert [r.job_id for r in rows] == [ids[0], ids[1]]

    def test_falls_back_to_created_at_when_unfinished(self, storage_dir: Path) -> None:
        """Jobs without ``finished_at`` (e.g. still pending) are ordered
        by ``created_at``."""
        base = datetime.now()
        old_finished = base - timedelta(days=1)
        new_pending = base
        finished_id = _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=old_finished,
            finished_at=old_finished,
            with_feedback=True,
        )
        pending_id = _write_job(
            storage_dir / "jobs",
            status=JobStatus.PENDING,
            created_at=new_pending,
            finished_at=None,
            with_feedback=True,
        )

        rows = compute_jobs_with_feedback(storage_dir)
        assert [r.job_id for r in rows] == [pending_id, finished_id]

    def test_has_comment_flag(self, storage_dir: Path) -> None:
        now = datetime.now()
        real_id = str(uuid4())
        job_dir = storage_dir / "jobs" / real_id
        job_dir.mkdir(parents=True)
        job = Job(
            id=real_id,
            status=JobStatus.COMPLETED,
            source_filename="x.md",
            source_ext="md",
            created_at=now,
            finished_at=now,
        )
        (job_dir / "job.json").write_text(json.dumps(job.to_dict()), encoding="utf-8")
        (job_dir / "feedback.json").write_text(
            json.dumps({"actions": [], "comment": "Ложное срабатывание на дате"}),
            encoding="utf-8",
        )

        rows = compute_jobs_with_feedback(storage_dir)
        assert rows[0].has_comment is True

    def test_missing_annotations_treated_as_zero(self, storage_dir: Path) -> None:
        now = datetime.now()
        _write_job(
            storage_dir / "jobs",
            status=JobStatus.COMPLETED,
            created_at=now,
            finished_at=now,
            with_feedback=True,
            # no annotations.json
        )
        rows = compute_jobs_with_feedback(storage_dir)
        assert rows[0].detected_spans == 0

    def test_skips_unreadable_jobs(self, storage_dir: Path) -> None:
        bad = storage_dir / "jobs" / "broken"
        bad.mkdir()
        (bad / "feedback.json").write_text("{}", encoding="utf-8")
        # missing job.json → from_dict raises

        rows = compute_jobs_with_feedback(storage_dir)
        assert rows == []
