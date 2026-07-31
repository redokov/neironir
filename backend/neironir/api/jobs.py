"""HTTP endpoints for managing redaction jobs.

Three routes are exposed under the ``/api/v1/documents`` prefix:

* ``POST /`` — accept a multipart upload, create a :class:`Job`, and
  schedule the background pipeline.
* ``GET /{job_id}`` — return the current state of a job.
* ``GET /{job_id}/download`` — return the cleaned file once the job
  has finished.

Errors are returned as :class:`ErrorResponse` payloads. The
:func:`_http_error` helper centralises the construction so the response
shape stays consistent across handlers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePath
from typing import Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from neironir.admin.training import append_job_feedback_to_dataset
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.api.schemas import (
    AnnotationSpan,
    AnnotationsResponse,
    ApplyFeedbackResponse,
    ErrorResponse,
    FeedbackResponse,
    FeedbackSubmit,
    JobResponse,
    ModeInfoResponse,
)
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import PrivacyFilterClient
from neironir.storage.local import LocalStorage, atomic_write
from neironir.workers.feedback_applier import FeedbackApplier
from neironir.workers.pipeline import run_job

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


# A separate router for endpoints that live under ``/api/v1`` but
# outside the ``/documents`` namespace — needed because the jobs
# router uses ``/{job_id}`` as a path parameter which would shadow
# any literal ``/mode`` style path.
meta_router = APIRouter(prefix="/api/v1", tags=["meta"])


_ALLOWED_EXTS = (".md", ".docx")


# Entity types the **mock** privacy filter is able to detect.  The
# frontend uses this list to render a small banner that explains
# which PII categories will (and won't) be redacted when running in
# mock mode.  See ``docs/api.md`` for the full mode reference.
_MOCK_DETECTED_TYPES = (
    "private_email",
    "private_phone",
    "private_url",
    "private_date",
    "account_number",
    "secret",
)

_FULL_DETECTED_TYPES = (
    "private_person",
    "private_address",
    "private_email",
    "private_phone",
    "private_url",
    "private_date",
    "account_number",
    "secret",
)


@meta_router.get("/mode", response_model=ModeInfoResponse)
async def get_mode(
    settings: Settings = Depends(get_settings),
) -> ModeInfoResponse:
    """Describe the active privacy filter so the UI can explain its limits.

    The frontend uses ``detected_types`` to render a one-liner that
    tells the user which entity categories will be redacted in this
    mode.  For ``mock`` we explicitly do **not** include names or
    addresses so the user understands why those remain visible in the
    output.
    """
    mode = settings.privacy_filter_mode
    detected = list(_MOCK_DETECTED_TYPES) if mode == "mock" else list(_FULL_DETECTED_TYPES)
    return ModeInfoResponse(
        privacy_filter_mode=mode,
        detected_types=detected,
    )


@router.post(
    "/",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported file extension"},
        413: {"model": ErrorResponse, "description": "Uploaded file exceeds size limit"},
    },
)
async def upload(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    storage: LocalStorage = Depends(get_storage),
    privacy: PrivacyFilterClient = Depends(get_privacy),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    output_format: str | None = Form(default=None),  # validated manually in _validate_output_format
) -> JobResponse:
    """Accept a markdown or docx file, create a job, and start the pipeline.

    Args:
        output_format: Optional override for the cleaned file format.
            When set to ``"md"`` and the source is ``.docx``, the
            pipeline converts the document to plain text, redacts it
            and writes the result as ``.md``.  Other combinations are
            rejected with HTTP 400.
    """
    filename = file.filename or ""
    if not filename.lower().endswith(_ALLOWED_EXTS):
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="unsupported_format",
            message="Only .md and .docx files are supported.",
        )

    # Two-layer size protection:
    # 1. ``MaxBodySizeMiddleware`` rejects requests with
    #    Content-Length > max_file_size BEFORE the body is
    #    parsed (catches the common case).
    # 2. This post-parse check (``len(content)``) catches
    #    chunked-encoding / forged-Content-Length requests.
    content = await file.read()
    if len(content) > settings.max_file_size:
        raise _http_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            code="file_too_large",
            message=(f"Uploaded file exceeds the maximum size of {settings.max_file_size} bytes."),
        )

    job_id = uuid4()
    try:
        storage.save_source(job_id, filename, content)
    except ValueError as exc:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="unsupported_format",
            message="Unsupported file format. Only .md and .docx files are accepted.",
        ) from exc

    source_ext = _ext_from_filename(filename)
    effective_output = _validate_output_format(source_ext, output_format)

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename=filename,
        source_ext=source_ext,
        output_ext=effective_output,
    )
    storage.save_job(job)

    background_tasks.add_task(
        run_job,
        job.id,
        settings=settings,
        storage=storage,
        privacy=privacy,
    )

    return JobResponse.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
async def get_job(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> JobResponse:
    """Return the current state of a single job."""
    job = _load_job_or_404(storage, job_id)
    return JobResponse.model_validate(job)


@router.get(
    "/{job_id}/download",
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job is not yet completed"},
    },
)
async def download(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> FileResponse:
    """Return the cleaned file for a completed job."""
    job = _load_job_or_404(storage, job_id)
    if job.status != JobStatus.COMPLETED:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="job_not_ready",
            message=(
                f"Job is in status {job.status.value!r}; download is only "
                "available for completed jobs."
            ),
        )

    output_ext = job.effective_output_ext
    result_path = storage.job_dir(job_id) / f"result.{output_ext}"
    download_name = _download_filename(job.source_filename, output_ext)
    media_type = _media_type_for(output_ext)
    return FileResponse(
        result_path,
        media_type=media_type,
        filename=download_name,
    )


# ---------------------------------------------------------------------------
# Annotation / Feedback / Preview endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/annotations",
    response_model=AnnotationsResponse,
    responses={404: {"model": ErrorResponse, "description": "Job or annotations not found"}},
)
async def get_annotations(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> AnnotationsResponse:
    """Return the extracted text and all detected entity spans."""
    _ensure_job_dir_exists(storage, job_id)

    text_path = storage.job_dir(job_id) / "extracted_text.txt"
    if not text_path.is_file():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="extracted_text.txt not found for this job",
        )
    text = text_path.read_text(encoding="utf-8")

    annotations_path = storage.job_dir(job_id) / "annotations.json"
    if not annotations_path.is_file():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="annotations.json not found for this job",
        )

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

    feedback_path = storage.job_dir(job_id) / "feedback.json"
    return AnnotationsResponse(
        job_id=job_id,
        text=text,
        spans=spans,
        has_feedback=feedback_path.is_file(),
    )


@router.post(
    "/{job_id}/feedback",
    response_model=FeedbackResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
async def submit_feedback(
    job_id: UUID,
    payload: FeedbackSubmit,
    storage: LocalStorage = Depends(get_storage),
) -> FeedbackResponse:
    """Accept user corrections for a completed job."""
    _ensure_job_dir_exists(storage, job_id)

    feedback = {
        "job_id": str(job_id),
        "actions": [a.model_dump() for a in payload.actions],
        "comment": payload.comment,
    }
    feedback_path = storage.job_dir(job_id) / "feedback.json"
    atomic_write(
        feedback_path,
        json.dumps(feedback, ensure_ascii=False, indent=2),
    )

    logger.info("feedback saved for job %s: %d actions", job_id, len(payload.actions))
    return FeedbackResponse(job_id=job_id, accepted=len(payload.actions))


@router.post(
    "/{job_id}/apply-feedback",
    response_model=ApplyFeedbackResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job is not yet completed"},
    },
)
async def apply_feedback(
    job_id: UUID,
    payload: FeedbackSubmit,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ApplyFeedbackResponse:
    """Rewrite the cleaned file with the user's corrections applied.

    The endpoint saves ``feedback.json`` (idempotent with
    ``POST /{job_id}/feedback``) and then re-applies the actions on top
    of the previously-cleaned document.  ``add`` actions inject new
    placeholders that **continue** the existing numbering; ``reject``
    actions restore the original text in place of the placeholder;
    ``confirm`` actions are no-ops.

    ADD actions are also appended to the cumulative training dataset
    (``<storage>/checkpoints/training_dataset.jsonl``) so the model
    learns from corrections **immediately**, without waiting for the
    admin "Запустить дообучение" run.  The response surfaces the
    count and the dataset path so the UI can confirm the contribution.

    Returns the counters of how many actions were applied.
    """
    _ensure_job_dir_exists(storage, job_id)
    job = storage.load_job(job_id)
    if job.status != JobStatus.COMPLETED:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="job_not_ready",
            message=(
                f"Job is in status {job.status.value!r}; apply-feedback is "
                "only available for completed jobs."
            ),
        )

    # DOCX output for apply-feedback is not supported.  The user must
    # select ``output_format=md`` when uploading a ``.docx`` file in
    # order to apply corrections.  See ``workers/feedback_applier.py``
    # and variants/B in the code-review findings.
    if job.effective_output_ext == "docx":
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="docx_output_not_supported",
            message=(
                "Apply-feedback is not available for DOCX output. "
                "Re-upload the file with output_format=md to apply corrections."
            ),
        )

    feedback = {
        "job_id": str(job_id),
        "actions": [a.model_dump() for a in payload.actions],
        "comment": payload.comment,
    }
    feedback_path = storage.job_dir(job_id) / "feedback.json"
    atomic_write(
        feedback_path,
        json.dumps(feedback, ensure_ascii=False, indent=2),
    )

    summary = FeedbackApplier().apply(
        job_dir=storage.job_dir(job_id),
        output_ext=job.effective_output_ext,
        feedback_actions=list(feedback["actions"] or []),  # type: ignore[arg-type]
    )

    # Append the new ADD actions to the cumulative training dataset.
    # This lets the model learn from corrections **immediately** —
    # admins don't have to wait for the next training run to harvest
    # the signal.
    dataset_path = _training_dataset_path(settings)
    training_records_added = append_job_feedback_to_dataset(storage.job_dir(job_id), dataset_path)

    logger.info(
        "applied feedback for job %s: %d add, %d reject, %d confirm; %d new training record(s)",
        job_id,
        summary.added,
        summary.rejected,
        summary.kept,
        training_records_added,
    )
    return ApplyFeedbackResponse(
        job_id=job_id,
        applied=summary.applied,
        added=summary.added,
        kept=summary.kept,
        rejected=summary.rejected,
        output_ext=job.effective_output_ext,
        training_records_added=training_records_added,
        training_dataset_path=str(dataset_path),
    )


@router.get(
    "/{job_id}/preview",
    responses={
        200: {"description": "Plain text with entity markers"},
        404: {"description": "Job or extracted text not found"},
    },
)
async def get_preview_text(
    job_id: UUID,
    storage: LocalStorage = Depends(get_storage),
) -> dict[str, str]:
    """Return the extracted plain text for client-side highlighting."""
    _ensure_job_dir_exists(storage, job_id)

    text_path = storage.job_dir(job_id) / "extracted_text.txt"
    if not text_path.is_file():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="extracted_text.txt not found",
        )

    return {"text": text_path.read_text(encoding="utf-8")}


def _http_error(http_status: int, *, code: str, message: str) -> HTTPException:
    """Build an :class:`HTTPException` carrying an :class:`ErrorResponse` body.

    FastAPI wraps ``HTTPException.detail`` in a top-level ``detail`` key
    when it serialises the response. The handler tests are aware of
    this and assert against ``response.json()["detail"]["code"]``.
    """
    return HTTPException(
        status_code=http_status,
        detail=ErrorResponse(code=code, message=message).model_dump(),
    )


def _load_job_or_404(storage: LocalStorage, job_id: UUID) -> Job:
    """Load a job or raise an HTTP 404 with an :class:`ErrorResponse` body."""
    try:
        return storage.load_job(job_id)
    except FileNotFoundError as exc:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message="Job not found.",
        ) from exc


def _ext_from_filename(filename: str) -> Literal["md", "docx"]:
    """Return the lower-case extension without the leading dot, validated."""
    suffix = PurePath(filename).suffix.lower().lstrip(".")
    if suffix not in ("md", "docx"):
        # The check before we got here guarantees this, but defensiveness
        # costs us one line and keeps the type narrowing honest.
        raise ValueError(f"Unsupported extension {suffix!r}")
    return suffix  # type: ignore[return-value]


def _download_filename(source_filename: str, ext: str) -> str:
    """Build the cleaned-file download name ``<stem>.cleaned.<ext>``."""
    stem = PurePath(source_filename).stem or "document"
    return f"{stem}.cleaned.{ext}"


def _media_type_for(ext: str) -> str:
    """Return the HTTP media type to use for a downloaded file extension."""
    if ext == "md":
        return "text/markdown; charset=utf-8"
    if ext == "docx":
        # ``application/vnd.openxmlformats-officedocument.wordprocessingml.document``
        # is the official MIME type for ``.docx``; some browsers prefer
        # the shorter alias so we send both via a single header.
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _validate_output_format(
    source_ext: str,
    output_format: str | None,
) -> Literal["md", "docx"] | None:
    """Return the requested ``output_format`` if it is compatible with ``source_ext``.

    Rules (matches :func:`neironir.workers.pipeline._validate_conversion`):

    * ``None`` — no override, the cleaned file inherits the source ext.
    * ``md`` allowed when source is ``md`` (identity) or ``docx``
      (markdown conversion via pandoc).
    * ``docx`` allowed only when source is ``docx`` (identity).

    ``md`` → ``docx`` would require building a real Word document from
    plain text — out of scope for the MVP.
    """
    if output_format is None:
        return None
    if output_format not in ("md", "docx"):
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="unsupported_output_format",
            message=(f"output_format={output_format!r} is not one of 'md' or 'docx'."),
        )
    if source_ext == output_format:
        return output_format  # type: ignore[return-value]
    if source_ext == "docx" and output_format == "md":
        return "md"
    raise _http_error(
        status.HTTP_400_BAD_REQUEST,
        code="unsupported_output_format",
        message=(
            f"output_format={output_format!r} is not compatible with "
            f"source_ext={source_ext!r}; only md→md, docx→md and docx→docx are supported."
        ),
    )


def _training_dataset_path(settings: Settings) -> Path:
    """Return the path where ``apply_feedback`` accumulates training
    records.

    Mirrors the path used by the admin "Запустить дообучение" button so
    both code paths write to the same JSONL file — the admin button
    sees everything that was streamed in through ``apply_feedback``.
    """
    from neironir.admin.training import CUMULATIVE_DATASET_NAME

    root = Path(settings.storage_dir) / "checkpoints" / CUMULATIVE_DATASET_NAME
    return root


def _ensure_job_dir_exists(storage: LocalStorage, job_id: UUID) -> None:
    """Raise 404 if the job directory doesn't exist."""
    path = storage.job_dir(job_id)
    if not path.is_dir():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="not_found",
            message=f"Job {job_id} not found",
        )


__all__ = ["router", "meta_router"]
