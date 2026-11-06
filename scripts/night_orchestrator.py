#!/usr/bin/env python3
"""Night-orchestrator: runs the SDD spec 006 Code Review Fixes as an autonomous
task pipeline, in the pattern of `workers/pipeline.run_job()` — a linear sequence
of task-steps, each with verification, progress recording and a transition to the
next task.

Usage (single night session):
    uv run python scripts/night_orchestrator.py --spec
    specs/006-code-review-fixes --tasks .ai/sdd/specs/006-code-review-fixes/tasks.md

What it does per task:
    1. read tasks.md Execution Log -> first Task not Done/blocked
    2. apply the changes described by the task (done by the human agent)
    3. run verification: ruff / mypy / pytest
    4. record progress: update Execution Log + heartbeat
    5. advance to next task

It is the *mechanic* part of the night-run recipe; the human agent is the worker
that performs the edits. After finishing, the agent calls it with --status to read
the current Execution Log and heartbeat.

Not part of this mechanic: the actual source edits, and the final
--final-verify (full `make check` equivalent).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from re import compile

from pydantic import BaseModel

ROOT = Path(__file__).parent.parent
LOG = ROOT / "docs/night-006-heartbeat.log"
HEARTBEAT_LINE = compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) T(?P<n>\d+) (Done|blocked) (?P<msg>.*)$"
)
LOG_LINE = compile(
    r"^\| (T\d+) \| (?P<status>[^|]+) \| (?P<evidence>.*)\s*$",
)

SPECS = ROOT / ".ai/sdd/specs"


class TaskLine(BaseModel):
    task: str
    status: str
    evidence: str


def read_execution_log(path: Path) -> dict[str, TaskLine]:
    """Parse the Execution Log table in tasks.md."""
    tasks: dict[str, TaskLine] = {}
    in_log = False
    for line in path.read_text("utf-8").splitlines():
        if line.startswith("## Execution Log"):
            in_log = True
            continue
        if in_log and line.startswith("|"):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 2 and parts[0].upper() == "TASK":
                continue
            if len(parts) >= 2 and parts[0].upper().startswith("T") and parts[0][1:].isdigit():
                tasks[parts[0]] = TaskLine(
                    task=parts[0],
                    status=parts[1] if len(parts) > 1 else "",
                    evidence=parts[2] if len(parts) > 2 else "",
                )
    return tasks


def write_execution_log(path: Path, tasks: dict[str, TaskLine]) -> None:
    """Rewrite the Execution Log table preserving order."""
    order = sorted(tasks, key=lambda t: int(t[1:]))
    lines = ["## Execution Log", "", "| Task | Status | Evidence |"]
    lines += ["|------|--------|----------|"]
    for name in order:
        t = tasks[name]
        lines.append(f"| {t.task} | {t.status} | {t.evidence} |")
    lines += ["", "Формат Status: `Done` / `blocked (причина)`. Evidence — вывод финальной"]
    lines += ["верификации задачи (ruff/mypy/pytest).", ""]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def next_pending(tasks: dict[str, TaskLine]) -> str | None:
    order = sorted(tasks.keys(), key=lambda t: int(t[1:]))
    for name in order:
        if tasks[name].status in ("—", ""):
            return name
    return None


def heartbeat_done(path: Path, task: str, msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    line = f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} {task} done {msg}\n"
    content = path.read_text("utf-8", errors="ignore") if path.exists() else ""
    path.write_text(content + line, "utf-8")


def heartbeat_blocked(path: Path, task: str, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    line = f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} {task} blocked {reason}\n"
    content = path.read_text("utf-8", errors="ignore") if path.exists() else ""
    path.write_text(content + line, "utf-8")


def run(cmd: str, cwd: Path) -> tuple[int, str, str]:
    r = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=1800)
    return r.returncode, r.stdout, r.stderr


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    spec_dir = SPECS / "006-code-review-fixes"
    tasks_path = spec_dir / "tasks.md"

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = args[0]
    if cmd == "status":
        tasks = read_execution_log(tasks_path)
        n_done = sum(1 for t in tasks.values() if t.status.startswith("Done"))
        n_blocked = sum(1 for t in tasks.values() if t.status.startswith("blocked"))
        n_left = len(tasks) - n_done - n_blocked
        print(f"spec: {spec_dir}")
        print(f"tasks Done={n_done} blocked={n_blocked} pending={n_left} total={len(tasks)}")
        for name in sorted(tasks, key=lambda t: int(t[1:])):
            t = tasks[name]
            print(f"  {name}: {t.status} | {t.evidence}")
        if LOG.exists():
            print("\nlast heartbeat lines:")
            print("\n".join(LOG.read_text("utf-8").splitlines()[-8:]))
        return 0

    if cmd == "verify":
        ruff = run("uv run ruff check .", ROOT)
        mypy = run("uv run mypy backend/neironir", ROOT)
        pytest = run('uv run pytest -m "not real_model" -q', ROOT)
        ok = True
        for name, res in (("ruff", ruff), ("mypy", mypy), ("pytest", pytest)):
            print(f"[{name}] exit={res[0]}")
            if res[0]:
                ok = False
                for line in (res[1] + res[2]).splitlines():
                    print("    ", line)
        if ok:
            print("VERIFY_OK")
        return 0

    if cmd == "next":
        tasks = read_execution_log(tasks_path)
        name = next_pending(tasks)
        print(name if name else "NO_PENDING_TASKS")
        return 0

    if cmd == "setdone":
        task, msg = args[1], " ".join(args[2:])
        tasks = read_execution_log(tasks_path)
        if task not in tasks:
            print(f"unknown task {task}", file=sys.stderr)
            return 1
        tasks[task].status = "Done"
        tasks[task].evidence = msg
        write_execution_log(tasks_path, tasks)
        heartbeat_done(LOG, task, msg[:80])
        print(f"{task} DONE: {msg}")
        return 0

    if cmd == "setblocked":
        task, reason = args[1], " ".join(args[2:])
        tasks = read_execution_log(tasks_path)
        if task not in tasks:
            print(f"unknown task {task}", file=sys.stderr)
            return 1
        tasks[task].status = f"blocked ({reason})"
        write_execution_log(tasks_path, tasks)
        heartbeat_blocked(LOG, task, reason)
        print(f"{task} BLOCKED: {reason}")
        return 0

    print(
        f"unknown command: {cmd} (status | verify | next | setdone <task> <msg> "
        "| setblocked <task> <reason>)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
