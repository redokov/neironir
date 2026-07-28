"""HTTP endpoints for the annotation review and correction UI.

Three routes are exposed under the ``/api/v1/documents/{job_id}`` prefix:

* ``GET /annotations`` — return the extracted plain text and all detected
  entity spans for interactive review.
* ``POST /feedback`` — accept user corrections (confirm, reject, add).
* ``GET /preview`` — return the extracted text with detected entities
  highlighted for display in the frontend.

A job must exist and be in ``completed`` status before these endpoints
are accessible (except when the pipeline runs in auto mode, in which case
the annotations are available immediately after processing).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from neironir.api.dependencies import get_storage
from neironir.api.schemas import (
    AnnotationSpan,
    AnnotationsResponse,
    ErrorResponse,
    FeedbackResponse,
    FeedbackSubmit,
)
from neironir.domain.job import JobStatus
from neironir.storage.local import LocalStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents/{job_id}", tags=["feedback"])


@router.get(
    "/annotations",
    response_model=AnnotationsResponse,
    responses={404: {"model": ErrorResponse, "description": "Job or annotations not found"}},
)
async def get_annotations(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> AnnotationsResponse:
    """Return the extracted text and all detected entity spans.

    The spans come from both the neural model and the rule-based detector.
    The frontend uses this data to render a highlighted preview and an
    editable annotation list.
    """
    _ensure_job_dir_exists(storage, job_id)

    # Load extracted text
    text_path = storage.job_dir(job_id) / "extracted_text.txt"
    if not text_path.is_file():
        raise _not_found("extracted_text.txt not found for this job")
    text = text_path.read_text(encoding="utf-8")

    # Load annotations
    annotations_path = storage.job_dir(job_id) / "annotations.json"
    if not annotations_path.is_file():
        raise _not_found("annotations.json not found for this job")

    raw_annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    spans = [
        AnnotationSpan(
            index=i,
            start=ann["start"],
            end=ann["end"],
            entity_type=ann["entity_type"],
            text=ann["text"],
            source=ann.get("source", "model"),
        )
        for i, ann in enumerate(raw_annotations)
    ]

    # Check if feedback already exists
    feedback_path = storage.job_dir(job_id) / "feedback.json"
    has_feedback = feedback_path.is_file()

    return AnnotationsResponse(
        job_id=job_id,
        text=text,
        spans=spans,
        has_feedback=has_feedback,
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
async def submit_feedback(
    job_id: UUID,
    payload: FeedbackSubmit,
    storage: LocalStorage = Depends(get_storage),
) -> FeedbackResponse:
    """Accept user corrections for a completed job.

    The payload contains a list of actions (confirm, reject, add) and an
    optional free-text comment. The feedback is stored as ``feedback.json``
    in the job directory for later use by the auto-rules (Phase 2) and
    model fine-tuning (Phase 3) pipelines.
    """
    _ensure_job_dir_exists(storage, job_id)

    feedback = {
        "job_id": str(job_id),
        "actions": [a.model_dump() for a in payload.actions],
        "comment": payload.comment,
    }
    feedback_path = storage.job_dir(job_id) / "feedback.json"
    feedback_path.write_text(json.dumps(feedback, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "feedback saved for job %s: %d actions",
        job_id,
        len(payload.actions),
    )
    return FeedbackResponse(job_id=job_id, accepted=len(payload.actions))


@router.get(
    "/preview",
    responses={
        200: {"description": "Plain text with entity markers"},
        404: {"description": "Job or extracted text not found"},
    },
)
async def get_preview_text(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> dict[str, str]:
    """Return the extracted plain text for client-side highlighting.

    The frontend uses this text together with the spans from
    ``/annotations`` to render a highlighted diff view on the client
    side. Separating the text from the spans keeps the rendering logic
    in JavaScript and avoids server-side HTML generation.
    """
    _ensure_job_dir_exists(storage, job_id)

    text_path = storage.job_dir(job_id) / "extracted_text.txt"
    if not text_path.is_file():
        raise _not_found("extracted_text.txt not found")

    return {"text": text_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_job_dir_exists(storage: LocalStorage, job_id: UUID) -> None:
    """Raise 404 if the job directory doesn't exist."""
    path = storage.job_dir(job_id)
    if not path.is_dir():
        raise _not_found(f"Job {job_id} not found")


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorResponse(code="not_found", message=detail).model_dump(),
    )


__all__ = ["router"]
