"""Tests for ``neironir.api.schemas``."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from neironir.api.schemas import (
    DownloadResultResponse,
    ErrorResponse,
    FeedbackItemIn,
    HealthResponse,
    JobResponse,
)
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


# ---------------------------------------------------------------------------
# DownloadResultResponse (F15: JSON download negotiation)
# ---------------------------------------------------------------------------


def _download_payload(**overrides: object) -> dict[str, object]:
    """A minimal valid field set for ``DownloadResultResponse``."""
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "filename": "Договор.cleaned.md",
        "ext": "md",
        "media_type": "text/markdown; charset=utf-8",
        "size": 12,
        "content_base64": base64.b64encode(b"cleaned text").decode("ascii"),
    }
    payload.update(overrides)
    return payload


class TestDownloadResultResponse:
    def test_accepts_all_fields(self) -> None:
        fixed_id = uuid4()
        response = DownloadResultResponse(**_download_payload(job_id=fixed_id))

        assert response.job_id == fixed_id
        assert response.filename == "Договор.cleaned.md"
        assert response.ext == "md"
        assert response.media_type == "text/markdown; charset=utf-8"
        assert response.size == 12
        assert response.content_base64 == base64.b64encode(b"cleaned text").decode("ascii")

    @pytest.mark.parametrize("ext", ["md", "docx"])
    def test_ext_accepts_both_documented_formats(self, ext: str) -> None:
        response = DownloadResultResponse(**_download_payload(ext=ext))
        assert response.ext == ext

    def test_ext_rejects_undocumented_value(self) -> None:
        with pytest.raises(ValidationError):
            DownloadResultResponse(**_download_payload(ext="txt"))  # type: ignore[arg-type]

    def test_job_id_must_be_uuid(self) -> None:
        with pytest.raises(ValidationError):
            DownloadResultResponse(**_download_payload(job_id="not-a-uuid"))  # type: ignore[arg-type]

    def test_content_base64_is_ascii_string(self) -> None:
        response = DownloadResultResponse(**_download_payload())
        assert isinstance(response.content_base64, str)
        assert response.content_base64.isascii()

    def test_base64_round_trip(self) -> None:
        original = "Текст с кириллицей и <PRIVATE_PERSON1> плейсхолдером".encode()
        encoded = base64.b64encode(original).decode("ascii")
        response = DownloadResultResponse(
            **_download_payload(
                content_base64=encoded,
                size=len(original),
            )
        )
        assert base64.b64decode(response.content_base64) == original
        assert response.size == len(original)

    def test_model_dump_json_round_trip(self) -> None:
        """The JSON wire format must survive a validate→dump→validate cycle."""
        response = DownloadResultResponse(**_download_payload())
        payload = response.model_dump(mode="json")

        assert set(payload) == {
            "job_id",
            "filename",
            "ext",
            "media_type",
            "size",
            "content_base64",
        }
        # UUID serialises to its canonical string form in JSON mode.
        assert payload["job_id"] == str(response.job_id)

        restored = DownloadResultResponse.model_validate(payload)
        assert restored == response

    def test_missing_field_is_rejected(self) -> None:
        payload = _download_payload()
        del payload["size"]  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            DownloadResultResponse(**payload)  # type: ignore[arg-type]


class TestFeedbackItemInValidation:
    """T8: malformed feedback is rejected on the schema, not in the applier."""

    def test_valid_action_passes(self) -> None:
        item = FeedbackItemIn(
            action="add", start=0, end=5, entity_type="private_email", text="a@b.com"
        )
        assert item.action == "add"

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItemIn(
                action="delete", start=0, end=5, entity_type="private_email", text="x"
            )

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItemIn(
                action="add", start=-1, end=5, entity_type="private_email", text="x"
            )

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItemIn(
                action="add", start=10, end=5, entity_type="private_email", text="x"
            )

    def test_equal_start_end_allowed_for_insert(self) -> None:
        item = FeedbackItemIn(
            action="add", start=7, end=7, entity_type="private_phone", text=""
        )
        assert item.start == item.end == 7

    def test_unknown_entity_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItemIn(
                action="add", start=0, end=5, entity_type="not_a_type", text="x"
            )

    def test_known_entity_type_accepted(self) -> None:
        item = FeedbackItemIn(
            action="reject",
            start=0,
            end=5,
            entity_type="private_person",
            text="x",
            original_span_index=3,
        )
        assert item.original_span_index == 3

    def test_negative_original_span_index_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItemIn(
                action="reject",
                start=0,
                end=5,
                entity_type="private_person",
                text="x",
                original_span_index=-1,
            )

    def test_null_original_span_index_allowed(self) -> None:
        item = FeedbackItemIn(
            action="add",
            start=0,
            end=5,
            entity_type="private_url",
            text="http://e.com",
            original_span_index=None,
        )
        assert item.original_span_index is None
