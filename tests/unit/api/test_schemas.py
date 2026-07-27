"""Tests for ``neironir.api.schemas``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from neironir.api.schemas import ErrorResponse, HealthResponse, JobResponse
from neironir.domain.job import Job, JobStatus
from pydantic import ValidationError


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "source_filename": "Договор.docx",
        "source_ext": "docx",
    }
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def test_job_response_accepts_values_from_domain_job() -> None:
    job = _make_job(status=JobStatus.PROCESSING)
    response = JobResponse.model_validate(job)

    assert response.id == job.id
    assert response.status == "processing"
    assert response.source_filename == "Договор.docx"
    assert response.source_ext == "docx"
    assert response.created_at == job.created_at
    assert response.finished_at is None
    assert response.error is None


def test_job_response_from_attributes_flag_is_set() -> None:
    config = JobResponse.model_config
    assert config.get("from_attributes") is True


@pytest.mark.parametrize(
    "status",
    ["pending", "processing", "completed", "failed"],
)
def test_job_response_status_accepts_documented_strings(status: str) -> None:
    response = JobResponse(
        id=uuid4(),
        status=status,
        source_filename="f",
        source_ext="md",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        finished_at=None,
        error=None,
    )
    assert response.status == status


def test_job_response_status_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        JobResponse(
            id=uuid4(),
            status="queued",  # type: ignore[arg-type]
            source_filename="f",
            source_ext="md",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            finished_at=None,
            error=None,
        )


def test_health_response_only_accepts_ok() -> None:
    response = HealthResponse(status="ok")
    assert response.status == "ok"
    with pytest.raises(ValidationError):
        HealthResponse(status="nope")  # type: ignore[arg-type]


def test_error_response_shape() -> None:
    response = ErrorResponse(code="bad_extension", message="Only .md and .docx are supported.")
    assert response.code == "bad_extension"
    assert response.message == "Only .md and .docx are supported."


def test_job_response_serialises_uuid_and_datetime_in_json() -> None:
    fixed_id = UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
    job = _make_job()
    response = JobResponse.model_validate(job)
    response = response.model_copy(update={"id": fixed_id})

    payload = response.model_dump(mode="json")
    assert payload["id"] == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert isinstance(payload["created_at"], str)
