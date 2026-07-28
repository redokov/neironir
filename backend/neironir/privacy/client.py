"""Adapter around the privacy-filter model.

Two implementations live in this module:

* :class:`MockPrivacyFilterClient` — a regex-based stub. Used during
  development and in the integration tests so the suite doesn't depend
  on having the model weights present.
* :class:`SubprocessPrivacyFilterClient` — the production client. It
  shells out to the privacy-filter CLI (``opf``) as a subprocess and
  reads the JSON response. The CLI invocation contract is documented
  in :mod:`docs.agents.03-backend` §3.0.

The protocol :class:`PrivacyFilterClient` is the abstraction the
pipeline depends on. It is intentionally minimal: one async method,
:func:`annotate`, that takes a plain string and returns a list of
:class:`EntitySpan` instances.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from neironir.domain.entity_type import EntityType

logger = logging.getLogger(__name__)


class PrivacyFilterError(Exception):
    """Raised when the privacy-filter subprocess fails or returns bad data."""


@dataclass(frozen=True)
class EntitySpan:
    """A single detected entity in the input text.

    The offsets are positions in the original ``text`` string passed to
    :meth:`PrivacyFilterClient.annotate`. The ``entity_type`` is mapped
    from the model's ``label`` (snake_case) to our closed :class:`EntityType`
    enum.
    """

    start: int
    end: int
    entity_type: EntityType


class PrivacyFilterClient(Protocol):
    """Abstraction over the privacy-filter model."""

    async def annotate(self, text: str) -> list[EntitySpan]:
        """Return the detected spans for ``text``."""
        ...


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------


# Patterns derived from the spec (docs/agents/03-backend.md §3.1). They
# are deliberately conservative; the goal is to provide a usable signal
# for tests and UI development, not to match the real model.
_EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
# Two pieces of defensive logic here:
# 1. ``(?<!\d)`` / ``(?!\d)`` keep the phone regex from matching a
#    short snippet inside a larger digit run (e.g. a 16-digit card).
# 2. ``(?!\d{16,})`` rejects runs that should belong to an
#    ``account_number`` per the spec (``\b\d{16,20}\b``). Without this
#    guard a phone match would greedily capture a 16-20 digit card
#    number and win by priority order, hiding the entity type.
_PHONE_PATTERN = r"(?<!\d)(?!\d{16,})\+?\d[\d\s\-()]{7,}\d(?!\d)"
_URL_PATTERN = r"https?://\S+"
_DATE_PATTERN = r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"
_ACCOUNT_PATTERN = r"\b\d{16,20}\b"
_SECRET_PATTERN = r"(?i)(?:password|passwd|pwd)\s*[:=]\s*\S+"


# Priority order for overlap resolution. A rule earlier in the list
# wins over a rule later in the list. ``PRIVATE_PERSON`` and
# ``PRIVATE_ADDRESS`` are intentionally omitted — the mock never
# produces them.
_RULES: list[tuple[EntityType, str]] = [
    (EntityType.PRIVATE_EMAIL, _EMAIL_PATTERN),
    (EntityType.PRIVATE_PHONE, _PHONE_PATTERN),
    (EntityType.PRIVATE_URL, _URL_PATTERN),
    (EntityType.PRIVATE_DATE, _DATE_PATTERN),
    (EntityType.ACCOUNT_NUMBER, _ACCOUNT_PATTERN),
    (EntityType.SECRET, _SECRET_PATTERN),
]


class MockPrivacyFilterClient:
    """A regex-based privacy-filter stand-in.

    Overlapping spans are resolved in favour of the rule earlier in
    ``_RULES``. ``PRIVATE_PERSON`` and ``PRIVATE_ADDRESS`` are never
    detected by the mock — the real model is the only source of those
    labels.
    """

    async def annotate(self, text: str) -> list[EntitySpan]:
        """Detect entities in ``text`` using regex heuristics."""
        candidates: list[EntitySpan] = []
        for entity_type, pattern in _RULES:
            for match in re.finditer(pattern, text):
                candidates.append(
                    EntitySpan(
                        start=match.start(),
                        end=match.end(),
                        entity_type=entity_type,
                    )
                )

        # The dedup helper is the single source of truth for overlap
        # resolution — it must keep the higher-priority rule across
        # the whole input, not just at equal ``start`` positions.
        return _deduplicate(candidates)


def _priority(entity_type: EntityType) -> int:
    """Lower is more specific. Used as a tie-breaker for equal start offsets."""
    for index, (candidate, _) in enumerate(_RULES):
        if candidate == entity_type:
            return index
    # Not in the list (e.g. PRIVATE_PERSON from a future mock expansion).
    return len(_RULES)


def _deduplicate(spans: list[EntitySpan]) -> list[EntitySpan]:
    """Drop spans that overlap with a higher-priority earlier span.

    The priority order is the one declared in :data:`_RULES` — the
    earlier a rule sits, the more specific it is (email is more
    specific than phone, phone is more specific than URL, …). When
    two spans overlap, only the highest-priority one survives. The
    output is sorted by start so callers can iterate replacements
    in ascending position; ties on start keep the higher-priority
    span first, matching how the pipeline breaks ties when assigning
    placeholder numbers.
    """
    if not spans:
        return []
    # Process in priority order: the higher-priority rule (lower
    # numeric priority) goes first. An incoming span is added to the
    # kept list only if no already-kept span overlaps with it. The
    # sweep below guarantees that the kept list at every step has no
    # overlaps, so the first sweep suffices — re-sorting at the end
    # is purely cosmetic.
    ordered = sorted(spans, key=lambda s: (_priority(s.entity_type), s.start))
    kept: list[EntitySpan] = []
    for span in ordered:
        if any(_overlaps(span, kept_span) for kept_span in kept):
            continue
        kept.append(span)
    kept.sort(key=lambda s: (s.start, _priority(s.entity_type)))
    return kept


def _overlaps(a: EntitySpan, b: EntitySpan) -> bool:
    """Return True if ``a`` and ``b`` cover at least one shared character."""
    return not (a.end <= b.start or b.end <= a.start)


# ---------------------------------------------------------------------------
# Subprocess client
# ---------------------------------------------------------------------------


class SubprocessPrivacyFilterClient:
    """Privacy-filter driver that invokes the ``opf`` CLI as a subprocess.

    The exact CLI invocation is captured in
    ``docs/agents/03-backend.md`` §3.0. The client:

    1. Writes the input text to a temporary file (the CLI requires a
       file input — stdin would split the document on newlines).
    2. Spawns ``opf`` with ``--format json --output-mode typed
       --decode-mode viterbi --device {device}``.
    3. Awaits completion with a configurable timeout.
    4. Parses the JSON payload and validates ``schema_version == 1``.
    5. Filters out labels that are not in our :class:`EntityType` enum.
    """

    def __init__(
        self,
        *,
        opf_cmd: list[str] | None = None,
        checkpoint_dir: Path | None = None,
        device: str = "cpu",
        timeout_s: float = 120.0,
    ) -> None:
        self._opf_cmd = opf_cmd if opf_cmd is not None else ["opf"]
        self._checkpoint_dir = checkpoint_dir
        self._device = device
        self._timeout_s = timeout_s

    async def annotate(self, text: str) -> list[EntitySpan]:
        """Invoke ``opf`` and return the detected spans."""
        tmp_path = self._write_temp_file(text)
        try:
            stdout_b, stderr_b = await self._run_subprocess(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if stderr_b:
            # ``opf`` writes a latency summary and checkpoint download
            # logs to stderr. Surface it through the project logger so
            # operators can correlate timings without polluting stdout.
            logger.debug("opf stderr: %s", _decode_opf_output(stderr_b))

        raw_text = _decode_opf_output(stdout_b)
        # ``opf`` appends a colour legend and a colour-coded text preview
        # after the JSON payload — strip everything after the first complete
        # JSON object.
        json_end = _find_json_end(raw_text)
        payload = json.loads(raw_text[:json_end])
        self._validate_schema_version(payload)
        return [
            EntitySpan(start=int(span["start"]), end=int(span["end"]), entity_type=entity_type)
            for span, entity_type in (
                (raw, self._coerce_label(raw["label"])) for raw in payload.get("detected_spans", [])
            )
            if entity_type is not None
        ]

    # -- internal helpers ---------------------------------------------

    @staticmethod
    def _write_temp_file(text: str) -> Path:
        """Write ``text`` to a UTF-8 temporary file and return its path."""
        # ``delete=False`` so we can pass the path to the subprocess on
        # Windows, where the file must outlive the ``with`` block. We
        # own the cleanup in the caller's ``finally``.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(text)
            return Path(tmp.name)

    def _build_command(self, tmp_path: Path) -> list[str]:
        """Compose the subprocess command line."""
        cmd = list(self._opf_cmd)
        cmd += [
            "-f",
            str(tmp_path),
            "--format",
            "json",
            "--output-mode",
            "typed",
            "--decode-mode",
            "viterbi",
            "--device",
            self._device,
        ]
        if self._checkpoint_dir is not None:
            cmd += ["--checkpoint", str(self._checkpoint_dir)]
        return cmd

    async def _run_subprocess(self, tmp_path: Path) -> tuple[bytes, bytes]:
        """Spawn ``opf`` and return ``(stdout, stderr)``."""
        cmd = self._build_command(tmp_path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_s)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise PrivacyFilterError(f"opf timeout after {self._timeout_s}s") from exc

        if proc.returncode != 0:
            raise PrivacyFilterError(
                f"opf exited {proc.returncode}: {stderr_b.decode('utf-8', errors='replace')}"
            )
        return stdout_b, stderr_b

    @staticmethod
    def _validate_schema_version(payload: dict[str, object]) -> None:
        """Reject payloads whose schema version is not 1."""
        if payload.get("schema_version") != 1:
            raise PrivacyFilterError(f"unexpected schema_version={payload.get('schema_version')!r}")

    @staticmethod
    def _coerce_label(label: str) -> EntityType | None:
        """Map ``label`` to an :class:`EntityType` or drop it with a warning."""
        try:
            return EntityType(label)
        except ValueError:
            logger.warning("privacy-filter emitted unknown label: %r", label)
            return None


def _decode_opf_output(data: bytes) -> str:
    """Decode ``opf`` CLI output, handling Windows encoding quirks.

    On Windows the ``opf`` subprocess writes JSON with its ``text``
    field encoded in the system's active code page (e.g. CP1251 for
    Russian Windows) rather than UTF-8. We try UTF-8 first and fall
    back to the system encoding.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # ``locale.getpreferredencoding()`` returns e.g. ``cp1251``
        # on Russian Windows.
        import locale

        encoding = locale.getpreferredencoding()
        logger.debug("opf output is not valid UTF-8; falling back to %s", encoding)
        return data.decode(encoding, errors="replace")


def _find_json_end(text: str) -> int:
    """Return the position of the closing ``}`` of the top-level JSON object.

    ``opf`` appends colour legend and colour-coded output after the
    JSON payload. This function finds where the JSON ends by tracking
    brace depth.
    """
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    # No balanced object found — let json.loads raise the error.
    return len(text)


__all__ = [
    "EntitySpan",
    "MockPrivacyFilterClient",
    "PrivacyFilterClient",
    "PrivacyFilterError",
    "SubprocessPrivacyFilterClient",
]
