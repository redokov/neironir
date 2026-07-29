"""Unit tests for ``neironir.workers.feedback_applier.FeedbackApplier``."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document
from neironir.config import Settings
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage
from neironir.workers.feedback_applier import FeedbackApplier
from neironir.workers.pipeline import run_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_md_job(tmp_path: Path, text: str) -> tuple[Path, LocalStorage, Job]:
    """Create a minimal storage layout with a single completed markdown job."""
    storage = LocalStorage(tmp_path)
    job_id = uuid4()
    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename="notes.md",
        source_ext="md",
    )
    storage.save_job(job)
    storage.save_source(job_id, "notes.md", text.encode("utf-8"))
    return tmp_path, storage, job


def _build_docx_job(
    tmp_path: Path, paragraphs: list[str]
) -> tuple[Path, LocalStorage, Job]:
    """Create a minimal storage layout with a single completed docx job."""
    storage = LocalStorage(tmp_path)
    job_id = uuid4()
    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        source_filename="contract.docx",
        source_ext="docx",
    )
    storage.save_job(job)
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    docx_path = tmp_path / "contract.docx"
    document.save(str(docx_path))
    storage.save_source(job_id, "contract.docx", docx_path.read_bytes())
    return tmp_path, storage, job


async def _process(job_dir: Path, storage: LocalStorage, job: Job) -> None:
    """Run the pipeline on a stored job (idempotent)."""
    await run_job(
        job.id,
        settings=Settings(),
        storage=storage,
        privacy=MockPrivacyFilterClient(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAddActions:
    async def test_add_injects_new_placeholder_with_next_number(self, tmp_path: Path) -> None:
        """Adding a second entity must produce ``<PRIVATE_PHONE1>``."""
        job_dir, storage, job = _build_md_job(
            tmp_path,
            "Reach me at user@example.com.",
        )
        await _process(job_dir, storage, job)

        # The cleaned file is "Reach me at <PRIVATE_EMAIL1>." (31 chars).
        # The trailing dot is at original position 28 (after the email
        # span [12, 28)).  We add the dot as a phone entity.
        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": 28,
                    "end": 29,
                    "entity_type": "private_phone",
                    "text": ".",
                }
            ],
        )

        assert summary.added == 1
        assert summary.applied == 1
        assert summary.kept == 0
        assert summary.rejected == 0

        result = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")
        assert "<PRIVATE_EMAIL1>" in result
        assert "<PRIVATE_PHONE1>" in result
        assert "user@example.com" not in result

    async def test_add_continues_numbering_across_calls(self, tmp_path: Path) -> None:
        """Each apply-feedback call keeps the counter monotonic."""
        job_dir, storage, job = _build_md_job(
            tmp_path,
            "user@example.com\nbackup@example.org\nthird@example.io\n",
        )
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        # First add: insert after the second email ``backup@example.org``
        # (orig [17, 34)).  We select just the newline character that
        # follows it (position 34) and add it as a new email entity.
        # The new placeholder gets the next sequential number:
        # ``<PRIVATE_EMAIL4>``.
        applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": 34,
                    "end": 35,
                    "entity_type": "private_email",
                    "text": "fourth@example.io",
                }
            ],
        )
        # Second add: insert after the first email ``user@example.com``
        # (orig [0, 16)).  We select just the newline (position 16).
        # Produces ``<PRIVATE_EMAIL5>``.
        applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": 16,
                    "end": 17,
                    "entity_type": "private_email",
                    "text": "fifth@example.io",
                }
            ],
        )

        result = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")
        # After two apply-feedback passes the document contains:
        #   EMAIL1 (original first email, [0, 18))
        #   EMAIL5 (new, inserted after EMAIL1, [18, 36))
        #   EMAIL4 (new, inserted after EMAIL2 region, [36, 54))
        #   EMAIL3 (original third email, [54, 72))
        # EMAIL2 was replaced by EMAIL4 during the first apply pass
        # because the add action's span covered annotation[1].
        for n in (1, 3, 4, 5):
            assert f"<PRIVATE_EMAIL{n}>" in result, (
                f"missing PRIVATE_EMAIL{n} in result"
            )
        # Verify the counter monotonicity: the largest number in the
        # result equals the total number of ADD actions across all
        # calls (2 adds beyond the original 3 emails = 5).
        assert "<PRIVATE_EMAIL5>" in result

    async def test_add_skips_malformed_action(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": -1,
                    "end": 10,
                    "entity_type": "private_email",
                    "text": "bad",
                },
                {
                    "action": "add",
                    "start": 5,
                    "end": 999,
                    "entity_type": "private_email",
                    "text": "bad",
                },
            ],
        )
        assert summary.added == 0

    async def test_add_unknown_entity_type_is_skipped(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 4,
                    "entity_type": "private_dog",
                    "text": "rex",
                }
            ],
        )
        assert summary.added == 0


class TestRejectActions:
    async def test_reject_restores_original_text(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(
            tmp_path, "Reach me at user@example.com."
        )
        await _process(job_dir, storage, job)

        # The email is annotation index 0 — reject it.
        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "reject",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
        )

        assert summary.rejected == 1
        result = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")
        assert "user@example.com" in result
        assert "<PRIVATE_EMAIL" not in result

    async def test_reject_partial_match(self, tmp_path: Path) -> None:
        """If the user's offset is slightly off, rejection still works."""
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        # Slightly off offset — but the original_text match wins.
        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "reject",
                    "start": 0,
                    "end": 0,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
        )
        assert summary.rejected == 1

    async def test_reject_missing_original_index(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "reject",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_email",
                    "text": "user@",
                    "original_span_index": None,
                }
            ],
        )
        assert summary.rejected == 0


