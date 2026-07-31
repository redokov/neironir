"""Training process manager — wraps ``opf train`` as a tracked subprocess.

The privacy-filter CLI exposes a ``train`` subcommand that consumes a
``JSONL`` dataset and writes a fine-tuned checkpoint.  The admin UI
exposes a "Запустить дообучение" button which translates into:

    POST /api/v1/admin/training/start

This module owns:

* Generation of the training dataset from accumulated ``feedback.json``
  (one record per ADD action).
* The :class:`TrainingState` singleton that the admin UI polls via
  ``GET /api/v1/admin/training/status``.
* Parsing ``opf train``'s stdout into a :class:`TrainingProgress`
  dataclass (current epoch, loss, ETA).

The subprocess is launched with ``stdout=PIPE`` and read in a background
task that updates the shared state.  ``stop_training`` sends ``SIGTERM``
and flips the state to ``cancelling``; the background reader transitions
it to ``cancelled`` once ``opf`` exits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


class TrainingStatus(str, Enum):  # noqa: UP042
    """Lifecycle states of the training subprocess.

    Values mirror what the admin UI displays:

    * ``idle`` — no training has ever been run in this process.
    * ``running`` — the subprocess is alive.
    * ``completed`` — ``opf train`` exited 0 successfully.
    * ``failed`` — ``opf train`` exited non-zero.
    * ``cancelled`` — the user pressed "Остановить".
    """

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TrainingProgress:
    """Snapshot of the training loop progress.

    The fields map 1-to-1 to the columns the admin UI renders.
    """

    epoch: int = 0
    total_epochs: int = 0
    loss: float | None = None
    eta_seconds: int | None = None


@dataclass
class TrainingState:
    """Singleton state object owned by :func:`get_training_state`.

    One ``TrainingState`` lives per process.  Mutating it from a
    background task is safe because Python attribute assignment is
    atomic under the GIL.
    """

    status: TrainingStatus = TrainingStatus.IDLE
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pid: int | None = None
    dataset_path: str | None = None
    checkpoint_path: str | None = None
    progress: TrainingProgress = field(default_factory=TrainingProgress)
    error: str | None = None
    log_tail: list[str] = field(default_factory=list)
    # The running subprocess object (not serialised). Used by
    # ``stop_training`` for cross-platform termination.
    _process: asyncio.subprocess.Process | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready snapshot for the HTTP layer."""
        return {
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "pid": self.pid,
            "dataset_path": self.dataset_path,
            "checkpoint_path": self.checkpoint_path,
            "progress": asdict(self.progress),
            "error": self.error,
            "log_tail": list(self.log_tail),
        }


# ---------------------------------------------------------------------------
# Module-level singleton + lock
# ---------------------------------------------------------------------------


_STATE: TrainingState = TrainingState()
_STATE_LOCK = asyncio.Lock()
# Lock protecting ``append_job_feedback_to_dataset`` — the JSONL
# file is opened in append mode and concurrent writes from parallel
# ``apply-feedback`` calls could interleave and corrupt the dataset.
_DATASET_APPEND_LOCK = threading.Lock()


def get_training_state() -> TrainingState:
    """Return the process-wide :class:`TrainingState` instance."""
    return _STATE


def reset_training_state() -> None:
    """Reset the singleton back to ``IDLE``.

    Intended for tests — production code never invokes this.
    """
    global _STATE
    _STATE = TrainingState()


# ---------------------------------------------------------------------------
# Dataset generation
# ---------------------------------------------------------------------------


@dataclass
class TrainingDatasetSummary:
    """Information about the JSONL dataset built from user feedback."""

    path: Path
    record_count: int
    by_entity_type: dict[str, int] = field(default_factory=dict)


