"""DOCX converter for the redaction pipeline.

MVP simplification (also documented in ``docs/architecture.md`` under
"Oграничения MVP"):

* Only the body paragraphs are preserved. Headings, lists, tables, images,
  footnotes, comments, headers/footers and inline runs (bold, italic, links,
  …) are ignored. The output is a flat list of plain-text paragraphs joined
  with ``"\\n"`` by :meth:`extract_text`.
* :meth:`build` re-writes the paragraphs of the source document with
  replacements applied only to plain text. A replacement that does not
  fit entirely inside a single paragraph raises :class:`ValueError` — the
  pipeline operator must restructure the document so each detected
  entity is contained in one paragraph. The rationale is documented in
  ``docs/agents/03-backend.md``, §1.

The :class:`docx.Document` round-trip is driven through ``python-docx``;
we never read or write the OOXML zip directly.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType

from neironir.converters.base import Replacement

# Inclusive start, exclusive end. We add ``+ 1`` for the newline between
# paragraphs so that offsets in the concatenated text line up with the
# positions the privacy-filter reports.
_PARAGRAPH_SEPARATOR = "\n"


class DocxConverter:
    """Read/write DOCX files with paragraph-level fidelity only."""

    ext = "docx"

    def extract_text(self, source: Path) -> str:
        """Concatenate body paragraphs with ``\\n`` separators."""
        document = Document(str(source))
        return _PARAGRAPH_SEPARATOR.join(paragraph.text for paragraph in document.paragraphs)

    def build(self, source: Path, target: Path, replacements: list[Replacement]) -> None:
        """Re-write ``source`` with replacements applied to ``target``.

        The replacement positions are anchored in the concatenated text
        produced by :meth:`extract_text`. We rebuild the sliced text
        paragraph-by-paragraph. Replacements that cross a paragraph
        boundary are rejected because the MVP cannot faithfully stitch
        entities across paragraphs.
        """
        document: DocumentType = Document(str(source))
        paragraph_texts = [paragraph.text for paragraph in document.paragraphs]
        cumulative = _cumulative_offsets(paragraph_texts)
        rewritten = _slice_paragraphs(paragraph_texts, cumulative, replacements)

        for paragraph, new_text in zip(document.paragraphs, rewritten, strict=True):
            _replace_paragraph_text(paragraph, new_text)

        document.save(str(target))


def _cumulative_offsets(paragraph_texts: list[str]) -> list[int]:
    """Return the inclusive start offset of each paragraph in the joined text.

    The offsets include the ``\\n`` separator between paragraphs, so
    ``cumulative[i]`` is the index of ``paragraph_texts[i][0]`` in the
    concatenated string. There is one extra entry — the total length —
    which simplifies boundary checks.
    """
    offsets: list[int] = []
    cursor = 0
    for text in paragraph_texts:
        offsets.append(cursor)
        cursor += len(text) + len(_PARAGRAPH_SEPARATOR)
    offsets.append(cursor)
    return offsets


def _find_paragraph_index(cumulative: list[int], offset: int) -> int:
    """Return the index of the paragraph that contains ``offset``.

    ``cumulative`` has ``len(paragraph_texts) + 1`` entries; the last
    entry is the total length. ``offset`` is expected to be in
    ``[0, total_len)`` and is bounded by the caller.
    """
    last_paragraph = len(cumulative) - 2
    for i in range(last_paragraph, -1, -1):
        if offset >= cumulative[i]:
            return i
    # ``offset`` is negative or zero before the first paragraph — the
    # caller guarantees this doesn't happen.
    raise ValueError(f"offset {offset} is before the first paragraph")  # pragma: no cover


def _slice_paragraphs(
    paragraph_texts: list[str],
    cumulative: list[int],
    replacements: list[Replacement],
) -> list[str]:
    """Apply replacements to the paragraph texts and return the new list.

    The replacements are anchored in the concatenated text; we project
    each one into a single paragraph. Anything that doesn't fit is
    rejected with a descriptive :class:`ValueError`.

    Replacements are applied in *descending* order of ``start`` so
    that each placeholder insertion cannot invalidate the offsets of
    the replacements that have not been processed yet. The caller may
    pass the replacements in any order; this function is the single
    source of truth for sorting. (See also
    :meth:`MarkdownConverter.build` for the same strategy on plain
    text.)
    """
    rewritten = list(paragraph_texts)
    ordered = sorted(replacements, key=lambda r: r.start, reverse=True)
    for replacement in ordered:
        start_paragraph = _find_paragraph_index(cumulative, replacement.start)
        end_paragraph = _find_paragraph_index(cumulative, replacement.end - 1)

        if start_paragraph != end_paragraph:
            raise ValueError(
                "Replacement crosses paragraph boundary: "
                f"start={replacement.start} (paragraph {start_paragraph}), "
                f"end={replacement.end} (paragraph {end_paragraph}). "
                "MVP docx converter only supports replacements confined to "
                "a single paragraph."
            )

        local_start = replacement.start - cumulative[start_paragraph]
        local_end = replacement.end - cumulative[start_paragraph]
        rewritten[start_paragraph] = (
            rewritten[start_paragraph][:local_start]
            + replacement.placeholder
            + rewritten[start_paragraph][local_end:]
        )

    return rewritten


def _replace_paragraph_text(paragraph: object, new_text: str) -> None:
    """Replace the paragraph's text, preserving no other formatting.

    The MVP deliberately discards runs — see the module docstring. We
    clear every run and assign the replacement to the first one so the
    paragraph still has a valid run element.
    """
    # ``python-docx`` exposes ``runs`` and ``text`` on Paragraph. We
    # type-narrow through getattr to keep mypy in --strict mode happy
    # without importing the internal type.
    runs = getattr(paragraph, "runs", None)
    if runs is None:
        # The attribute always exists for ``docx.text.paragraph.Paragraph``,
        # but the check keeps the function safe to call with mocks.
        raise AttributeError("paragraph object has no 'runs' attribute")  # pragma: no cover
    for run in runs:
        run.text = ""
    if runs:
        runs[0].text = new_text


__all__ = ["DocxConverter"]
