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

import base64
import hashlib
import json
import logging
import secrets
from pathlib import Path, PurePath
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
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
    DownloadResultResponse,
    ErrorResponse,
    FeedbackResponse,
    FeedbackSubmit,
    JobResponse,
    JobStatusResponse,
    ModeInfoResponse,
)
from neironir.auth.dependencies import (
    get_session_payload,
    require_documents_auth,
)
from neironir.auth.session import SESSION_PAYLOAD_KEY
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import PrivacyFilterClient
from neironir.storage.local import LocalStorage, atomic_write
from neironir.workers.feedback_applier import (
    AlignmentMismatchError,
    FeedbackApplier,
    OverlappingActionsError,
)
from neironir.workers.pipeline import run_job

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    dependencies=[Depends(require_documents_auth)],
)


# A separate router for endpoints that live under ``/api/v1`` but
# outside the ``/documents`` namespace — needed because the jobs
# router uses ``/{job_id}`` as a path parameter which would shadow
# any literal ``/mode`` style path.
meta_router = APIRouter(prefix="/api/v1", tags=["meta"])


_ALLOWED_EXTS = (".md", ".docx")


def issue_job_access_token() -> tuple[str, str]:
    """Issue a random opaque job access token (spec 007 TD-003).

    Returns ``(raw_token, digest)`` where ``digest`` is the SHA-256 hex
    digest of the raw token.  The raw token must be returned to the
    client exactly once (in the creation response) and never persisted
    or logged; only the digest is stored in ``job.json``.
    """
    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token, digest


def verify_job_access(
    request: Request,
    job: Job,
    settings: Settings,
) -> None:
    """Enforce per-job access (spec 007 FR-004).

    An explicit admin session bypasses the token check.  Otherwise the
    request must carry an ``X-Job-Token`` header whose SHA-256 digest
    equals ``job.access_token_digest`` (constant-time comparison).
    The token is accepted only from the header (spec 008): a token
    passed in the URL query string never authorizes a request.

    Wrong token, missing token and legacy jobs without a digest all
    raise an indistinguishable 404 — the API never reveals whether the
    UUID exists or whether the token was simply wrong (design §8).
    """
    payload = get_session_payload(request, settings)
    if payload is not None and payload.get(SESSION_PAYLOAD_KEY):
        return  # privileged admin session — explicit bypass
    # Spec 008: the token is accepted only from the ``X-Job-Token``
    # header — never from the URL, so it cannot leak into browser
    # history, access logs or the Referer header.
    provided = request.headers.get("x-job-token")
    if not provided or not job.access_token_digest:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message="Job not found.",
        )
    provided_digest = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(provided_digest, job.access_token_digest):
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message="Job not found.",
        )