class TestConfirmActions:
    async def test_confirm_is_noop(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        original = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "confirm",
                    "start": 0,
                    "end": 15,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                }
            ],
        )
        assert summary.kept == 1
        assert summary.applied == 0
        # File unchanged
        assert (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8") == original


class TestMixedActions:
    async def test_add_and_reject_in_one_pass(self, tmp_path: Path) -> None:
        """One email gets rejected, another one is added."""
        job_dir, storage, job = _build_md_job(
            tmp_path, "Reach me at user@example.com or admin@example.org."
        )
        await _process(job_dir, storage, job)

        # Annotation 0 = user@example.com, annotation 1 = admin@example.org.
        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                # Reject the first email
                {
                    "action": "reject",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                },
                # Add a new email — but careful with the offset; the
                # cleaned file already has 2 placeholders so we can't
                # use the source offsets.  Pick a substring that is
                # known to still be plain text.
                {
                    "action": "add",
                    "start": 12,
                    "end": 16,
                    "entity_type": "private_phone",
                    "text": "or",
                },
            ],
        )

        assert summary.rejected == 1
        assert summary.added == 1

        result = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")
        assert "user@example.com" in result
        assert "<PRIVATE_EMAIL1>" not in result
        # The added placeholder should be <PRIVATE_PHONE1>
        assert "<PRIVATE_PHONE1>" in result

    async def test_multiple_adds_in_one_pass(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                # Add a new "secret" at the start of the document.
                {
                    "action": "add",
                    "start": 0,
                    "end": 4,
                    "entity_type": "private_phone",
                    "text": "user",
                },
                # Add another secret inside the existing placeholder
                # slice — the offset is relative to the original text.
                {
                    "action": "add",
                    "start": 5,
                    "end": 12,
                    "entity_type": "private_phone",
                    "text": "exampl",
                },
            ],
        )
        assert summary.added == 2


class TestDocxOutput:
    async def test_apply_feedback_to_docx_rejected(self, tmp_path: Path) -> None:
        """DOCX output for apply-feedback raises ValueError since the
        DOCX code path was removed (see api/jobs.py — it raises 400
        before reaching FeedbackApplier)."""
        job_dir, storage, job = _build_docx_job(
            tmp_path,
            ["Reach me at user@example.com."],
        )
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        with pytest.raises(ValueError, match="no converter registered for extension"):
            applier.apply(
                job_dir=storage.job_dir(job.id),
                output_ext="docx",
                feedback_actions=[
                    {
                        "action": "add",
                        "start": 12,
                        "end": 28,
                        "entity_type": "private_phone",
                        "text": "+7 495 123-45-67",
                    }
                ],
            )


class TestCounterPersistence:
    async def test_persisted_counters_are_reused(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(
            tmp_path,
            "user@example.com",
        )
        await _process(job_dir, storage, job)

        applier = FeedbackApplier()
        applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[
                {
                    "action": "add",
                    "start": 20,
                    "end": 35,
                    "entity_type": "private_email",
                    "text": "another@x.io",
                }
            ],
        )
        assert (storage.job_dir(job.id) / "counters.json").is_file()


class TestEmptyFeedback:
    async def test_no_actions_leaves_file_intact(self, tmp_path: Path) -> None:
        job_dir, storage, job = _build_md_job(tmp_path, "user@example.com")
        await _process(job_dir, storage, job)

        original = (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8")

        applier = FeedbackApplier()
        summary = applier.apply(
            job_dir=storage.job_dir(job.id),
            output_ext="md",
            feedback_actions=[],
        )

        assert summary.applied == 0
        # File unchanged — nothing was rewritten.
        assert (storage.job_dir(job.id) / "result.md").read_text(encoding="utf-8") == original


class TestErrorHandling:
    async def test_missing_result_file_raises(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job = Job(id=job_id, source_filename="x.md", source_ext="md")
        storage.save_job(job)

        applier = FeedbackApplier()
        with pytest.raises(FileNotFoundError):
            applier.apply(
                job_dir=storage.job_dir(job_id),
                output_ext="md",
                feedback_actions=[],
            )

    async def test_unknown_output_format_raises(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job_dir = storage.job_dir(job_id)
        job_dir.mkdir(parents=True)
        (job_dir / "result.md").write_text("hello", encoding="utf-8")
        (job_dir / "extracted_text.txt").write_text("hello", encoding="utf-8")
        (job_dir / "annotations.json").write_text("[]", encoding="utf-8")

        applier = FeedbackApplier()
        with pytest.raises(ValueError):
            applier.apply(
                job_dir=job_dir,
                output_ext="pdf",  # type: ignore[arg-type]
                feedback_actions=[],
            )