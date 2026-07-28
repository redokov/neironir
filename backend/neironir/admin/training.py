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
import re
import shlex
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

        cmd = list(spec.opf_cmd) + [
            "train",
            "--data",
            str(spec.dataset_path),
            "--output",
            str(spec.output_dir),
            "--epochs",
            str(spec.epochs),
        ] + list(spec.extra_args)

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
        _STATE.dataset_path = str(spec.dataset_path)
        _STATE.checkpoint_path = str(spec.output_dir)
        _STATE.progress = TrainingProgress(total_epochs=spec.epochs)
        _STATE.error = None
        _STATE.log_tail.clear()

        asyncio.create_task(_monitor(proc))

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
    state = get_training_state()
    if state.status != TrainingStatus.RUNNING:
        return False
    if state.pid is None:
        return False
    try:
        # ``kill`` is async on Unix and sync on Windows.  Both accept
        # the process object — we discover the live proc via the PID
        # through psutil would be heavy, so we just send to the PID
        # directly using ``os.kill`` which works on Windows for the
        # supported signals via the ``signal.CTRL_*`` family.  For the
        # MVP we accept that cancellation is best-effort on Windows.
        import os
        import signal

        os.kill(state.pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


async def _monitor(proc: asyncio.subprocess.Process) -> None:
    """Read ``opf train`` output and update :data:`_STATE`.

    Runs as a background task for the lifetime of the subprocess.  On
    exit it flips the status to ``COMPLETED`` / ``FAILED`` /
    ``CANCELLED`` depending on the return code.

    Both stdout and stderr are drained concurrently — if we only read
    one, the other may fill its pipe buffer and block the subprocess.
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
            if match:
                _STATE.progress.epoch = int(match.group(1))
                with contextlib.suppress(ValueError):
                    _STATE.progress.loss = float(match.group(2))

            eta = _ETA_RE.search(text)
            if eta:
                h, m, s = (int(eta.group(1)), int(eta.group(2)), int(eta.group(3)))
                _STATE.progress.eta_seconds = h * 3600 + m * 60 + s

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
        await asyncio.gather(_read_stdout(), _read_stderr())
    except Exception as exc:  # noqa: BLE001 — we never want the monitor to crash
        logger.exception("training monitor crashed")
        _STATE.error = str(exc)

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
) -> TrainingState:
    """High-level helper used by the HTTP endpoint.

    Builds the dataset from feedback, then delegates to
    :func:`start_training`.  If the dataset is empty the state is
    flipped to ``FAILED`` and the helper returns immediately without
    spawning a subprocess.
    """
    try:
        summary = build_training_dataset(storage_dir, output_dir)
    except FileNotFoundError as exc:
        _STATE.status = TrainingStatus.FAILED
        _STATE.error = str(exc)
        return _STATE

    spec = TrainingCommandSpec(
        opf_cmd=opf_cmd,
        dataset_path=summary.path,
        output_dir=output_dir,
        epochs=epochs,
        extra_args=extra_args or [],
    )
    return await start_training(spec)


# Maximum tail size retained in state — the admin UI shows only the
# last few lines so we cap memory growth.
LOG_TAIL_MAX: int = 50


__all__ = [
    "TrainingCommandSpec",
    "TrainingDatasetSummary",
    "TrainingProgress",
    "TrainingState",
    "TrainingStatus",
    "build_training_dataset",
    "get_training_state",
    "reset_training_state",
    "start_training",
    "start_training_from_feedback",
    "stop_training",
]