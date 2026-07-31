"""Unit tests for the incremental training-dataset writer.

The admin ``build_training_dataset`` function rewrites the whole
``feedback_dataset.jsonl`` from scratch, which is wasteful when the
user just clicked "Сохранить правки в файл" and only a single job's
ADD actions should land in the dataset.  These tests cover the
smaller, incremental counterpart used by the apply-feedback API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from neironir.admin.training import append_job_feedback_to_dataset


def _write_feedback(
    job_dir: Path,
    *,
    text: str,
    actions: list[dict],
    with_text_file: bool = True,
) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    if with_text_file:
        (job_dir / "extracted_text.txt").write_text(text, encoding="utf-8")
    (job_dir / "feedback.json").write_text(
        json.dumps(
            {"job_id": "test", "actions": actions, "comment": None},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class TestAppendJobFeedbackToDataset:
    def test_writes_one_record_per_add_action(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-1"
        _write_feedback(
            job_dir,
            text="Reach me at user@example.com and admin@example.org.",
            actions=[
                {
                    "action": "add",
                    "start": 28,
                    "end": 46,
                    "entity_type": "private_phone",
                    "text": "admin@example.org",
                }
            ],
        )

        dataset = tmp_path / "out" / "feedback_dataset.jsonl"
        added = append_job_feedback_to_dataset(job_dir, dataset)

        assert added == 1
        assert dataset.is_file()

        lines = dataset.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        # Original text is preserved verbatim.
        assert record["text"] == "Reach me at user@example.com and admin@example.org."
        # Span offsets are relative to the original text.
        assert record["spans"] == [{"start": 28, "end": 46, "label": "private_phone"}]

    def test_skips_non_add_actions(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-2"
        _write_feedback(
            job_dir,
            text="Plain text with email@example.com inside.",
            actions=[
                {
                    "action": "confirm",
                    "start": 17,
                    "end": 34,
                    "entity_type": "private_email",
                    "text": "email@example.com",
                    "original_span_index": 0,
                },
                {
                    "action": "reject",
                    "start": 17,
                    "end": 34,
                    "entity_type": "private_email",
                    "text": "email@example.com",
                    "original_span_index": 0,
                },
            ],
        )

        dataset = tmp_path / "dataset.jsonl"
        added = append_job_feedback_to_dataset(job_dir, dataset)

        # Neither confirm nor reject adds new signal — the file
        # must not be created.
        assert added == 0
        assert not dataset.exists()

    def test_mixed_actions_only_count_adds(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-3"
        _write_feedback(
            job_dir,
            text="user@example.com and +7 999 000 00 00",
            actions=[
                {
                    "action": "confirm",
                    "start": 0,
                    "end": 16,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                    "original_span_index": 0,
                },
                {
                    "action": "add",
                    "start": 21,
                    "end": 37,
                    "entity_type": "private_phone",
                    "text": "+7 999 000 00 00",
                },
            ],
        )

        dataset = tmp_path / "dataset.jsonl"
        added = append_job_feedback_to_dataset(job_dir, dataset)

        assert added == 1
        records = [
            json.loads(line) for line in dataset.read_text(encoding="utf-8").strip().splitlines()
        ]
        assert len(records) == 1
        assert records[0]["spans"][0]["label"] == "private_phone"

    def test_appends_to_existing_dataset(self, tmp_path: Path) -> None:
        """A second call should append, not overwrite."""
        dataset = tmp_path / "dataset.jsonl"
        dataset.write_text(
            '{"text": "earlier", "spans": [{"start": 0, "end": 1, "label": "x"}]}\n',
            encoding="utf-8",
        )

        job_dir = tmp_path / "jobs" / "job-4"
        _write_feedback(
            job_dir,
            text="new content",
            actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 3,
                    "entity_type": "private_phone",
                    "text": "new",
                }
            ],
        )

        added = append_job_feedback_to_dataset(job_dir, dataset)
        assert added == 1

        lines = dataset.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        # Original first line is preserved.
        assert '"earlier"' in lines[0]
        # New line is the second record.
        record = json.loads(lines[1])
        assert record["text"] == "new content"

    def test_creates_parent_directory_if_missing(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-5"
        _write_feedback(
            job_dir,
            text="hello@example.com",
            actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 17,
                    "entity_type": "private_email",
                    "text": "hello@example.com",
                }
            ],
        )

        # Dataset path lives under a directory that does not yet
        # exist; the writer must create it.
        dataset = tmp_path / "deeply" / "nested" / "out.jsonl"
        assert not dataset.parent.exists()

        added = append_job_feedback_to_dataset(job_dir, dataset)
        assert added == 1
        assert dataset.is_file()

    def test_no_text_file_returns_zero(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-6"
        # ``extracted_text.txt`` is missing on purpose — the writer
        # must treat that as "nothing to do" rather than crashing.
        _write_feedback(
            job_dir,
            text="ignored",
            actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_phone",
                    "text": "foo",
                }
            ],
            with_text_file=False,
        )

        dataset = tmp_path / "dataset.jsonl"
        assert append_job_feedback_to_dataset(job_dir, dataset) == 0
        assert not dataset.exists()

    def test_skips_out_of_bounds_actions(self, tmp_path: Path) -> None:
        """``add`` actions with invalid offsets must not poison the file."""
        job_dir = tmp_path / "jobs" / "job-7"
        _write_feedback(
            job_dir,
            text="short",
            actions=[
                # end > len(text) — skip
                {
                    "action": "add",
                    "start": 0,
                    "end": 999,
                    "entity_type": "private_phone",
                    "text": "x",
                },
                # start < 0 — skip
                {
                    "action": "add",
                    "start": -1,
                    "end": 3,
                    "entity_type": "private_phone",
                    "text": "x",
                },
            ],
        )

        dataset = tmp_path / "dataset.jsonl"
        assert append_job_feedback_to_dataset(job_dir, dataset) == 0

    def test_unknown_entity_type_is_skipped_silently(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "jobs" / "job-8"
        _write_feedback(
            job_dir,
            text="hello world",
            actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_dog",
                    "text": "hello",
                }
            ],
        )

        dataset = tmp_path / "dataset.jsonl"
        # The record still lands in the dataset — the unknown entity
        # type will trip up OPF at training time but that's a
        # training-pipeline concern, not the writer's.
        added = append_job_feedback_to_dataset(job_dir, dataset)
        assert added == 1

    def test_handles_unreadable_feedback_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        job_dir = tmp_path / "jobs" / "job-9"
        job_dir.mkdir(parents=True)
        (job_dir / "extracted_text.txt").write_text("anything", encoding="utf-8")
        (job_dir / "feedback.json").write_text("{not json", encoding="utf-8")

        dataset = tmp_path / "dataset.jsonl"
        added = append_job_feedback_to_dataset(job_dir, dataset)
        assert added == 0
        assert not dataset.exists()
