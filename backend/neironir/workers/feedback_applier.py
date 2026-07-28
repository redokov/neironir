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
from neironir.converters.docx import DocxConverter
from neironir.converters.markdown import MarkdownConverter
from neironir.domain.entity_type import TEMPLATE_FORMAT, EntityType

logger = logging.getLogger(__name__)


_CONVERTERS: dict[str, DocumentConverter] = {
    "md": MarkdownConverter(),
    "docx": DocxConverter(),
}


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


def _build_alignment(
    annotations: list[dict[str, object]],
    cleaned_text: str,
) -> list[SpanAlignment]:
    """Pair each annotation with the placeholder the pipeline wrote for it.

    Placeholders are written left-to-right in the cleaned text in the
    same order as the annotations, so we can match them by index.
    """
    alignments: list[SpanAlignment] = []
    placeholders = _list_placeholders(cleaned_text)
    pi = 0
    for ann in annotations:
        try:
            orig_start = int(str(ann["start"]))
            orig_end = int(str(ann["end"]))
            etype_str = str(ann.get("entity_type", ""))
            entity_type = EntityType(etype_str)
        except (KeyError, ValueError):
            continue
        while pi < len(placeholders):
            ph = placeholders[pi]
            if ph[0] == entity_type:
                alignments.append(
                    SpanAlignment(
                        orig_start=orig_start,
                        orig_end=orig_end,
                        cleaned_start=ph[1],
                        cleaned_end=ph[2],
                        entity_type=entity_type,
                        placeholder=ph[3],
                    )
                )
                pi += 1
                break
            pi += 1
        else:
            logger.warning(
                "ran out of placeholders while aligning annotation %d..%d (%s)",
                orig_start, orig_end, entity_type.value,
            )
            break
    return alignments


def _map_original_offset(
    alignments: list[SpanAlignment],
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
    for al in alignments:
        # Plain segment before this alignment.
        if al.orig_start > orig_cursor:
            plain_len = al.orig_start - orig_cursor
            if orig_offset <= orig_cursor + plain_len:
                return cleaned_cursor + (orig_offset - orig_cursor)
            orig_cursor += plain_len
            cleaned_cursor += plain_len
        # Placeholder segment.
        if orig_cursor <= orig_offset <= al.orig_end:
            return al.cleaned_start
        if orig_offset > al.orig_end:
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
                f"unsupported output_ext {output_ext!r}; "
                f"only 'md' and 'docx' are supported"
            )

        original_text_path = job_dir / "extracted_text.txt"
        if not original_text_path.is_file():
            raise FileNotFoundError(f"extracted_text.txt missing in {job_dir}")
        original_text = original_text_path.read_text(encoding="utf-8")

        result_path = job_dir / f"result.{output_ext}"
        if not result_path.is_file():
            raise FileNotFoundError(f"result.{output_ext} missing in {job_dir}")

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
        cleaned_text = converter.extract_text(result_path)
        alignments = _build_alignment(annotations, cleaned_text)
        ann_to_alignment = {
            (a.orig_start, a.orig_end): a for a in alignments
        }

        counters = _initial_counters(cleaned_text)

        actions = list(feedback_actions)
        actions.sort(
            key=lambda a: (
                {"add": 0, "reject": 1, "confirm": 2}.get(str(a.get("action", "")), 9),
                a.get("start", 0),
            )
        )

        applied = added = kept = rejected = 0
        reject_replacements: list[Replacement] = []
        add_replacements: list[Replacement] = []

        for action in actions:
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
                key = (int(str(ann["start"])), int(str(ann["end"])))
                alignment = ann_to_alignment.get(key)
                if alignment is None:
                    logger.warning(
                        "could not reject span idx=%s — no alignment", original_index
                    )
                    continue
                reject_replacements.append(
                    Replacement(
                        start=alignment.cleaned_start,
                        end=alignment.cleaned_end,
                        entity_type=alignment.entity_type,
                        placeholder=str(ann.get("text", "")),
                    )
                )
                rejected += 1
                applied += 1
            elif kind == "confirm":
                kept += 1
            else:
                logger.warning("unknown action %r", kind)

        # Rejections first (they shrink the text), then additions
        # (they grow it).  Within each group, right-to-left so that
        # earlier offsets remain valid as the string mutates.
        all_replacements = reject_replacements + add_replacements
        if all_replacements:
            if output_ext == "md":
                text = cleaned_text
                # Right-to-left application on the cleaned text.
                ordered = sorted(all_replacements, key=lambda r: r.start, reverse=True)
                for replacement in ordered:
                    text = (
                        text[: replacement.start]
                        + replacement.placeholder
                        + text[replacement.end :]
                    )
                result_path.write_text(text, encoding="utf-8")
            else:
                # DOCX output: rebuild from the original source with
                # the initial pipeline replacements + user corrections
                # concatenated.  ``DocxConverter.build`` accepts
                # replacements in any order and re-sorts internally.
                source_path = job_dir / "source.docx"
                initial = [
                    Replacement(
                        start=int(str(ann["start"])),
                        end=int(str(ann["end"])),
                        entity_type=EntityType(str(ann["entity_type"])),
                        placeholder=_initial_placeholder(
                            annotations, alignments, cleaned_text, idx
                        ),
                    )
                    for idx, ann in enumerate(annotations)
                    if ann.get("entity_type")
                    and EntityType(str(ann["entity_type"])) in EntityType.__members__.values()
                ]
                converter.build(source_path, result_path, initial + all_replacements)

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
        path.write_text(
            json.dumps(
                {entity_type.value: n for entity_type, n in counters.items()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _initial_placeholder(
    annotations: list[dict[str, object]],
    alignments: list[SpanAlignment],
    cleaned_text: str,
    annotation_idx: int,
) -> str:
    """Return the placeholder string the pipeline wrote for an annotation."""
    if annotation_idx >= len(annotations):
        return ""
    ann = annotations[annotation_idx]
    key = (int(str(ann["start"])), int(str(ann["end"])))
    for al in alignments:
        if (al.orig_start, al.orig_end) == key:
            return cleaned_text[al.cleaned_start:al.cleaned_end]
    return ""


__all__ = ["ApplySummary", "FeedbackApplier", "SpanAlignment"]