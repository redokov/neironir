"""Rewrite the cleaned file in response to user feedback.

The :class:`FeedbackApplier` takes a previously-redacted document and
applies the user's corrections on top of it.  The corrections come from
the ``feedback.json`` written by the review UI.

Important offset convention
---------------------------

The frontend shows the **original** extracted text (the contents of
``extracted_text.txt``) with detected spans highlighted.  All feedback
``start``/``end`` offsets are relative to that original text, **not**
to the cleaned file.  The applier translates them into ``result.{ext}``
offsets using the alignments between annotations and placeholders, then
applies the corrections.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from neironir.converters.base import DocumentConverter, Replacement
from neironir.converters.markdown import MarkdownConverter
from neironir.domain.entity_type import TEMPLATE_FORMAT, EntityType
from neironir.storage.local import atomic_write

logger = logging.getLogger(__name__)


_CONVERTERS: dict[str, DocumentConverter] = {
    "md": MarkdownConverter(),
}

# Per-job history of applied feedback actions (T5 idempotency).
_HISTORY_NAME = "applied_actions.json"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplySummary:
    """Counters returned by :meth:`FeedbackApplier.apply`."""

    applied: int
    added: int
    kept: int
    rejected: int


class OverlappingActionsError(ValueError):
    """Raised when two or more feedback actions cover the same text range.

    The conflicting pairs are reported so the API can surface them as a 400
    response **before** anything is written to disk.
    """

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__("overlapping feedback actions: " + "; ".join(conflicts))


@dataclass(frozen=True)
class SpanAlignment:
    """Maps one original annotation to its slice in the cleaned text."""

    orig_start: int
    orig_end: int
    cleaned_start: int
    cleaned_end: int
    entity_type: EntityType
    placeholder: str  # the actual placeholder string from the cleaned file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _placeholder_re(entity_type: EntityType) -> re.Pattern[str]:
    """Compile a regex that matches ``<NAME{n}>`` for any ``n`` ≥ 1."""
    template = TEMPLATE_FORMAT[entity_type].format(n=99999)
    head = template.split("99999")[0]
    tail = template.split("99999")[1]
    return re.compile(re.escape(head) + r"(\d+)" + re.escape(tail))


def _initial_counters(result_text: str) -> dict[EntityType, int]:
    """Return the highest placeholder number per entity type in ``result_text``."""
    counters: dict[EntityType, int] = {}
    for entity_type in EntityType:
        pattern = _placeholder_re(entity_type)
        max_seen = 0
        for match in pattern.finditer(result_text):
            n = int(match.group(1))
            if n > max_seen:
                max_seen = n
        counters[entity_type] = max_seen
    return counters


def _canonical(value: object) -> str:
    """Stable string form of an action field.

    ``entity_type`` arrives as an :class:`~neironir.domain.entity_type.EntityType`
    member from the schema but as its plain string value after the JSON
    round-trip through ``applied_actions.json``; both must hash to the same
    key or repeated applies are treated as new actions (T5 idempotency).
    """
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _action_key(action: dict[str, object]) -> tuple[str, str, str, str, str, str]:
    """Canonical identity of a feedback action used for history dedup."""
    return (
        _canonical(action.get("action", "")),
        _canonical(action.get("start", "")),
        _canonical(action.get("end", "")),
        _canonical(action.get("entity_type", "")),
        _canonical(action.get("text", "")),
        _canonical(action.get("original_span_index", "")),
    )


def _validate_no_overlaps(
    new_actions: list[dict[str, object]],
    annotations: list[dict[str, object]],
) -> None:
    """Reject overlapping ADD ranges **before** any write (T6).

    Only ``add`` actions inject text into ranges that were not detected by
    the model; two adds covering the same source span produce ambiguous
    offsets and must be refused. ``reject`` actions target *detected*
    annotations by index — an add may legitimately select a sub-range of a
    rejected annotation, so rejects are not part of this check.
    """
    ranges: list[tuple[int, int, str]] = []
    for i, action in enumerate(new_actions):
        if str(action.get("action", "")) != "add":
            continue
        start = int(str(action.get("start", -1)))
        end = int(str(action.get("end", -1)))
        if start < 0 or end <= start:
            continue  # malformed — skipped later with a warning
        ranges.append((start, end, f"add[{i}] ({start}..{end})"))

    conflicts: list[str] = []
    for a in range(len(ranges)):
        for b in range(a + 1, len(ranges)):
            s1, e1, l1 = ranges[a]
            s2, e2, l2 = ranges[b]
            if s1 < e2 and s2 < e1:
                conflicts.append(f"{l1} overlaps {l2}")
    if conflicts:
        raise OverlappingActionsError(conflicts)


def _load_applied_actions(job_dir: Path) -> list[dict[str, object]]:
    """Return the action history written by previous ``apply`` calls.

    An absent or unreadable history is treated as empty so that legacy
    job directories keep working.
    """
    path = job_dir / _HISTORY_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict)]


def _converter_for(ext: str) -> DocumentConverter:
    """Return the converter registered for ``ext`` or raise :class:`ValueError`."""
    try:
        return _CONVERTERS[ext]
    except KeyError as exc:
        raise ValueError(f"no converter registered for extension {ext!r}") from exc


def _list_placeholders(cleaned_text: str) -> list[tuple[EntityType, int, int, str]]:
    """Return every placeholder in ``cleaned_text`` as ``(type, start, end, text)``."""
    out: list[tuple[EntityType, int, int, str]] = []
    for entity_type in EntityType:
        pattern = _placeholder_re(entity_type)
        for m in pattern.finditer(cleaned_text):
            out.append((entity_type, m.start(), m.end(), m.group(0)))
    out.sort(key=lambda t: t[1])
    return out


class AlignmentMismatchError(ValueError):
    """Raised when annotations and placeholders cannot be paired 1:1 (T7).

    The pipeline writes placeholders strictly left-to-right in annotation
    order, so the i-th annotation must correspond to the i-th placeholder.
    A mismatch means the alignment is untrustworthy and applying feedback
    could restore *someone else's* text — the applier refuses the whole
    action set instead.
    """


def _build_alignment(
    annotations: list[dict[str, object]],
    cleaned_text: str,
    original_text: str,
) -> dict[int, SpanAlignment]:
    """Pair the i-th annotation with the i-th placeholder, verifying offsets.

    Returns a mapping ``{annotation_index: SpanAlignment}``.  Strict
    positional pairing replaces the previous entity-type scan, which could
    bind an annotation to another span's placeholder when an annotation of
    the same type was missing from ``annotations.json`` (T7 regression).

    Raises :class:`AlignmentMismatchError` when counts differ, the entity
    types disagree, or the recorded original offsets do not match the
    source text at those positions.
    """
    placeholders = _list_placeholders(cleaned_text)
    if len(placeholders) != len(annotations):
        raise AlignmentMismatchError(
            f"placeholder count {len(placeholders)} != annotation count {len(annotations)}"
        )

    alignments: dict[int, SpanAlignment] = {}
    for i, (ann, ph) in enumerate(zip(annotations, placeholders, strict=True)):
        try:
            orig_start = int(str(ann["start"]))
            orig_end = int(str(ann["end"]))
            entity_type = EntityType(str(ann.get("entity_type", "")))
        except (KeyError, ValueError) as exc:
            raise AlignmentMismatchError(f"annotation {i} is malformed: {exc}") from exc
        # T7: verify the recorded offsets against the source text.
        snippet = original_text[orig_start:orig_end]
        ann_text = str(ann.get("text", ""))
        if ann_text and snippet != ann_text:
            raise AlignmentMismatchError(
                f"annotation {i}: text mismatch at {orig_start}..{orig_end}"
            )
        if ph[0] != entity_type:
            raise AlignmentMismatchError(
                f"annotation {i}: type {entity_type.value} != placeholder {ph[3]}"
            )
        alignments[i] = SpanAlignment(
            orig_start=orig_start,
            orig_end=orig_end,
            cleaned_start=ph[1],
            cleaned_end=ph[2],
            entity_type=entity_type,
            placeholder=ph[3],
        )
    return alignments


def _map_original_offset(
    alignments: dict[int, SpanAlignment],
    orig_offset: int,
    original_len: int,
) -> int | None:
    """Translate ``orig_offset`` into the cleaned-text coordinate.

    The map is piecewise-linear: between placeholders, characters map
    1:1 (the only changes between original and cleaned text are the
    placeholder substitutions, which are tracked by ``alignments``).
    """
    if orig_offset < 0 or orig_offset > original_len:
        return None

    cleaned_cursor = 0
    orig_cursor = 0
    for al in alignments.values():
        # Plain segment before this alignment.
        if al.orig_start > orig_cursor:
            plain_len = al.orig_start - orig_cursor
            if orig_offset <= orig_cursor + plain_len:
                return cleaned_cursor + (orig_offset - orig_cursor)
            orig_cursor += plain_len
            cleaned_cursor += plain_len
        # Placeholder segment.
        if orig_cursor <= orig_offset < al.orig_end:
            return int(al.cleaned_start)
        if orig_offset >= al.orig_end:
            orig_cursor = al.orig_end
            cleaned_cursor = al.cleaned_end

    # Tail plain segment.
    tail_len = original_len - orig_cursor
    if orig_offset - orig_cursor <= tail_len:
        return cleaned_cursor + (orig_offset - orig_cursor)
    return None


# ---------------------------------------------------------------------------
# Main applier
# ---------------------------------------------------------------------------


class FeedbackApplier:
    """Apply user feedback to a previously-redacted document."""

    def apply(
        self,
        *,
        job_dir: Path,
        output_ext: Literal["md", "docx"],
        feedback_actions: Iterable[dict[str, object]],
    ) -> ApplySummary:
        """Rewrite ``result.{ext}`` with the user's corrections applied."""
        # Validate ``output_ext`` up front so we never try to read
        # ``result.pdf`` and emit a confusing ``FileNotFoundError``.
        if output_ext not in ("md", "docx"):
            raise ValueError(
                f"unsupported output_ext {output_ext!r}; only 'md' and 'docx' are supported"
            )

        original_text_path = job_dir / "extracted_text.txt"
        if not original_text_path.is_file():
            raise FileNotFoundError(f"extracted_text.txt missing in {job_dir}")
        original_text = original_text_path.read_text(encoding="utf-8")

        result_path = job_dir / f"result.{output_ext}"
        if not result_path.is_file():
            raise FileNotFoundError(f"result.{output_ext} missing in {job_dir}")

        # T5 (idempotency): the first apply snapshots the pipeline output as
        # ``result.orig.{ext}``; this and every later apply builds from that
        # snapshot instead of the already-rewritten ``result.{ext}``, so
        # repeated applies of the same actions are a no-op and the
        # annotation-based alignments never drift.
        pristine_path = job_dir / f"result.orig.{output_ext}"
        base_path = pristine_path if pristine_path.is_file() else result_path

        annotations_path = job_dir / "annotations.json"
        if not annotations_path.is_file():
            raise FileNotFoundError(f"annotations.json missing in {job_dir}")
        try:
            annotations: list[dict[str, object]] = json.loads(
                annotations_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            raise FileNotFoundError(f"annotations.json unreadable: {exc}") from exc

        converter = _converter_for(output_ext)
        cleaned_text = converter.extract_text(base_path)
        alignments = _build_alignment(annotations, cleaned_text, original_text)

        counters = _initial_counters(cleaned_text)

        actions = list(feedback_actions)
        actions.sort(
            key=lambda a: (
                {"add": 0, "reject": 1, "confirm": 2}.get(str(a.get("action", "")), 9),
                a.get("start", 0),
            )
        )

        # T5 idempotency: actions from earlier calls are recorded in
        # ``applied_actions.json``.  Only actions not yet applied are
        # processed now; the full merged history is replayed on top of the
        # pristine base so a repeated apply is a no-op and the placeholder
        # numbering stays monotonic across calls.
        history = _load_applied_actions(job_dir)
        seen = {_action_key(a) for a in history}
        new_actions = [a for a in actions if _action_key(a) not in seen]
        if not new_actions:
            return ApplySummary(applied=0, added=0, kept=0, rejected=0)
        # T6: refuse overlapping ranges before touching any file.
        _validate_no_overlaps(new_actions, annotations)
        merged = history + new_actions
        n_history = len(history)

        applied = added = kept = rejected = 0
        reject_replacements: list[Replacement] = []
        add_replacements: list[Replacement] = []

        for idx, action in enumerate(merged):
            is_new = idx >= n_history
            kind = action.get("action")
            etype_str = action.get("entity_type", "")
            try:
                entity_type = EntityType(etype_str)
            except ValueError:
                logger.warning("unknown entity type %r in feedback", etype_str)
                continue

            if kind == "add":
                orig_start = int(str(action.get("start", -1)))
                orig_end = int(str(action.get("end", -1)))
                if orig_start < 0 or orig_end <= orig_start or orig_end > len(original_text):
                    logger.warning("ignoring malformed add action: %r", action)
                    continue
                c_start = _map_original_offset(alignments, orig_start, len(original_text))
                c_end = _map_original_offset(alignments, orig_end, len(original_text))
                if c_start is None or c_end is None:
                    logger.warning("could not map add offsets: %d..%d", orig_start, orig_end)
                    continue
                if c_end < c_start:
                    c_end = c_start
                counters[entity_type] += 1
                placeholder = TEMPLATE_FORMAT[entity_type].format(n=counters[entity_type])
                add_replacements.append(
                    Replacement(
                        start=c_start,
                        end=c_end,
                        entity_type=entity_type,
                        placeholder=placeholder,
                    )
                )
                if is_new:
                    added += 1
                    applied += 1
            elif kind == "reject":
                original_index = action.get("original_span_index")
                if original_index is None:
                    continue
                original_index = int(str(original_index))
                if original_index < 0 or original_index >= len(annotations):
                    continue
                ann = annotations[original_index]
                # T7: look the alignment up by annotation index directly; the
                # previous (start, end) key could bind the annotation to a
                # different placeholder when spans were skipped.
                alignment = alignments.get(original_index)
                if alignment is None:
                    logger.warning("could not reject span idx=%s — no alignment", original_index)
                    continue
                reject_replacements.append(
                    Replacement(
                        start=alignment.cleaned_start,
                        end=alignment.cleaned_end,
                        entity_type=alignment.entity_type,
                        placeholder=str(ann.get("text", "")),
                    )
                )
                if is_new:
                    rejected += 1
                    applied += 1
            elif kind == "confirm":
                if is_new:
                    kept += 1
            else:
                logger.warning("unknown action %r", kind)

        # Rejections first (they shrink the text), then additions
        # (they grow it).  Within each group, right-to-left so that
        # earlier offsets remain valid as the string mutates.
        all_replacements = reject_replacements + add_replacements
        if all_replacements:
            # Only Markdown output is reachable here — the API endpoint
            # rejects DOCX output with HTTP 400 (``docx_output_not_supported``)
            # before this method is ever called. Keeping a single code path
            # avoids the previous dead DOCX branch and its fragile
            # ``EntityType(...) in __members__.values()`` guard that could
            # raise ``ValueError`` before the ``in`` check ran.
            assert output_ext == "md", "apply-feedback requires Markdown output"  # noqa: S101
            text = cleaned_text
            # Right-to-left application on the cleaned text.
            ordered = sorted(all_replacements, key=lambda r: r.start, reverse=True)
            for replacement in ordered:
                text = text[: replacement.start] + replacement.placeholder + text[replacement.end :]
            # Snapshot the pristine result before the first rewrite so that
            # subsequent applies rebase on the original content.
            if not pristine_path.is_file():
                atomic_write(pristine_path, result_path.read_bytes())
            atomic_write(result_path, text)  # atomic write prevents partial files on crash

        # Record the merged action history so future calls can replay it.
        # Written after the result update so a crash between the two writes
        # only causes the actions to be re-applied idempotently next time.
        atomic_write(
            job_dir / _HISTORY_NAME,
            json.dumps(merged, ensure_ascii=False, indent=2),
        )

        self._persist_counters(job_dir, counters)

        return ApplySummary(
            applied=applied,
            added=added,
            kept=kept,
            rejected=rejected,
        )

    def _persist_counters(self, job_dir: Path, counters: dict[EntityType, int]) -> None:
        """Write the latest placeholder numbering for the next apply-feedback call."""
        path = job_dir / "counters.json"
        atomic_write(
            path,
            json.dumps(
                {entity_type.value: n for entity_type, n in counters.items()},
                ensure_ascii=False,
            ),
        )


__all__ = ["ApplySummary", "FeedbackApplier", "SpanAlignment"]
