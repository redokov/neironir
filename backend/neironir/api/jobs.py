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

import logging
from pathlib import PurePath
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.api.schemas import ErrorResponse, JobResponse
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import PrivacyFilterClient
from neironir.storage.local import LocalStorage
from neironir.workers.pipeline import run_job

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


_ALLOWED_EXTS = (".md", ".docx")


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
) -> JobResponse:
    """Accept a markdown or docx file, create a job, and start the pipeline."""
    filename = file.filename or ""
    if not filename.lower().endswith(_ALLOWED_EXTS):
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="unsupported_format",
            message="Only .md and .docx files are supported.",
        )

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
            message=str(exc),
        ) from exc

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename=filename,
        source_ext=_ext_from_filename(filename),
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

    result_path = storage.job_dir(job_id) / f"result.{job.source_ext}"
    download_name = _download_filename(job.source_filename, job.source_ext)
    return FileResponse(
        result_path,
        media_type="application/octet-stream",
        filename=download_name,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


__all__ = ["router"]
