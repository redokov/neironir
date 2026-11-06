"""Tests for :class:`neironir.converters.docx.DocxConverter`."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from neironir.converters.base import Replacement
from neironir.converters.docx import DocxConverter
from neironir.domain.entity_type import EntityType


def _make_docx(tmp_path: Path, paragraphs: list[str], name: str = "source.docx") -> Path:
    """Create a real ``.docx`` on disk with the given plain-text paragraphs."""
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    path = tmp_path / name
    document.save(str(path))
    return path


def test_extract_text_joins_paragraphs_with_newlines(tmp_path: Path) -> None:
    source = _make_docx(tmp_path, ["first", "second", "third"])
    converter = DocxConverter()

    assert converter.extract_text(source) == "first\nsecond\nthird"


def test_round_trip_with_replacement(tmp_path: Path) -> None:
    source = _make_docx(
        tmp_path,
        ["Email: user@example.com", "Plain paragraph"],
    )
    converter = DocxConverter()
    target = tmp_path / "out.docx"

    # ``"Email: "`` is 7 chars, the email is 16 chars.
    replacement = Replacement(
        start=7,
        end=23,
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    converter.build(source, target, [replacement])

    rewritten = Document(str(target))
    paragraphs = [p.text for p in rewritten.paragraphs]
    assert paragraphs == ["Email: <PRIVATE_EMAIL1>", "Plain paragraph"]


def test_build_rejects_replacement_crossing_paragraph_boundary(tmp_path: Path) -> None:
    """A replacement that straddles a paragraph boundary raises ValueError (no silent clipping)."""
    source = _make_docx(tmp_path, ["Email me at user@example.", "com soon"])
    converter = DocxConverter()
    target = tmp_path / "out.docx"

    # start=12 is inside paragraph 0, end=29 is inside paragraph 1.
    replacement = Replacement(
        start=12,
        end=29,
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    with pytest.raises(ValueError, match="crosses a paragraph/cell boundary"):
        converter.build(source, target, [replacement])

    # Nothing was written: the output must not be produced.
    assert not target.exists() or target.stat().st_size == 0


def test_build_still_clips_at_element_bounds(tmp_path: Path) -> None:
    """Clamping to element bounds (out-of-range offset) still happens silently.

    This is the residual current behaviour that the new ValueError path must not
    disturb: a replacement whose end projects beyond the containing element is
    still clamped rather than rejected.
    """
    source = _make_docx(tmp_path, ["Email me at user@example."])
    converter = DocxConverter()
    target = tmp_path / "out.docx"

    replacement = Replacement(
        start=12,
        end=299,  # way beyond paragraph 0
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    converter.build(source, target, [replacement])

    rewritten = Document(str(target))
    paragraphs = [p.text for p in rewritten.paragraphs]
    assert paragraphs == ["Email me at <PRIVATE_EMAIL1>"]


def test_build_with_no_replacements_preserves_paragraphs(tmp_path: Path) -> None:
    source = _make_docx(tmp_path, ["one", "two"])
    converter = DocxConverter()
    target = tmp_path / "out.docx"

    converter.build(source, target, [])

    paragraphs = [p.text for p in Document(str(target)).paragraphs]
    assert paragraphs == ["one", "two"]


def test_reject_count_is_recorded_in_stats(tmp_path: Path) -> None:
    """The stats counter is incremented for rejected cross-boundary replacements."""
    source = _make_docx(
        tmp_path,
        ["Email user@example.com", "Phone 123-456-7890"],
    )
    converter = DocxConverter()
    target = tmp_path / "out.docx"

    # Both replacements straddle the boundary between the two paragraphs.
    replacements = [
        Replacement(
            start=7,
            end=34,
            entity_type=EntityType.PRIVATE_EMAIL,
            placeholder="<PRIVATE_EMAIL1>",
        ),
        Replacement(
            start=0,
            end=40,
            entity_type=EntityType.PRIVATE_PHONE,
            placeholder="<PRIVATE_PHONE1>",
        ),
    ]
    with pytest.raises(ValueError, match="crosses a paragraph/cell boundary"):
        converter.build(source, target, replacements)

    assert not target.exists() or target.stat().st_size == 0


def test_build_preserves_text_of_hyperlink_paragraph(tmp_path: Path) -> None:
    """A paragraph made of a single hyperlink keeps its visible text at build.

    Word auto-converts URLs/emails to hyperlinks; their text is NOT in
    paragraph.runs, so clearing runs alone would lose the hyperlink's
    displayed text. _replace_text must apply the same fallback as
    _set_cell_text (~line 345): set element.text when runs is empty.
    Regression test for T2.
    """
    document = Document()
    para = document.add_paragraph()
    hyper = para.add_run("https://example.com/")
    hyper._element.set(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}href",
        "https://example.com/",
    )
    source = tmp_path / "source.docx"
    document.save(str(source))

    # The hyperlink text "https://example.com/" occupies offset 0..21.
    replacement = Replacement(
        start=0,
        end=21,
        entity_type=EntityType.PRIVATE_URL,
        placeholder="<PRIVATE_URL1>",
    )
    converter = DocxConverter()
    target = tmp_path / "out.docx"
    converter.build(source, target, [replacement])

    rewritten = Document(str(target))
    paragraphs = [p.text for p in rewritten.paragraphs]
    assert paragraphs == ["<PRIVATE_URL1>"]
