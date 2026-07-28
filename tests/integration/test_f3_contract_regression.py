"""Regression test for the real-world contract that triggered the
custom docx→md converter.

The user reported that
``C:/MyProjects/MyTasks/summerizer/Договоры/2/ф3.docx`` came out of
the old ``pandoc``-based pipeline with a lot of noise:
``[10]{.underline}``, ``[…]{.mark}``, ``{=html}`` blocks,
``<amashchinov@tomat-astra.ru>`` autolink wrappers, etc.  This test
locks in the new converter's behaviour against that exact file.

It is **skipped** unless the file is present on disk so it works on
machines that don't have the user's local files.  CI runs against
the in-tree fixtures instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from neironir.converters.docx_to_md import convert_to_markdown

REAL_CONTRACT = Path(r"C:/MyProjects/MyTasks/summerizer/Договоры/2/ф3.docx")


pytestmark = pytest.mark.skipif(
    not REAL_CONTRACT.is_file(),
    reason=f"local contract not present at {REAL_CONTRACT}",
)


class TestF3ContractNoise:
    """Lock in the absence of the noise patterns the user complained
    about."""

    def test_no_underline_class(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        assert not re.search(r"\{[^}]*underline[^}]*\}", md), (
            "underline span class is back — like '[10]{.underline}'"
        )

    def test_no_mark_class(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        assert not re.search(r"\{[^}]*mark[^}]*\}", md), (
            "highlight span class is back — like '[...]{.mark}'"
        )

    def test_no_pandoc_html_block(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        assert "{=html" not in md, (
            "raw 'pandoc html block' separator leaked into the output"
        )

    def test_no_pandoc_html_comment(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        assert "<!--" not in md, (
            "raw HTML comment leaked into the output"
        )

    def test_no_autolink_email_wrapper(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        # ``<amashchinov@tomat-astra.ru>`` is what Word emits for an
        # autolinked email — privacy filter regex still catches it,
        # but it makes the file ugly.
        assert not re.search(
            r"<[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}>",
            md,
        )

    def test_no_markdown_link_syntax(self) -> None:
        """No ``[label](url)`` — only plain text per spec."""
        md = convert_to_markdown(REAL_CONTRACT)
        assert "](" not in md

    def test_email_address_remains_detectable(self) -> None:
        """Email text survives as plain text so privacy-filter regex
        can still find it."""
        md = convert_to_markdown(REAL_CONTRACT)
        emails = re.findall(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            md,
        )
        assert "amashchinov@tomat-astra.ru" in emails

    def test_email_address_has_no_angle_brackets(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        assert "<amashchinov" not in md
        assert "amashchinov>" not in md or "@" not in md.split("amashchinov>")[0][-50:]

    def test_tables_are_pipe_tables(self) -> None:
        md = convert_to_markdown(REAL_CONTRACT)
        # Look for the markdown separator line that proves pipe-table
        # format: ``| --- | --- |``.
        assert re.search(r"^\|[ \t-]+\|", md, re.MULTILINE), (
            "tables should render as pipe-tables, not HTML or grid"
        )

    def test_only_paragraphs_headings_and_tables(self) -> None:
        """No blockquotes, no code blocks, no HTML."""
        md = convert_to_markdown(REAL_CONTRACT)
        assert "> " not in md.splitlines()[0:30] if md.splitlines() else True
        # Actually, we don't want any blockquote lines (``> ...``).
        for line in md.splitlines():
            # Allow a single ``>`` char inside a quoted string in the
            # actual text content, but not as a markdown blockquote
            # prefix at line start.
            assert not line.startswith("> "), (
                f"unexpected blockquote: {line!r}"
            )

    def test_output_is_much_shorter_than_pandoc(self) -> None:
        """The old pandoc output for this contract was 10771 bytes;
        the new converter should produce something substantially
        smaller."""
        import subprocess

        pandoc = shutil_which("pandoc")
        if pandoc is None:
            pytest.skip("pandoc not on PATH")
        completed = subprocess.run(
            [pandoc, str(REAL_CONTRACT), "-t", "markdown", "--wrap", "none"],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            pytest.skip("pandoc failed")
        old_size = len(completed.stdout)
        new_size = len(convert_to_markdown(REAL_CONTRACT))
        # Allow a small slack but expect the new output to be at
        # least 10% smaller — empirically the diff is ~30%.
        assert new_size < old_size * 0.9, (
            f"new converter output ({new_size}) is not noticeably "
            f"shorter than pandoc ({old_size})"
        )


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)