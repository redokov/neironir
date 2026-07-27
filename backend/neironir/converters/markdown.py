"""Markdown converter for the redaction pipeline.

Markdown is treated as plain UTF-8 text. The converter reads the source
file, the pipeline annotates positions in the resulting string, and we
re-write the file with replacements applied. Because ``str`` slicing is
positional, replacements are applied **right-to-left** so earlier offsets
remain valid as the string shrinks.
"""

from __future__ import annotations

from pathlib import Path

from neironir.converters.base import Replacement


class MarkdownConverter:
    """Read/write Markdown files as plain UTF-8 text."""

    ext = "md"

    def extract_text(self, source: Path) -> str:
        """Return the file contents decoded as UTF-8."""
        return source.read_text(encoding="utf-8")

    def build(self, source: Path, target: Path, replacements: list[Replacement]) -> None:
        """Write ``source`` with replacements applied to ``target``.

        Replacements are sorted by ``start`` descending so that each
        splice does not invalidate the offsets of the replacements we have
        not yet applied. The pipeline generally passes replacements in
        ascending order, but this converter is defensive against the
        reverse-order case.
        """
        text = source.read_text(encoding="utf-8")
        # Right-to-left: larger offsets first so string mutation doesn't
        # invalidate the indices of the remaining replacements.
        ordered = sorted(replacements, key=lambda r: r.start, reverse=True)
        for replacement in ordered:
            text = text[: replacement.start] + replacement.placeholder + text[replacement.end :]
        target.write_text(text, encoding="utf-8")


__all__ = ["MarkdownConverter"]
