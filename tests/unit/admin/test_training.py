"""Unit tests for the training manager."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest
from neironir.admin import training
from neironir.admin.training import (
    TrainingCommandSpec,
    TrainingState,
    TrainingStatus,
    build_training_dataset,
    get_training_state,
    reset_training_state,
    start_training,
    stop_training,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Ensure the singleton state is clean between tests."""
    reset_training_state()


@pytest.fixture
def feedback_dir(tmp_path: Path) -> Path:
    """Storage root with two jobs, both having ADD feedback."""
    storage = tmp_path / "storage"
    storage.mkdir()
    jobs = storage / "jobs"
    jobs.mkdir()

    for i, (text, action_text, etype) in enumerate(
        [
            ("Звоните +7 (495) 123-45-67", "+7 (495) 123-45-67", "private_phone"),
            ("Email: a@b.com, телефон +7 999 000 00 00", "+7 999 000 00 00", "private_phone"),
        ]
    ):
        job_dir = jobs / f"job-{i}"
        job_dir.mkdir()
        (job_dir / "extracted_text.txt").write_text(text, encoding="utf-8")
        (job_dir / "feedback.json").write_text(
            json.dumps(
                {
                    "job_id": f"job-{i}",
                    "actions": [
                        {
                            "action": "add",
                            "start": text.find(action_text),
                            "end": text.find(action_text) + len(action_text),
                            "entity_type": etype,
                            "text": action_text,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    return storage


class TestBuildTrainingDataset:
    def test_writes_jsonl_with_records(self, feedback_dir: Path) -> None:
        out = feedback_dir / "out"
        out.mkdir()
        summary = build_training_dataset(feedback_dir, out)

        assert summary.record_count == 2
        assert summary.path.is_file()

        lines = summary.path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        rec = json.loads(lines[0])
        assert "text" in rec
        assert "spans" in rec
        assert rec["spans"][0]["label"]

    def test_groups_by_entity_type(self, feedback_dir: Path) -> None:
        out = feedback_dir / "out"
        out.mkdir()
        summary = build_training_dataset(feedback_dir, out)
        assert summary.by_entity_type == {"private_phone": 2}

    def test_raises_when_no_feedback(self, tmp_path: Path) -> None:
        storage = tmp_path / "empty"
        storage.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(FileNotFoundError):
            build_training_dataset(storage, out)

    def test_skips_jobs_without_text_or_feedback(self, tmp_path: Path) -> None:
        storage = tmp_path / "s"
        storage.mkdir()
        (storage / "jobs").mkdir()
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(FileNotFoundError):
            build_training_dataset(storage, out)

    def test_skips_malformed_action_offsets(self, tmp_path: Path) -> None:
        storage = tmp_path / "s"
        storage.mkdir()
        jobs = storage / "jobs"
        jobs.mkdir()
        job_dir = jobs / "broken"
        job_dir.mkdir()
        (job_dir / "extracted_text.txt").write_text("hello world", encoding="utf-8")
        (job_dir / "feedback.json").write_text(
            json.dumps(
                {
                    "actions": [
                        # end beyond text length → skipped
                        {
                            "action": "add",
                            "start": 0,
                            "end": 999,
                            "entity_type": "x",
                            "text": "x",
                        },
                        # start negative → skipped
                        {
                            "action": "add",
                            "start": -1,
                            "end": 4,
                            "entity_type": "x",
                            "text": "hell",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(FileNotFoundError):
            build_training_dataset(storage, out)


class TestStartTraining:
    @pytest.mark.asyncio
    async def test_raises_when_already_running(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=1,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        await start_training(spec)
        try:
            with pytest.raises(RuntimeError):
                await start_training(spec)
        finally:
            state = get_training_state()
            if state.pid:
                with contextlib.suppress(ProcessLookupError):
                    import os
                    import signal

                    os.kill(state.pid, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_updates_state_to_running(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=3,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        state = await start_training(spec)
        try:
            assert state.status == TrainingStatus.RUNNING
            assert state.progress.total_epochs == 3
            assert state.started_at is not None
            assert state.pid is not None
        finally:
            with contextlib.suppress(ProcessLookupError):
                import os
                import signal

                os.kill(state.pid, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_rejects_missing_paths(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(epochs=1)
        with pytest.raises(ValueError):
            await start_training(spec)

    @pytest.mark.asyncio
    async def test_completed_when_process_exits_zero(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[sys.executable, "-c", "print('epoch=1 loss=0.5 eta=00:00:10')"],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=1,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        await start_training(spec)
        # Wait for the monitor task to finish processing the output
        for _ in range(40):
            await asyncio.sleep(0.1)
            if get_training_state().status != TrainingStatus.RUNNING:
                break

        state = get_training_state()
        assert state.status == TrainingStatus.COMPLETED
        assert state.finished_at is not None
        assert state.progress.epoch == 1
        assert state.progress.loss == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_failed_when_process_exits_nonzero(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=1,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        await start_training(spec)
        for _ in range(40):
            await asyncio.sleep(0.1)
            if get_training_state().status != TrainingStatus.RUNNING:
                break

        state = get_training_state()
        assert state.status == TrainingStatus.FAILED
        assert state.error is not None
        assert "boom" in state.error


class TestStopTraining:
    @pytest.mark.asyncio
    async def test_returns_false_when_idle(self) -> None:
        assert await stop_training() is False

    @pytest.mark.asyncio
    async def test_sends_signal_to_running_proc(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[sys.executable, "-c", "import time; time.sleep(60)"],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=1,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        await start_training(spec)
        sent = await stop_training()
        assert sent is True

        for _ in range(40):
            await asyncio.sleep(0.1)
            if get_training_state().status != TrainingStatus.RUNNING:
                break
        state = get_training_state()
        assert state.status in {
            TrainingStatus.CANCELLED,
            TrainingStatus.FAILED,
            TrainingStatus.COMPLETED,
        }


class TestProgressParsing:
    @pytest.mark.asyncio
    async def test_parses_eta_from_output(self, tmp_path: Path) -> None:
        spec = TrainingCommandSpec(
            opf_cmd=[
                sys.executable,
                "-c",
                "print('epoch=2 loss=0.12 eta=00:30:00')",
            ],
            dataset_path=tmp_path / "d.jsonl",
            output_dir=tmp_path / "out",
            epochs=3,
        )
        (tmp_path / "d.jsonl").write_text("{}", encoding="utf-8")
        (tmp_path / "out").mkdir()

        await start_training(spec)
        for _ in range(40):
            await asyncio.sleep(0.1)
            if get_training_state().status != TrainingStatus.RUNNING:
                break
        state = get_training_state()
        assert state.progress.epoch == 2
        assert state.progress.loss == pytest.approx(0.12)
        assert state.progress.eta_seconds == 30 * 60


class TestStateSerialization:
    def test_to_dict_round_trips(self) -> None:
        from datetime import UTC, datetime

        state = TrainingState(
            status=TrainingStatus.RUNNING,
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
            pid=12345,
            dataset_path="/tmp/d.jsonl",
            checkpoint_path="/tmp/out",
        )
        d = state.to_dict()
        assert d["status"] == "running"
        assert d["pid"] == 12345
        assert d["dataset_path"] == "/tmp/d.jsonl"
        assert d["started_at"].startswith("2025-01-01")


class TestStartTrainingFromFeedback:
    @pytest.mark.asyncio
    async def test_marks_failed_when_no_feedback(
        self, tmp_path: Path
    ) -> None:
        storage = tmp_path / "empty"
        storage.mkdir()
        out = tmp_path / "out"
        out.mkdir()

        state = await training.start_training_from_feedback(
            storage_dir=storage,
            output_dir=out,
            opf_cmd=[sys.executable],
            epochs=1,
        )
        assert state.status == TrainingStatus.FAILED
        assert state.error is not None

    @pytest.mark.asyncio
    async def test_starts_with_real_dataset(
        self, feedback_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        out.mkdir()
        state = await training.start_training_from_feedback(
            storage_dir=feedback_dir,
            output_dir=out,
            opf_cmd=[sys.executable, "-c", "print('epoch=1 loss=0.1')"],
            epochs=2,
        )
        try:
            assert state.status in {TrainingStatus.RUNNING, TrainingStatus.COMPLETED}
            assert state.dataset_path is not None
            assert Path(state.dataset_path).is_file()
        finally:
            if state.pid:
                with contextlib.suppress(ProcessLookupError):
                    import os
                    import signal

                    os.kill(state.pid, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_merges_cumulative_and_fresh_datasets(
        self, feedback_dir: Path, tmp_path: Path
    ) -> None:
        """``start_training_from_feedback`` must read records from
        **both** the cumulative ``training_dataset.jsonl`` (written by
        ``apply-feedback``) **and** the fresh ``feedback.json`` files
        on disk, and deduplicate them by ``(text, start, end, label)``."""
        # Pre-seed a cumulative dataset with two records.
        checkpoints = feedback_dir / "checkpoints"
        checkpoints.mkdir(parents=True)
        cumulative = checkpoints / "training_dataset.jsonl"
        cumulative.write_text(
            json.dumps({
                "text": "Email: a@b.com",
                "spans": [{"start": 7, "end": 14, "label": "private_email"}],
            }) + "\n"
            + json.dumps({
                "text": "Email: a@b.com",
                "spans": [{"start": 7, "end": 14, "label": "private_email"}],
            }) + "\n",
            encoding="utf-8",
        )

        out = tmp_path / "out"
        out.mkdir()

        state = await training.start_training_from_feedback(
            storage_dir=feedback_dir,
            output_dir=out,
            opf_cmd=[sys.executable, "-c", "print('epoch=1 loss=0.1')"],
            epochs=1,
        )

        try:
            assert state.status in {TrainingStatus.RUNNING, TrainingStatus.COMPLETED}, state.error
            combined_path = Path(state.dataset_path)
            assert combined_path.is_file()

            lines = combined_path.read_text(encoding="utf-8").strip().splitlines()
            # The cumulative file had 2 records but they are the same
            # (text, start, end, label) — deduplication should collapse
            # them to 1.  Fresh feedback adds 2 more distinct records.
            # Total: 1 + 2 = 3.
            assert len(lines) == 3, f"expected 3 deduplicated records, got {len(lines)}"
        finally:
            if state.pid:
                with contextlib.suppress(ProcessLookupError):
                    import os
                    import signal

                    os.kill(state.pid, signal.SIGTERM)