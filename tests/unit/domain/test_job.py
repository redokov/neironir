"""Tests for ``neironir.domain.job``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from neironir.domain.job import Job, JobStatus


def _sample_job() -> Job:
    return Job(
        source_filename="Договор.docx",
        source_ext="docx",
    )


def test_to_dict_then_from_dict_round_trip() -> None:
    original = _sample_job()
    restored = Job.from_dict(original.to_dict())

    assert restored.id == original.id
    assert restored.status == original.status
    assert restored.source_filename == original.source_filename
    assert restored.source_ext == original.source_ext
    assert restored.created_at == original.created_at
    assert restored.finished_at == original.finished_at
    assert restored.error == original.error


def test_to_dict_serialises_datetime_as_iso_8601() -> None:
    job = Job(
        source_filename="notes.md",
        source_ext="md",
        created_at=datetime(2025, 7, 27, 10, 15, 23, 123000, tzinfo=UTC),
        finished_at=datetime(2025, 7, 27, 10, 16, 0, tzinfo=UTC),
    )
    payload = job.to_dict()

    # ISO 8601 representation is a string, not a datetime object.
    assert isinstance(payload["created_at"], str)
    assert isinstance(payload["finished_at"], str)
    # Pydantic v2 emits either ``+00:00`` or ``Z`` for UTC datetimes in JSON
    # mode; assert the structured prefix and full ISO format rather than
    # pinning the exact UTC suffix.
    assert payload["created_at"].startswith("2025-07-27T10:15:23.123000")
    assert payload["created_at"].endswith(("+00:00", "Z"))
    assert payload["finished_at"].startswith("2025-07-27T10:16:00")
    assert payload["finished_at"].endswith(("+00:00", "Z"))
    # Round-trip through the public API must preserve the wall-clock value.
    assert Job.from_dict(payload).created_at == job.created_at


def test_to_dict_then_json_round_trip() -> None:
    job = Job(
        source_filename="file.docx",
        source_ext="docx",
        status=JobStatus.COMPLETED,
    )
    encoded = json.dumps(job.to_dict())
    decoded = json.loads(encoded)
    restored = Job.from_dict(decoded)

    assert restored.status == JobStatus.COMPLETED
    assert restored.source_filename == "file.docx"
    assert isinstance(restored.id, UUID)


def test_from_dict_handles_missing_finished_at_and_error() -> None:
    job = _sample_job()
    data = job.to_dict()
    data["finished_at"] = None
    data["error"] = None
    restored = Job.from_dict(data)
    assert restored.finished_at is None
    assert restored.error is None


def test_status_is_string_enum() -> None:
    job = _sample_job()
    assert job.status == JobStatus.PENDING
    assert job.status.value == "pending"
    assert isinstance(job.status, str)


@pytest.mark.parametrize("value", ["md", "docx"])
def test_source_ext_accepts_documented_values(value: str) -> None:
    job = Job(source_filename="x", source_ext=value)
    assert job.source_ext == value


def test_source_ext_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        Job(source_filename="x", source_ext="pdf")  # type: ignore[arg-type]
