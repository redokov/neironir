"""Aggregate statistics for the admin dashboard.

The admin UI shows two numbers up front:

* **Total processed documents** — every job ever created (regardless of
  status).  Optionally filtered to a time window (``since`` /
  ``until``).
* **Total documents with feedback** — jobs that the human reviewer
  actually opened and either confirmed or corrected.  This is the
  signal that drives the auto-rules loop and the model fine-tuning.

Counts are derived from ``storage/jobs/*/job.json`` (one per
:class:`~neironir.domain.job.Job`) and from ``storage/jobs/*/feedback.json``
(only present when the user submitted corrections).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from neironir.domain.job import Job, JobStatus

logger = logging.getLogger(__name__)


# Time-bucket granularity used by the ``/stats`` endpoint when the UI
# asks for a breakdown over time.
Period = Literal["day", "week", "month"]


@dataclass(frozen=True)
class DocumentsStats:
    """Top-line counters shown on the admin dashboard."""

    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    jobs_with_feedback: int = 0
    # Bucketed counts for the period selector (UTC dates).
    by_day: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly ``dict`` for the HTTP layer."""
        return asdict(self)


@dataclass(frozen=True)
class JobFeedbackSummary:
    """Per-document summary that the admin UI lists in the feedback tab."""

    job_id: str
    source_filename: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    # Number of detected entity spans the model produced.
    detected_spans: int
    # Breakdown of feedback actions.
    confirmed: int
    rejected: int
    added: int
    has_comment: bool
    # Counts by entity type ("private_person" → 4, …).
    corrections_by_type: dict[str, int] = field(default_factory=dict)
    missed_by_type: dict[str, int] = field(default_factory=dict)
    false_positive_by_type: dict[str, int] = field(default_factory=dict)


def compute_documents_stats(
    storage_dir: Path,
    *,
    period: Period = "day",
    since: datetime | None = None,
    until: datetime | None = None,
) -> DocumentsStats:
    """Walk the ``jobs/`` directory and compute top-line counters.

    Args:
        storage_dir: Path to the storage root (``jobs/`` lives inside).
        period: Granularity of the time-bucket histogram.  Only used
            when ``since``/``until`` is provided.
        since: Lower bound (inclusive) for the ``by_day`` bucket
            counts.  ``None`` means "no lower bound".
        until: Upper bound (exclusive) for the ``by_day`` bucket
            counts.  ``None`` means "no upper bound".

    Returns:
        A :class:`DocumentsStats` instance ready for JSON.
    """
    jobs_dir = Path(storage_dir) / "jobs"
    if not jobs_dir.is_dir():
        return DocumentsStats()

    total = 0
    completed = 0
    failed = 0
    with_feedback = 0
    by_day: dict[str, int] = {}

    for job_dir in sorted(jobs_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        job_path = job_dir / "job.json"
        if not job_path.is_file():
            continue
        try:
            job = Job.from_dict(json.loads(job_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("skipping unreadable job %s: %s", job_dir.name, exc)
            continue

        # Apply the time-window filter before counting so the totals
        # agree with the bucket counts.
        if since is not None and job.created_at < since:
            continue
        if until is not None and job.created_at >= until:
            continue

        total += 1
        if job.status == JobStatus.COMPLETED:
            completed += 1
        elif job.status == JobStatus.FAILED:
            failed += 1

        if (job_dir / "feedback.json").is_file():
            with_feedback += 1

        bucket_key = _bucket_key(job.created_at, period)
        by_day[bucket_key] = by_day.get(bucket_key, 0) + 1

    return DocumentsStats(
        total_jobs=total,
        completed_jobs=completed,
        failed_jobs=failed,
        jobs_with_feedback=with_feedback,
        by_day=by_day,
    )


def compute_jobs_with_feedback(
    storage_dir: Path,
    *,
    limit: int = 50,
) -> list[JobFeedbackSummary]:
    """List the most recent jobs that have user feedback.

    The admin UI uses this list as the entry point for the per-document
    feedback drill-down.  Sorted by ``finished_at`` (or ``created_at``
    if the job never finished) descending so the most recent reviewer
    activity is on top.

    Args:
        storage_dir: Path to the storage root.
        limit: Maximum number of summaries to return (default 50,
            the admin page size).

    Returns:
        A list of :class:`JobFeedbackSummary` instances.  Jobs that
        have no ``feedback.json`` are silently skipped.
    """
    jobs_dir = Path(storage_dir) / "jobs"
    if not jobs_dir.is_dir():
        return []

    results: list[JobFeedbackSummary] = []
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        feedback_path = job_dir / "feedback.json"
        if not feedback_path.is_file():
            continue

        try:
            job = Job.from_dict(json.loads((job_dir / "job.json").read_text(encoding="utf-8")))
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("skipping unreadable job %s: %s", job_dir.name, exc)
            continue

        annotations_path = job_dir / "annotations.json"
        detected = 0
        if annotations_path.is_file():
            try:
                detected = len(json.loads(annotations_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                detected = 0

        confirmed = rejected = added = 0
        corrections_by_type: dict[str, int] = {}
        missed_by_type: dict[str, int] = {}
        false_positive_by_type: dict[str, int] = {}
        for action in feedback.get("actions", []):
            etype = action.get("entity_type", "unknown")
            kind = action.get("action")
            if kind == "confirm":
                confirmed += 1
                corrections_by_type[etype] = corrections_by_type.get(etype, 0) + 1
            elif kind == "reject":
                rejected += 1
                false_positive_by_type[etype] = false_positive_by_type.get(etype, 0) + 1
            elif kind == "add":
                added += 1
                missed_by_type[etype] = missed_by_type.get(etype, 0) + 1

        results.append(
            JobFeedbackSummary(
                job_id=job_dir.name,
                source_filename=job.source_filename,
                status=str(job.status.value if hasattr(job.status, "value") else job.status),
                created_at=job.created_at,
                finished_at=job.finished_at,
                detected_spans=detected,
                confirmed=confirmed,
                rejected=rejected,
                added=added,
                has_comment=bool(feedback.get("comment")),
                corrections_by_type=corrections_by_type,
                missed_by_type=missed_by_type,
                false_positive_by_type=false_positive_by_type,
            )
        )

    # Sort by reviewer activity, most recent first: ``finished_at`` when
    # the job completed, falling back to ``created_at``.  Directory names
    # are random UUIDs, so iteration order says nothing about recency —
    # the limit must be applied only *after* sorting.
    results.sort(key=lambda r: r.finished_at or r.created_at, reverse=True)
    return results[:limit]


def _bucket_key(ts: datetime, period: Period) -> str:
    """Return the bucket key for ``ts`` at the requested ``period``.

    Day buckets are UTC calendar dates (``YYYY-MM-DD``).
    Week buckets are ISO Monday-prefixed dates (``YYYY-Www``).
    Month buckets are ``YYYY-MM``.
    """
    if period == "week":
        iso = ts.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "month":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")


__all__ = [
    "DocumentsStats",
    "JobFeedbackSummary",
    "Period",
    "compute_documents_stats",
    "compute_jobs_with_feedback",
]
