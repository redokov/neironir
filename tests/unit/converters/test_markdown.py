"""Tests for :class:`neironir.converters.markdown.MarkdownConverter`."""

from __future__ import annotations

from pathlib import Path

from neironir.converters.base import Replacement
from neironir.converters.markdown import MarkdownConverter
from neironir.domain.entity_type import EntityType


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_extract_returns_utf8_contents(tmp_path: Path) -> None:
    source = _write(tmp_path, "note.md", "Привет, мир!\n")
    converter = MarkdownConverter()
    assert converter.extract_text(source) == "Привет, мир!\n"


def test_build_replaces_text_in_source(tmp_path: Path) -> None:
    source = _write(tmp_path, "note.md", "Email: user@example.com")
    converter = MarkdownConverter()
    target = tmp_path / "out.md"

    # Layout:  0123456789012345678901234567
    #          "Email: user@example.com"
    #                 ^7            ^23
    replacement = Replacement(
        start=7,
        end=23,
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    converter.build(source, target, [replacement])

    assert target.read_text(encoding="utf-8") == "Email: <PRIVATE_EMAIL1>"


def test_build_handles_replacements_in_reverse_order(tmp_path: Path) -> None:
    """Passing replacements in descending order must still work."""
    source = _write(tmp_path, "note.md", "Call +7 495 123-45-67 or email a@b.io")
    converter = MarkdownConverter()
    target = tmp_path / "out.md"

    # Layout:  0         1         2         3
    #          0123456789012345678901234567890123456
    #          "Call +7 495 123-45-67 or email a@b.io"
    #                 ^5                ^21   ^31 ^37
    email = Replacement(
        start=31,
        end=37,
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    phone = Replacement(
        start=5,
        end=21,
        entity_type=EntityType.PRIVATE_PHONE,
        placeholder="<PRIVATE_PHONE1>",
    )
    # Intentionally pass in the wrong order.
    converter.build(source, target, [email, phone])

    result = target.read_text(encoding="utf-8")
    assert result == "Call <PRIVATE_PHONE1> or email <PRIVATE_EMAIL1>"


def test_build_preserves_unicode_around_replacements(tmp_path: Path) -> None:
    source = _write(tmp_path, "note.md", "Пишите на hello@example.com, пожалуйста.")
    converter = MarkdownConverter()
    target = tmp_path / "out.md"

    # Layout (one char per visible glyph):
    #          "Пишите на hello@example.com, пожалуйста."
    #                       ^10            ^27
    replacement = Replacement(
        start=10,
        end=27,
        entity_type=EntityType.PRIVATE_EMAIL,
        placeholder="<PRIVATE_EMAIL1>",
    )
    converter.build(source, target, [replacement])

    assert target.read_text(encoding="utf-8") == "Пишите на <PRIVATE_EMAIL1>, пожалуйста."


def test_build_with_no_replacements_copies_the_file(tmp_path: Path) -> None:
    source = _write(tmp_path, "note.md", "Nothing to redact here.")
    converter = MarkdownConverter()
    target = tmp_path / "out.md"

    converter.build(source, target, [])

    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
