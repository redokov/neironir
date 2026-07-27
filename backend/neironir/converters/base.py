"""Base converter contract used by the redaction pipeline.

The pipeline speaks a single, format-agnostic interface:

* :meth:`DocumentConverter.extract_text` returns the plain text of the
  document so the privacy-filter can annotate positions in it.
* :meth:`DocumentConverter.build` rewrites the document, replacing the
  given :class:`Replacement` runs with ``placeholder`` strings.

Implementations live in :mod:`neironir.converters.markdown` and
:mod:`neironir.converters.docx`. The contract is expressed as a
:class:`typing.Protocol` so we can keep structural typing without forcing
every converter to share a concrete base class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from neironir.domain.entity_type import EntityType


@dataclass(frozen=True)
class Replacement:
    """A single replacement to apply during :meth:`DocumentConverter.build`.

    Attributes:
        start: Inclusive offset in the extracted text (0-based).
        end: Exclusive offset in the extracted text. ``text[start:end]`` is
            the slice to be replaced with ``placeholder``.
        entity_type: The :class:`EntityType` used to generate the placeholder.
        placeholder: The fully-formatted placeholder string, e.g.
            ``"<PRIVATE_EMAIL1>"``. The number is per-document (see
            :class:`neironir.domain.placeholder.PlaceholderCounter`).
    """

    start: int
    end: int
    entity_type: EntityType
    placeholder: str


class DocumentConverter(Protocol):
    """Format-specific reader/writer for the redaction pipeline."""

    ext: str  # file extension without the leading dot, e.g. "md" or "docx"

    def extract_text(self, source: Path) -> str:
        """Return the plain text of ``source`` for annotation."""
        ...

    def build(self, source: Path, target: Path, replacements: list[Replacement]) -> None:
        """Write a copy of ``source`` with replacements applied to ``target``.

        Implementations must be tolerant to ``replacements`` being passed in
        any order; the markdown converter sorts internally.
        """
        ...


__all__ = ["DocumentConverter", "Replacement"]