def _require_job_access(
    request: Request,
    job_id: UUID,
    storage: LocalStorage,
    settings: Settings,
) -> Job:
    """Load a job and verify access, mapping every denial to 404.

    Access denials (unknown job, wrong/missing token, legacy job for a
    non-admin) all answer the identical 404 ``job_not_found`` body so the
    UUID is not enumerable (spec 007 T3).  A *corrupted* ``job.json`` is a
    different condition — the job exists but its metadata is damaged —
    and answers 409 ``job_corrupted`` (T4 regression contract); the
    response never reveals the stored digest or any token material.
    """
    try:
        job = storage.load_job(job_id)
    except FileNotFoundError as exc:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message="Job not found.",
        ) from exc
    except json.JSONDecodeError as exc:
        # On-disk metadata is damaged: the job exists but cannot be
        # loaded, so no token can ever be checked.  Surface a distinct,
        # stable 409 rather than masquerading as an auth denial.
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="job_corrupted",
            message="Job metadata is corrupted; feedback cannot be accepted.",
        ) from exc
    except (KeyError, ValueError) as exc:
        # Missing keys or invalid field values (e.g. a malformed digest)
        # are the same corruption condition as an undecodable file.
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="job_corrupted",
            message="Job metadata is corrupted; feedback cannot be accepted.",
        ) from exc
    verify_job_access(request, job, settings)
    return job


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
    "private_organization",
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
    # 2. This bounded post-parse read catches chunked-encoding /
    #    forged-Content-Length requests. The read itself is capped at
    #    ``max_file_size + 1`` so the endpoint never pulls more than
    #    one extra byte past the limit from the spool file. This does
    #    NOT limit how much the multipart parser already spooled to
    #    disk while reading the request stream; it only bounds what
    #    the handler reads before rejecting oversized uploads.
    content = await file.read(settings.max_file_size + 1)
    if len(content) > settings.max_file_size:
        raise _http_error(
            413,  # literal: starlette renamed the constant across versions
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

    # Spec 007 TD-003: issue the one-time opaque access token.  Only the
    # digest is persisted; the raw token is returned in this response only.
    raw_token, digest = issue_job_access_token()

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename=filename,
        source_ext=source_ext,
        output_ext=effective_output,
        access_token_digest=digest,
    )
    storage.save_job(job)

    background_tasks.add_task(
        run_job,
        job.id,
        settings=settings,
        storage=storage,
        privacy=privacy,
    )

    response = JobResponse.model_validate(job)
    response.access_token = raw_token
    return response


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse, "description": "Job not found"}},
)
async def get_job(
    job_id: UUID,
    request: Request,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> JobStatusResponse:
    """Return the current state of a single job.

    Requires a valid ``X-Job-Token`` header (or an admin session); any
    denial answers 404 so the UUID is not enumerable.
    """
    job = _require_job_access(request, job_id, storage, settings)
    # The status payload must not serialise the token field at all —
    # ``access_token`` is delivered exactly once, in the 202 creation
    # response (spec 007 TD-003).
    return JobStatusResponse.model_validate(job)


@router.get(
    "/{job_id}/download",
    response_model=None,
    responses={
        200: {
            "description": (
                "Cleaned file as a binary download, or as JSON with base64 "
                "content when the client sends 'Accept: application/json'."
            ),
            "content": {
                "application/octet-stream": {},
                "application/json": {"model": DownloadResultResponse},
            },
        },
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job is not yet completed"},
        413: {"model": ErrorResponse, "description": "JSON representation exceeds size cap"},
    },
)
async def download(
    job_id: UUID,
    request: Request,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> FileResponse | DownloadResultResponse:
    """Return the cleaned file for a completed job.

    Content negotiation (Q3/TD-003): an Accept header containing an
    exact ``application/json`` media-range (``q``-parameters and other
    entries are ignored) yields a :class:`DownloadResultResponse` JSON
    body with base64-encoded content. Any other Accept — including
    ``*/*`` or no header at all — returns the binary file exactly as
    before (FR-005, backward compatibility).
    """
    job = _require_job_access(request, job_id, storage, settings)
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
    # T17 (#17): a COMPLETED job whose result file is gone (manual cleanup,
    # disk failure, interrupted pipeline) must answer 404 — not crash with a
    # FileNotFoundError deep inside FileResponse/read_bytes.
    if not result_path.is_file():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="result_missing",
            message=(
                f"Result file for job {job_id} is missing; re-upload the "
                "document to clean it again."
            ),
        )
    download_name = _download_filename(job.source_filename, output_ext)
    media_type = _media_type_for(output_ext)

    if _wants_json(request.headers.get("accept", "")):
        # T6: bound the JSON (base64) representation **before** reading
        # the file into memory.  The on-disk byte size is a lower bound
        # on the JSON payload (base64 inflates by ~33%), so checking
        # ``st_size`` against the cap keeps both memory and response size
        # bounded.  Binary downloads are unaffected by the cap.
        file_size = result_path.stat().st_size
        if file_size > settings.max_json_download_size:
            raise _http_error(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="result_too_large",
                message=(
                    f"Result is {file_size} bytes, which exceeds the JSON "
                    f"download cap of {settings.max_json_download_size} bytes; "
                    "download the file in binary mode instead."
                ),
            )
        content = result_path.read_bytes()
        return DownloadResultResponse(
            job_id=job_id,
            filename=download_name,
            ext=output_ext,
            media_type=media_type,
            size=len(content),
            content_base64=base64.b64encode(content).decode("ascii"),
        )

    # Content-Disposition is built by hand (instead of FileResponse's
    # ``filename=``) so non-ASCII names also carry a plain ASCII
    # ``filename="..."`` fallback next to the RFC 5987 ``filename*``
    # parameter (RFC 6266 §4.3); some M2M clients only read the plain one.
    response = FileResponse(result_path, media_type=media_type)
    response.headers["content-disposition"] = _content_disposition(download_name)
    return response


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
    request: Request,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> AnnotationsResponse:
    """Return the extracted text and all detected entity spans."""
    job = _require_job_access(request, job_id, storage, settings)

    text_path = storage.job_dir(job.id) / "extracted_text.txt"
    if not text_path.is_file():
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="extracted_text.txt not found for this job",
        )
    text = text_path.read_text(encoding="utf-8")

    annotations_path = storage.job_dir(job.id) / "annotations.json"
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

    feedback_path = storage.job_dir(job.id) / "feedback.json"
    return AnnotationsResponse(
        job_id=job.id,
        text=text,
        spans=spans,
        has_feedback=feedback_path.is_file(),
    )


