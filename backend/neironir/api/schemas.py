"""Pydantic schemas describing the HTTP API contract.

These mirror the domain types in :mod:`neironir.domain` for serialisation
purposes. In phase 3 the FastAPI layer will use ``JobResponse.model_validate(
job)`` to convert a domain ``Job`` directly into the wire format thanks to
``from_attributes=True`` — no bespoke mapper required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    """JSON representation of a :class:`neironir.domain.job.Job`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: Literal["pending", "processing", "completed", "failed"]
    source_filename: str
    source_ext: Literal["md", "docx"]
    created_at: datetime
    finished_at: datetime | None
    error: str | None


class HealthResponse(BaseModel):
    """Payload returned by ``GET /api/v1/health``."""

    status: Literal["ok"]


class ErrorResponse(BaseModel):
    """Uniform error envelope for non-2xx responses."""

    code: str
    message: str


# -- Annotation / feedback schemas -------------------------------------------


class AnnotationSpan(BaseModel):
    """A single detected entity span exposed to the frontend."""

    index: int
    start: int
    end: int
    entity_type: str
    text: str
    source: str  # "model", "rule", or "user"


class AnnotationsResponse(BaseModel):
    """Full annotation state for a job."""

    job_id: UUID
    text: str
    spans: list[AnnotationSpan]
    has_feedback: bool = False


class FeedbackItemIn(BaseModel):
    """A single user action from the frontend."""

    action: str  # "confirm" | "reject" | "add"
    start: int
    end: int
    entity_type: str
    text: str
    original_span_index: int | None = None


class FeedbackSubmit(BaseModel):
    """Feedback payload from the review UI."""

    actions: list[FeedbackItemIn]
    comment: str | None = None


class FeedbackResponse(BaseModel):
    """Confirmation that feedback was saved."""

    job_id: UUID
    accepted: int


__all__ = [
    "JobResponse", "HealthResponse", "ErrorResponse",
    "AnnotationSpan", "AnnotationsResponse",
    "FeedbackItemIn", "FeedbackSubmit", "FeedbackResponse",
]
