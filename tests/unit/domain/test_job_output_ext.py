"""Unit tests for the new ``Job.output_ext`` field and its helpers."""

from __future__ import annotations

import json

from neironir.domain.job import Job, JobStatus


def test_default_output_ext_is_none() -> None:
    job = Job(source_filename="notes.md", source_ext="md")
    assert job.output_ext is None


def test_effective_output_ext_falls_back_to_source() -> None:
    job = Job(source_filename="notes.md", source_ext="md")
    assert job.effective_output_ext == "md"


def test_output_ext_explicit_md_with_docx_source() -> None:
    job = Job(
        source_filename="contract.docx",
        source_ext="docx",
        output_ext="md",
    )
    assert job.effective_output_ext == "md"


def test_output_ext_rejects_unknown_value() -> None:
    import pytest

    with pytest.raises(ValueError):
        Job(
            source_filename="x.docx",
            source_ext="docx",
            output_ext="pdf",  # type: ignore[arg-type]
        )


def test_to_dict_includes_output_ext() -> None:
    job = Job(
        source_filename="contract.docx",
        source_ext="docx",
        output_ext="md",
        status=JobStatus.COMPLETED,
    )
    payload = job.to_dict()
    assert payload["output_ext"] == "md"


def test_from_dict_handles_missing_output_ext_for_legacy_jobs() -> None:
    """Pre-existing ``job.json`` files do not have ``output_ext``.

    They were saved before the field was added — :meth:`from_dict` must
    accept them transparently and fall back to ``source_ext``.
    """
    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "status": "completed",
        "source_filename": "legacy.docx",
        "source_ext": "docx",
        "created_at": "2025-01-01T00:00:00",
        "finished_at": None,
        "error": None,
    }
    job = Job.from_dict(payload)
    assert job.output_ext is None
    assert job.effective_output_ext == "docx"


def test_from_dict_round_trip_with_output_ext() -> None:
    original = Job(
        source_filename="contract.docx",
        source_ext="docx",
        output_ext="md",
    )
    encoded = json.dumps(original.to_dict())
    restored = Job.from_dict(json.loads(encoded))
    assert restored.output_ext == "md"
    assert restored.effective_output_ext == "md"


def test_assign_output_ext_after_construction() -> None:
    """``validate_assignment=True`` lets us tweak ``output_ext`` later."""
    job = Job(source_filename="contract.docx", source_ext="docx")
    assert job.effective_output_ext == "docx"
    job.output_ext = "md"
    assert job.effective_output_ext == "md"