@router.post(
    "/{job_id}/feedback",
    response_model=FeedbackResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job is not yet completed"},
    },
)
async def submit_feedback(
    job_id: UUID,
    request: Request,
    payload: FeedbackSubmit,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> FeedbackResponse:
    """Accept user corrections for a completed job.

    Access is enforced first: wrong/missing ``X-Job-Token`` (or a legacy
    job without a digest) answers 404 ``job_not_found``, indistinguishable
    from a nonexistent job.
    """
    job = _require_job_access(request, job_id, storage, settings)
    if job.status != JobStatus.COMPLETED:
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="job_not_ready",
            message=(
                f"Job is in status {job.status.value!r}; feedback is "
                "only available for completed jobs."
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
    request: Request,
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
    job = _require_job_access(request, job_id, storage, settings)
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

    # T6: overlapping add/reject ranges are rejected with 400 **before**
    # anything is written; ``feedback.json`` was just saved above, so remove
    # it again when the applier refuses the action set.
    try:
        summary = FeedbackApplier().apply(
            job_dir=storage.job_dir(job_id),
            output_ext=job.effective_output_ext,
            feedback_actions=list(feedback["actions"] or []),  # type: ignore[arg-type]
        )
    except OverlappingActionsError as exc:
        feedback_path.unlink(missing_ok=True)
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            code="overlapping_actions",
            message=str(exc),
        ) from exc
    except AlignmentMismatchError as exc:
        # T7: annotations and placeholders cannot be paired 1:1 — refuse the
        # action set instead of restoring foreign text.
        feedback_path.unlink(missing_ok=True)
        raise _http_error(
            status.HTTP_409_CONFLICT,
            code="alignment_mismatch",
            message=(
                "Detected spans and cleaned placeholders are out of sync; "
                "feedback cannot be applied safely."
            ),
        ) from exc

    # Append the new ADD actions to the cumulative training dataset.
    # Only do it if this call applied something new — otherwise a repeated
    # apply-feedback would duplicate records in the dataset (idempotency).
    dataset_path = _training_dataset_path(settings)
    training_records_added = (
        append_job_feedback_to_dataset(storage.job_dir(job_id), dataset_path)
        if summary.added
        else 0
    )

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
    request: Request,
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Return the extracted plain text for client-side highlighting."""
    job = _require_job_access(request, job_id, storage, settings)

    text_path = storage.job_dir(job.id) / "extracted_text.txt"
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


def _content_disposition(download_name: str) -> str:
    """Build the ``Content-Disposition`` value for ``download_name``.

    ASCII names keep the plain ``attachment; filename="..."`` form.
    Non-ASCII names get a sanitized ASCII fallback in ``filename="..."``
    (non-ASCII characters and quote characters become ``_``) plus the
    URL-encoded original name in ``filename*=utf-8''...`` (RFC 5987),
    as recommended by RFC 6266 §4.3.
    """

    def _ascii_fallback(name: str) -> str:
        return "".join(ch if ch.isascii() and ch not in '\\"' else "_" for ch in name)

    if download_name.isascii():
        return f'attachment; filename="{_ascii_fallback(download_name)}"'
    return (
        f'attachment; filename="{_ascii_fallback(download_name)}"; '
        f"filename*=utf-8''{quote(download_name)}"
    )


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


def _wants_json(accept: str) -> bool:
    """True iff any media-range in the Accept header is exactly 'application/json'.

    Only the media-range part of each comma-separated entry is checked
    (``q``-parameters and other params are ignored) with a lower-cased
    exact match, so ``application/json; q=0.9`` or
    ``application/json, */*`` match, while ``application/jsonx`` or a
    bare ``*/*`` do not (TD-003).
    """
    return any(
        entry.split(";", 1)[0].strip().lower() == "application/json"
        for entry in accept.split(",")
        if entry.strip()
    )


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


__all__ = ["router", "meta_router"]