def build_training_dataset(storage_dir: Path, output_dir: Path) -> TrainingDatasetSummary:
    """Convert accumulated ``feedback.json`` files into a JSONL training set.

    One JSONL record per ``ADD`` action — the user's correction is the
    positive example.  CONFIRM/REJECT actions are skipped because they
    don't add new signal beyond what the model already learned.

    The schema mirrors the privacy-filter CLI's expected training
    format::

        {"text": "...", "spans": [{"start": int, "end": int, "label": "..."}]}

    Returns:
        A :class:`TrainingDatasetSummary` with the path and counts.

    Raises:
        FileNotFoundError: If no feedback has been collected yet.
    """
    jobs_dir = Path(storage_dir) / "jobs"
    if not jobs_dir.is_dir():
        raise FileNotFoundError("no jobs directory")

    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "feedback_dataset.jsonl"

    record_count = 0
    by_entity_type: dict[str, int] = {}

    with dataset_path.open("w", encoding="utf-8") as out:
        for job_dir in sorted(jobs_dir.iterdir()):
            feedback_path = job_dir / "feedback.json"
            text_path = job_dir / "extracted_text.txt"
            if not (feedback_path.is_file() and text_path.is_file()):
                continue

            try:
                feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
                text = text_path.read_text(encoding="utf-8")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("skipping unreadable feedback %s: %s", job_dir.name, exc)
                continue

            for action in feedback.get("actions", []):
                if action.get("action") != "add":
                    continue
                start = int(action.get("start", -1))
                end = int(action.get("end", -1))
                etype = action.get("entity_type", "unknown")
                if start < 0 or end <= start or end > len(text):
                    continue

                record = {
                    "text": text,
                    "spans": [
                        {"start": start, "end": end, "label": etype},
                    ],
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                record_count += 1
                by_entity_type[etype] = by_entity_type.get(etype, 0) + 1

    if record_count == 0:
        raise FileNotFoundError("no ADD actions found in feedback")

    return TrainingDatasetSummary(
        path=dataset_path,
        record_count=record_count,
        by_entity_type=by_entity_type,
    )


def append_job_feedback_to_dataset(
    job_dir: Path,
    dataset_path: Path,
) -> int:
    """Append the ADD actions from ``job_dir/feedback.json`` to ``dataset_path``.

    Used by the ``POST /api/v1/documents/{id}/apply-feedback`` endpoint
    so that user corrections flow into the training set **immediately**,
    without waiting for the admin to click "Запустить дообучение".

    Args:
        job_dir: Per-job storage directory that contains
            ``feedback.json`` and ``extracted_text.txt``.
        dataset_path: Path to the cumulative ``feedback_dataset.jsonl``
            file.  The function creates the parent directory if needed
            and appends one JSONL line per ADD action.

    Returns:
        Number of records appended.  Zero is a valid result when the
        user only confirmed/rejected detected spans (no new signal for
        the model).
    """
    feedback_path = job_dir / "feedback.json"
    text_path = job_dir / "extracted_text.txt"
    if not (feedback_path.is_file() and text_path.is_file()):
        return 0

    try:
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        text = text_path.read_text(encoding="utf-8")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("append_job_feedback_to_dataset: unreadable %s: %s", feedback_path, exc)
        return 0

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0

    # Buffer records in memory and write them out only if at least
    # one ADD action produced a valid record — this avoids creating
    # an empty dataset file when the user only confirmed / rejected
    # detected spans (no new signal for the model).
    pending_records: list[str] = []
    for action in feedback.get("actions", []):
        if action.get("action") != "add":
            continue
        start = int(action.get("start", -1))
        end = int(action.get("end", -1))
        etype = action.get("entity_type", "unknown")
        if start < 0 or end <= start or end > len(text):
            continue
        record = {
            "text": text,
            "spans": [{"start": start, "end": end, "label": etype}],
        }
        pending_records.append(json.dumps(record, ensure_ascii=False) + "\n")
        appended += 1

    if pending_records:
        with _DATASET_APPEND_LOCK, dataset_path.open("a", encoding="utf-8") as out:
            out.writelines(pending_records)
    return appended


# ---------------------------------------------------------------------------
# Subprocess control
# ---------------------------------------------------------------------------


# ``opf train`` prints one progress line per epoch.  We keep the regex
# tolerant because real-world output is rarely consistent across versions.
_PROGRESS_RE = re.compile(
    r"""
    epoch\s*[:=]?\s*(\d+)        # epoch number
    .*?                           # arbitrary separator
    loss\s*[:=]?\s*([\d.]+)       # current loss
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Per-epoch time: ``epoch=3/10 loss=0.42 took=12.3s eta=01:32:00``
_ETA_RE = re.compile(r"eta\s*[:=]?\s*(\d+):(\d{2}):(\d{2})", re.IGNORECASE)


@dataclass
class TrainingCommandSpec:
    """How to invoke ``opf train`` for the admin button.

    The defaults are conservative — the user can override them via
    :class:`neironir.config.Settings`.
    """

    opf_cmd: list[str] = field(default_factory=lambda: ["opf"])
    dataset_path: Path | None = None
    output_dir: Path | None = None
    epochs: int = 3
    extra_args: list[str] = field(default_factory=list)
    # Hard timeout for the subprocess — 3 hours by default.
    # If the training hangs beyond this limit the process is
    # terminated and the status is set to ``FAILED``.
    timeout_seconds: int = 10800


async def start_training(spec: TrainingCommandSpec) -> TrainingState:
    """Spawn ``opf train`` and start the background progress reader.

    Idempotency: if a training run is already in flight the function
    raises :class:`RuntimeError` so the admin UI shows a clean error
    rather than silently launching a second subprocess.

    Args:
        spec: How to invoke ``opf train``.

    Returns:
        The shared :class:`TrainingState` (snapshot taken after
        the status has been flipped to ``RUNNING``).
    """
    async with _STATE_LOCK:
        if _STATE.status == TrainingStatus.RUNNING:
            raise RuntimeError("training is already running")

        if spec.dataset_path is None or spec.output_dir is None:
            raise ValueError("dataset_path and output_dir must be provided")

        cmd = (
            list(spec.opf_cmd)
            + [
                "train",
                "--data",
                str(spec.dataset_path),
                "--output",
                str(spec.output_dir),
                "--epochs",
                str(spec.epochs),
            ]
            + list(spec.extra_args)
        )

        logger.info("launching opf train: %s", " ".join(shlex.quote(c) for c in cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _STATE.status = TrainingStatus.RUNNING
        _STATE.started_at = datetime.now(UTC)
        _STATE.finished_at = None
        _STATE.pid = proc.pid
        _STATE._process = proc
        _STATE.dataset_path = str(spec.dataset_path)
        _STATE.checkpoint_path = str(spec.output_dir)
        _STATE.progress = TrainingProgress(total_epochs=spec.epochs)
        _STATE.error = None
        _STATE.log_tail.clear()

        asyncio.create_task(_monitor(proc, timeout_seconds=spec.timeout_seconds))

        return _STATE


async def stop_training() -> bool:
    """Ask the running ``opf train`` to exit gracefully.

    Sends ``SIGTERM`` and flips the status to ``CANCELLING``.  The
    background reader transitions to ``CANCELLED`` once the process
    actually exits.

    Returns:
        ``True`` if a process was signalled; ``False`` if nothing was
        running.
    """
    async with _STATE_LOCK:
        state = get_training_state()
        if state.status != TrainingStatus.RUNNING:
            return False
        if state._process is None:
            return False
        try:
            proc = state._process
            proc.terminate()
            return True
        except ProcessLookupError:
            return False


async def _monitor(proc: asyncio.subprocess.Process, timeout_seconds: int = 10800) -> None:
    """Read ``opf train`` output and update :data:`_STATE`.

    Runs as a background task for the lifetime of the subprocess.  On
    exit it flips the status to ``COMPLETED`` / ``FAILED`` /
    ``CANCELLED`` depending on the return code.

    Both stdout and stderr are drained concurrently — if we only read
    one, the other may fill its pipe buffer and block the subprocess.

    If the subprocess runs longer than ``timeout_seconds`` it is
    terminated with SIGTERM and the status is set to ``FAILED``.
    """
    log_tail: list[str] = [""]
    log_tail.clear()
    stderr_lines: list[str] = [""]
    stderr_lines.clear()

    async def _read_stdout() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            log_tail.append(text)
            if len(log_tail) > 50:
                log_tail.pop(0)
            _STATE.log_tail = list(log_tail)

            match = _PROGRESS_RE.search(text)
            eta_match = _ETA_RE.search(text)
            if match or eta_match:
                # Build a new snapshot atomically to avoid the reader
                # seeing a partially-updated progress (e.g. epoch
                # bumped but loss still from the previous epoch).
                epoch = _STATE.progress.epoch
                loss = _STATE.progress.loss
                eta = _STATE.progress.eta_seconds
                if match:
                    epoch = int(match.group(1))
                    with contextlib.suppress(ValueError):
                        loss = float(match.group(2))
                if eta_match:
                    h, m_val, s_val = (
                        int(eta_match.group(1)),
                        int(eta_match.group(2)),
                        int(eta_match.group(3)),
                    )
                    eta = h * 3600 + m_val * 60 + s_val
                _STATE.progress = TrainingProgress(
                    epoch=epoch,
                    total_epochs=_STATE.progress.total_epochs,
                    loss=loss,
                    eta_seconds=eta,
                )

    async def _read_stderr() -> None:
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                stderr_lines.append(text)

    try:
        await asyncio.wait_for(
            asyncio.gather(_read_stdout(), _read_stderr()),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("opf train timed out after %s seconds — terminating", timeout_seconds)
        proc.terminate()
        await proc.wait()
        _STATE.status = TrainingStatus.FAILED
        _STATE.error = f"Training timed out after {timeout_seconds} seconds."
        return
    except Exception as exc:  # noqa: BLE001 — we never want the monitor to crash
        logger.exception("training monitor crashed")
        _STATE.error = str(exc)
        await proc.wait()
        return

    await proc.wait()

    _STATE.finished_at = datetime.now(UTC)
    if _STATE.status == TrainingStatus.RUNNING:
        if proc.returncode == 0:
            _STATE.status = TrainingStatus.COMPLETED
        elif proc.returncode is not None and proc.returncode < 0:
            _STATE.status = TrainingStatus.CANCELLED
        else:
            _STATE.status = TrainingStatus.FAILED
            if stderr_lines:
                _STATE.error = stderr_lines[-1]


# ---------------------------------------------------------------------------
# Convenience: build dataset + spawn in one call (used by the API)
# ---------------------------------------------------------------------------


async def start_training_from_feedback(
    *,
    storage_dir: Path,
    output_dir: Path,
    opf_cmd: list[str],
    epochs: int,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 10800,
) -> TrainingState:
    """High-level helper used by the HTTP endpoint.

    Combines two sources of training data:

    * the cumulative JSONL written incrementally by
      ``POST /apply-feedback`` (one record per user ADD action);
    * the freshly-built JSONL from :func:`build_training_dataset`
      which scans every ``feedback.json`` for ADD actions (covers
      the case where the user only called ``POST /feedback`` and
      never used the apply-feedback button).

    Records are deduplicated by ``(text, start, end, label)`` so a
    single ADD action never lands in the dataset twice.  If the
    combined dataset is empty the state is flipped to ``FAILED`` and
    no subprocess is spawned.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / "combined_dataset.jsonl"
    tmp_combined = output_dir / ".combined_dataset.jsonl.tmp"

    seen: set[tuple[str, int, int, str]] = set()
    combined_count = 0
    combined_by_type: dict[str, int] = {}
    pending_lines: list[str] = []

    def _append(record: dict[str, object]) -> None:
        nonlocal combined_count
        spans = record.get("spans") or []
        if not isinstance(spans, list) or not spans:
            return
        span = spans[0]
        if not isinstance(span, dict):
            return
        key = (
            str(record.get("text", "")),
            int(span.get("start", 0)),
            int(span.get("end", 0)),
            str(span.get("label", "")),
        )
        if key in seen:
            return
        seen.add(key)
        pending_lines.append(json.dumps(record, ensure_ascii=False) + "\n")
        combined_count += 1
        label = str(span.get("label", "unknown"))
        combined_by_type[label] = combined_by_type.get(label, 0) + 1

    def _flush() -> None:
        """Flush buffered lines into ``combined_path`` atomically.

        Writes to a temporary file first, then ``os.replace()`` so
        that a crash mid-write never leaves a half-written dataset.
        """
        if not pending_lines:
            return
        tmp_combined.write_text("".join(pending_lines), encoding="utf-8")
        os.replace(str(tmp_combined), str(combined_path))

    # 1) Drain the cumulative file written by apply-feedback.
    cumulative_path = storage_dir / "checkpoints" / CUMULATIVE_DATASET_NAME
    if cumulative_path.is_file():
        try:
            with cumulative_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        _append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.warning("cannot read cumulative dataset %s: %s", cumulative_path, exc)

    # 2) Append any fresh ADD actions from feedback.json files
    #    that the user never routed through apply-feedback.
    #
    # ``build_training_dataset`` always writes to
    # ``<output_dir>/feedback_dataset.jsonl`` — the file name is
    # fixed.  That's fine here: we read it back into the combined
    # stream, then delete it so the only file the trainer sees is
    # ``combined_path``.
    #
    # ``build_training_dataset`` is synchronous I/O-heavy — run it
    # in the default thread pool to avoid blocking the event loop.
    loop = asyncio.get_running_loop()
    with suppress(FileNotFoundError):
        await loop.run_in_executor(None, build_training_dataset, storage_dir, output_dir)

    fresh_path = output_dir / "feedback_dataset.jsonl"
    if fresh_path != combined_path and fresh_path.is_file():
        try:
            with fresh_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        _append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        finally:
            with suppress(OSError):
                fresh_path.unlink()

    # Flush buffered lines to the combined dataset atomically.
    _flush()

    # Clean up the tmp file if it exists and is stale (e.g. crashed
    # mid-write from a previous run).
    with contextlib.suppress(OSError):
        tmp_combined.unlink()

    if combined_count == 0:
        _STATE.status = TrainingStatus.FAILED
        _STATE.error = "no ADD actions in feedback"
        return _STATE

    summary = TrainingDatasetSummary(
        path=combined_path,
        record_count=combined_count,
        by_entity_type=combined_by_type,
    )

    spec = TrainingCommandSpec(
        opf_cmd=opf_cmd,
        dataset_path=summary.path,
        output_dir=output_dir,
        epochs=epochs,
        extra_args=extra_args or [],
        timeout_seconds=timeout_seconds,
    )
    return await start_training(spec)


# Maximum tail size retained in state — the admin UI shows only the
# last few lines so we cap memory growth.
LOG_TAIL_MAX: int = 50


# Path of the cumulative training dataset that
# ``POST /apply-feedback`` writes to.  Lives under
# ``<storage>/checkpoints/`` so the admin's "Запустить дообучение"
# button can find it without configuration.
CUMULATIVE_DATASET_NAME = "training_dataset.jsonl"


__all__ = [
    "TrainingCommandSpec",
    "TrainingDatasetSummary",
    "TrainingProgress",
    "TrainingState",
    "TrainingStatus",
    "append_job_feedback_to_dataset",
    "build_training_dataset",
    "get_training_state",
    "reset_training_state",
    "start_training",
    "start_training_from_feedback",
    "stop_training",
]
