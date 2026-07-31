"""Unit tests for :mod:`neironir.config` helpers."""

from __future__ import annotations

from neironir.config import parse_opf_cmd


class TestParseOpfCmd:
    def test_empty_string_returns_default(self) -> None:
        assert parse_opf_cmd("") == ["opf"]
        assert parse_opf_cmd("   ") == ["opf"]

    def test_bare_executable(self) -> None:
        assert parse_opf_cmd("opf") == ["opf"]

    def test_windows_path_with_backslashes(self) -> None:
        """The executable path must survive tokenisation intact — a naive
        ``shlex.split`` would eat the backslashes."""
        assert parse_opf_cmd(".venv-opf/Scripts/opf.exe") == [".venv-opf/Scripts/opf.exe"]
        assert parse_opf_cmd(r"C:\tools\opf\opf.exe") == [r"C:\tools\opf\opf.exe"]

    def test_executable_with_simple_args(self) -> None:
        assert parse_opf_cmd("opf --device cpu --n-ctx 512") == [
            "opf",
            "--device",
            "cpu",
            "--n-ctx",
            "512",
        ]

    def test_quoted_argument_is_honoured(self) -> None:
        assert parse_opf_cmd('opf --checkpoint "my dir/ckpt"') == [
            "opf",
            "--checkpoint",
            "my dir/ckpt",
        ]

    def test_quoted_executable_with_spaces(self) -> None:
        assert parse_opf_cmd(r'"C:\Program Files\opf\opf.exe" --device cpu') == [
            r"C:\Program Files\opf\opf.exe",
            "--device",
            "cpu",
        ]

    def test_unbalanced_quotes_fall_back_to_naive_split(self) -> None:
        # ``shlex.split`` raises ValueError on unbalanced quotes; the
        # fallback must still produce a usable command line.
        assert parse_opf_cmd('opf --name "unclosed') == ["opf", "--name", '"unclosed']
