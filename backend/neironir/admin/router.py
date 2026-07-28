"""HTTP endpoints for the admin dashboard.

The admin UI is served at ``/admin`` (a static HTML page) and talks to
the JSON API under ``/api/v1/admin/*``.

Endpoints
---------

* ``GET  /api/v1/admin/stats`` — top-line counters + per-day buckets.
* ``GET  /api/v1/admin/documents`` — list of jobs with feedback.
* ``GET  /api/v1/admin/documents/{job_id}`` — per-job drill-down
  (annotations + feedback actions side by side).
* ``POST /api/v1/admin/training/start`` — build dataset + spawn
  ``opf train``.  Idempotent: a 409 is returned if a run is in flight.
* ``GET  /api/v1/admin/training/status`` — poll the shared
  :class:`TrainingState` snapshot.
* ``POST /api/v1/admin/training/stop`` — SIGTERM the running subprocess.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from neironir.admin.stats import (
    JobFeedbackSummary,
    Period,
    compute_documents_stats,
    compute_jobs_with_feedback,
)
from neironir.admin.training import (
    TrainingState,
    get_training_state,
    start_training_from_feedback,
    stop_training,
)
from neironir.api.dependencies import get_settings
from neironir.api.schemas import ErrorResponse
from neironir.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    responses={500: {"model": ErrorResponse, "description": "Internal error"}},
)
async def get_stats(
    period: Period = Query("day"),
    days: int = Query(30, ge=1, le=365),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return aggregated document counters.

    Args:
        period: Bucket granularity — ``day`` (default), ``week`` or ``month``.
        days: Window length in days for the per-period buckets.
    """
    from datetime import datetime, timedelta

    since = datetime.now() - timedelta(days=days)
    stats = compute_documents_stats(
        Path(settings.storage_dir),
        period=period,
        since=since,
    )
    return stats.to_dict()


@router.get("/documents")
async def list_documents(
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, object]]:
    """List the most recent jobs that have user feedback."""
    summaries = compute_jobs_with_feedback(Path(settings.storage_dir), limit=limit)
    return [_serialize_summary(s) for s in summaries]


@router.get(
    "/documents/{job_id}",
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
async def get_document_detail(
    job_id: UUID,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return the full annotation + feedback drill-down for one job."""
    storage_dir = Path(settings.storage_dir)
    job_dir = storage_dir / "jobs" / str(job_id)
    if not job_dir.is_dir():
        raise _not_found(f"Job {job_id} not found")

    job_path = job_dir / "job.json"
    if not job_path.is_file():
        raise _not_found(f"Job {job_id} metadata missing")

    text_path = job_dir / "extracted_text.txt"
    annotations_path = job_dir / "annotations.json"
    feedback_path = job_dir / "feedback.json"

    text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
    annotations: list[dict[str, object]] = []
    if annotations_path.is_file():
        try:
            annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            annotations = []

    feedback: dict[str, object] | None = None
    if feedback_path.is_file():
        try:
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            feedback = None

    return {
        "job_id": str(job_id),
        "job": json.loads(job_path.read_text(encoding="utf-8")),
        "text": text,
        "annotations": annotations,
        "feedback": feedback,
    }


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


@router.post(
    "/training/start",
    responses={
        409: {"model": ErrorResponse, "description": "Training already running"},
        422: {"model": ErrorResponse, "description": "Invalid input"},
    },
)
async def start_training_endpoint(
    epochs: int = Query(3, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Build a JSONL dataset from feedback and spawn ``opf train``.

    Args:
        epochs: Number of training epochs to forward to ``opf train``.
    """
    storage_dir = Path(settings.storage_dir)
    output_dir = storage_dir / "checkpoints" / _now_iso()
    opf_cmd = _opf_cmd(settings)

    try:
        state = await start_training_from_feedback(
            storage_dir=storage_dir,
            output_dir=output_dir,
            opf_cmd=opf_cmd,
            epochs=epochs,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                code="training_in_progress",
                message=str(exc),
            ).model_dump(),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — surface any startup error
        logger.exception("failed to start training")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                code="training_failed",
                message=str(exc),
            ).model_dump(),
        ) from exc

    return _serialize_state(state)


@router.get("/training/status")
async def training_status() -> dict[str, object]:
    """Return a snapshot of the shared :class:`TrainingState`."""
    return _serialize_state(get_training_state())


@router.post("/training/stop")
async def stop_training_endpoint() -> dict[str, object]:
    """Ask the running subprocess to terminate."""
    sent = await stop_training()
    return {"status": "stopping", "signal_sent": sent}


# ---------------------------------------------------------------------------
# Admin UI static page
# ---------------------------------------------------------------------------


@router.get("/ui", include_in_schema=False)
async def admin_ui(
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve the admin dashboard HTML page."""
    return FileResponse(settings.frontend_path / "admin.html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_state(state: TrainingState) -> dict[str, object]:
    """Return the JSON snapshot for a :class:`TrainingState`."""
    return state.to_dict()


def _serialize_summary(summary: JobFeedbackSummary) -> dict[str, object]:
    """Return the JSON dict for a :class:`JobFeedbackSummary`."""
    return {
        "job_id": summary.job_id,
        "source_filename": summary.source_filename,
        "status": summary.status,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "finished_at": summary.finished_at.isoformat() if summary.finished_at else None,
        "detected_spans": summary.detected_spans,
        "confirmed": summary.confirmed,
        "rejected": summary.rejected,
        "added": summary.added,
        "has_comment": summary.has_comment,
        "corrections_by_type": dict(summary.corrections_by_type),
        "missed_by_type": dict(summary.missed_by_type),
        "false_positive_by_type": dict(summary.false_positive_by_type),
    }


def _opf_cmd(settings: Settings) -> list[str]:
    """Build the ``opf`` command tokenised for subprocess exec.

    Tries ``shlex.split`` first because that is the portable POSIX way
    to honour quoting.  Falls back to a naive ``.split()`` if the
    tokenizer misbehaves on a Windows path with backslashes (a known
    ``shlex`` limitation).
    """
    raw = settings.privacy_filter_cmd.strip()
    if not raw:
        return ["opf"]

    # First word is the executable — the path may contain backslashes
    # that ``shlex.split`` would mangle on Windows.  Split it off and
    # parse the rest with ``shlex`` to honour any quoting.
    import shlex

    parts = raw.split(None, 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if rest:
        try:
            return [head] + shlex.split(rest, posix=True)
        except ValueError:
            return [head] + rest.split()
    return [head]


def _now_iso() -> str:
    """Return a filesystem-safe UTC timestamp."""
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _not_found(detail: str) -> HTTPException:
    """Build a 404 envelope consistent with the rest of the API."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorResponse(code="not_found", message=detail).model_dump(),
    )


__all__ = ["router"]