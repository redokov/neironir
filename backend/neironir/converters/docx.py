"""DOCX converter for the redaction pipeline.

Extracts text from **both** body paragraphs and tables, applying the
privacy-filter detection and replacement to the concatenated text.

Key design
----------

The document body is a sequence of XML elements — paragraphs (``w:p``)
and tables (``w:tbl``) — in reading order. This converter:

1. Iterates the body elements in document order.
2. Flattens each table into a sequence of individual cell texts (treated
   analogously to paragraphs for offset tracking).
3. Concatenates all texts with ``\\n`` separators for the privacy model.
4. During :meth:`build`, projects each :class:`Replacement` back to the
   originating paragraph or table cell.

Known limitations (inherited from the MVP)
------------------------------------------

* Inline formatting (bold, italic, links) is lost — all runs in a paragraph
  are replaced with a single plain-text run.
* Nested tables are not supported.
* A replacement that crosses a paragraph *or* table-cell boundary raises
  :class:`ValueError`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph

from neironir.converters.base import Replacement

logger = logging.getLogger(__name__)

# Separator inserted between every pair of body-element texts (paragraphs
# and table cells alike). The privacy-filter sees one continuous string
# with ``\\n`` separators.
_SEPARATOR = "\n"


class DocxConverter:
    """Read/write DOCX files, handling both paragraphs and tables.

    Handles nested tables: tables inside table cells are traversed
    recursively so that PII in nested cells is both detected (extract_text)
    and sanitized (build).
    """

    ext = "docx"

    # Internal element type constants
    _P = "p"  # paragraph
    _T = "t"  # table
    _TC = "c"  # table cell (flattened)

    def extract_text(self, source: Path) -> str:
        """Return the concatenated plain text of paragraphs and table cells.

        The result includes a ``\\n`` separator between every pair of
        body elements (paragraph↔paragraph, paragraph↔table-cell, etc.)
        so that offsets are stable and predictable.

        For the ``docx → md`` conversion the pipeline uses
        :func:`neironir.workers.pipeline._docx_to_markdown` which
        delegates to ``pandoc`` for a richer markdown rendering; this
        method remains the source-of-truth offset representation that
        :meth:`build` projects replacements onto.
        """
        document = Document(str(source))
        # Cache for :meth:`build` (legacy; the element map now references
        # objects directly).
        self._doc_paragraphs = list(document.paragraphs)
        self._doc_tables = list(document.tables)
        self._element_map = _build_element_map(document)
        self._texts = _element_texts(self._element_map)
        return _SEPARATOR.join(self._texts)

    def build(self, source: Path, target: Path, replacements: list[Replacement]) -> None:
        """Re-write ``source`` with replacements applied, saving to ``target``.

        Replacements are anchored in the concatenated text produced by an
        earlier call to :meth:`extract_text`. Each replacement is projected
        onto the originating paragraph or table cell. Cross-boundary
        replacements are rejected.
        """
        document: DocumentType = Document(str(source))
        element_map = _build_element_map(document)
        texts = _element_texts(element_map)
        cumulative = _cumulative_offsets(texts)

        stats: dict[str, int] = {"clipped": 0, "rejected_cross_boundary": 0}
        rewritten = _slice_elements(
            texts=texts,
            cumulative=cumulative,
            replacements=replacements,
            element_map=element_map,
            stats=stats,
        )

        # Apply rewritten texts back to paragraphs and table cells.
        _apply_rewrites(element_map, rewritten)

        document.save(str(target))


# ---------------------------------------------------------------------------
# Body element iteration
# ---------------------------------------------------------------------------


def _build_element_map(
    document: DocumentType,
) -> list[tuple[str, BaseOxmlElement]]:
    """Produce a flat sequence of body elements in document order.

    Each entry is ``(type, xml_element)`` where ``type`` is ``_P`` (paragraph,
    ``w:p``). Tables nested inside cells are visited recursively so that the
    paragraphs of nested tables appear in document order alongside root-level
    elements. Storing the XML element directly (instead of the wrapped
    ``Paragraph`` object) avoids fragile reconstruction of objects when walking
    the underlying tree.
    """
    elements: list[tuple[str, BaseOxmlElement]] = []
    _collect_cells(elements, document.element.body)
    return elements


def _collect_cells(
    elements: list[tuple[str, BaseOxmlElement]],
    body: BaseOxmlElement,
) -> None:
    """Recursively walk body, tables and cells in document order.

    Walk ``w:p`` paragraphs and ``w:tbl`` tables. When a table is found, its
    cells are descended into so that the paragraphs they contain are added in
    order — this is how nested tables are handled: paragraphs inside nested
    tables are interleaved with the surrounding text.
    """
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            elements.append((DocxConverter._P, child))
        elif tag == qn("w:tbl"):
            for tr in child.findall(qn("w:tr")):
                for tc in tr.findall(qn("w:tc")):
                    # Descend inside the cell so its paragraphs are handled.
                    _collect_cells(elements, tc)


def _element_texts(element_map: list[tuple[str, BaseOxmlElement]]) -> list[str]:
    """Extract text from each element in the map order.

    The map contains only ``w:p`` elements (root paragraphs and the paragraphs
    of nested table cells), so the text getter is uniform for every entry.
    """
    texts: list[str] = []
    for _elem_type, obj in element_map:
        texts.append(obj.text or "")
    return texts



# ---------------------------------------------------------------------------
# Offset computation
# ---------------------------------------------------------------------------


def _cumulative_offsets(texts: list[str]) -> list[int]:
    """Return the start offset of each element in the joined text.

    ``cumulative[i]`` is the index of ``texts[i][0]`` in the concatenated
    string. The list has one extra entry at the end — the total length —
    for simpler boundary checks.
    """
    offsets: list[int] = []
    cursor = 0
    for text in texts:
        offsets.append(cursor)
        cursor += len(text) + len(_SEPARATOR)
    offsets.append(cursor)
    return offsets


# ---------------------------------------------------------------------------
# Replacement application
# ---------------------------------------------------------------------------


def _find_element_index(cumulative: list[int], offset: int) -> int:
    """Return the index into ``texts`` that contains ``offset``."""
    for i in range(len(cumulative) - 2, -1, -1):
        if offset >= cumulative[i]:
            return i
    raise ValueError(f"offset {offset} is before the first element")


def _slice_elements(
    texts: list[str],
    cumulative: list[int],
    replacements: list[Replacement],
    element_map: list[tuple[str, BaseOxmlElement]],
    *,
    stats: dict[str, int],
) -> list[str]:
    """Apply replacements and return the rewritten text list.

    Each replacement is expected to fit **inside a single body element
    (paragraph or table cell)**. A replacement whose ``start`` and ``end``
    project to different elements — i.e. it crosses a paragraph / cell
    boundary — raises :class:`ValueError`, as documented in
    :meth:`DocxConverter.build`.
    """
    rewritten = list(texts)
    ordered = sorted(replacements, key=lambda r: r.start, reverse=True)

    for replacement in ordered:
        start_idx = _find_element_index(cumulative, replacement.start)
        end_idx = _find_element_index(cumulative, replacement.end - 1)

        if start_idx == end_idx:
            # Normal case — fits inside one element.
            local_start = replacement.start - cumulative[start_idx]
            local_end = replacement.end - cumulative[start_idx]
            element_text = rewritten[start_idx]
            # Clamp to element bounds — if the offset is out of range
            # (e.g. due to a corrupted annotation), we silently clip
            # rather than crash. The log warning below flags this.
            if local_start < 0 or local_end > len(element_text):
                stats["clipped"] += 1
                local_start = max(0, local_start)
                local_end = min(len(element_text), local_end)
                if stats["clipped"] <= 3:
                    logger.warning(
                        "replacement %s clipped: start=%d end=%d elem_len=%d local=%d..%d",
                        replacement.entity_type,
                        replacement.start,
                        replacement.end,
                        len(element_text),
                        local_start,
                        local_end,
                    )
            # Allow both zero-length (insert) and positive-length
            # (replace) replacements.
            rewritten[start_idx] = (
                element_text[:local_start] + replacement.placeholder + element_text[local_end:]
            )
        else:
            # Cross-boundary span — the replacement is split across two
            # elements; silently clipping would lose part of the data and
            # leave the document in an inconsistent state. Raise instead.
            stats["rejected_cross_boundary"] += 1
            raise ValueError(
                f"{replacement.entity_type} replacement {replacement.start}..{replacement.end} "
                f"crosses a paragraph/cell boundary "
                f"(start element {start_idx}, end element {end_idx})"
            )

    return rewritten


def _apply_rewrites(
    element_map: list[tuple[str, BaseOxmlElement]],
    rewritten: list[str],
) -> None:
    """Write rewritten texts back to the ``w:p`` XML elements.

    Each map entry directly references an XML element, so no index lookup is
    needed. This also makes the rewrite path consistent with
    ``_element_texts`` and with nested-table walks.
    """
    for (_elem_type, obj), new_text in zip(element_map, rewritten, strict=True):
        _replace_para_xml(obj, new_text)


def _replace_text(element: Paragraph, new_text: str) -> None:
    """Replace the element's text, preserving no other formatting.

    Clears all runs and assigns the replacement to the first run so the
    paragraph/cell still has a valid run element.

    Also removes hyperlink XML elements (``w:hyperlink``) which are not
    accessible via ``paragraph.runs`` — Word auto-converts email addresses
    and URLs to hyperlinks, and their text survives ``runs`` clearing.
    """
    from docx.oxml.ns import qn

    runs = getattr(element, "runs", None)
    if runs is None:
        raise AttributeError("element has no 'runs' attribute")

    # 1. Clear all normal runs
    for run in runs:
        run.text = ""

    # 2. Remove hyperlink elements (w:hyperlink) from the paragraph XML.
    #    These contain their own runs and are NOT included in paragraph.runs.
    p_element = getattr(element, "_element", None)
    if p_element is not None:
        hyperlink_tag = qn("w:hyperlink")
        hyperlinks = p_element.findall(hyperlink_tag)
        for hl in hyperlinks:
            p_element.remove(hl)

    # 3. Set the new text in the first run, with a fallback for paragraphs
    #    that have no normal runs at all (e.g. a paragraph made of a single
    #    w:hyperlink — Word auto-converts URLs/emails to hyperlinks).
    #    See _set_cell_text (~line 345) for the matching fallback pattern.
    if runs:
        runs[0].text = new_text
    else:
        element.text = new_text


def _replace_para_xml(element: BaseOxmlElement, new_text: str) -> None:
    """Replace the text of a ``w:p`` XML element (used by the nested-table
    rewrite path).

    Removes hyperlink elements (``w:hyperlink``), clears all runs, and writes
    the replacement into a single new run so the paragraph has a valid run.
    This mirrors :func:`_replace_text` but operates on XML elements directly
    (``_build_element_map`` stores ``w:p``/``w:tc`` elements, not wrapped
    ``Paragraph``/``Cell`` objects).
    """
    from docx.oxml.ns import qn

    # Remove hyperlink wrappers and their contents.
    for hl in element.findall(qn("w:hyperlink")):
        element.remove(hl)

    # Clear existing runs (keeping w:pPr if present) and write the replacement.
    element.clear_content()
    r = element.add_r()
    r.text = new_text


def _set_cell_text(table: Table, cell_idx: int | None, new_text: str) -> None:
    """Set the text of a cell identified by its **global** cell index.

    The index is the sequential position across *all* rows of the table,
    matching the ``cell_index`` stored by :func:`_build_element_map`.

    Handles cells with multiple paragraphs by clearing all existing
    paragraph text and writing the full ``new_text`` into the first
    paragraph.  Multi-line content (``\n``) is preserved inside the
    single paragraph.  Hyperlinks are also removed from all paragraphs.
    """
    from docx.oxml.ns import qn

    hyperlink_tag = qn("w:hyperlink")

    count = 0
    for row in table.rows:
        for cell in row.cells:
            if cell_idx is not None and count == cell_idx:
                # Clear ALL paragraphs in the cell
                for para in cell.paragraphs:
                    runs = getattr(para, "runs", None)
                    if runs is not None:
                        for run in runs:
                            run.text = ""
                    # Remove hyperlinks from each paragraph
                    p_element = getattr(para, "_element", None)
                    if p_element is not None:
                        for hl in p_element.findall(hyperlink_tag):
                            p_element.remove(hl)
                # Write the full text to the first paragraph
                if cell.paragraphs:
                    first_para = cell.paragraphs[0]
                    runs = getattr(first_para, "runs", None)
                    if runs:
                        runs[0].text = new_text
                    else:
                        first_para.text = new_text
                return
            count += 1
    logger.warning("table cell index %s not found (table has %d cells)", cell_idx, count)


__all__ = ["DocxConverter"]
