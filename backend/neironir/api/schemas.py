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


__all__ = ["JobResponse", "HealthResponse", "ErrorResponse"]
