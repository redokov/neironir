"""Unit tests for :mod:`neironir.privacy.client.SubprocessPrivacyFilterClient`.

The client shells out to ``opf`` via ``asyncio.create_subprocess_exec``.
We monkey-patch ``asyncio.create_subprocess_exec`` to return a mock
process so the tests never require a real OPF binary.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, MagicMock

import pytest

from neironir.privacy.client import SubprocessPrivacyFilterClient


@pytest.fixture
def subject() -> SubprocessPrivacyFilterClient:
    """A client configured with a fake ``opf_cmd``."""
    return SubprocessPrivacyFilterClient(
        opf_cmd=["opf", "--format", "json"],
        device="cpu",
        timeout_s=30,
    )


def _make_mock_proc(
    stdout_bytes: bytes = b"",
    stderr_bytes: bytes = b"",
    returncode: int = 0,
) -> AsyncMock:
    """Create a mock ``asyncio.subprocess.Process``.

    The mock supports both ``communicate()`` (which the
    ``SubprocessPrivacyFilterClient`` uses) and ``pid`` / ``returncode``.
    """
    proc = AsyncMock(spec=asyncio.subprocess.Process)
    proc.returncode = returncode
    proc.pid = 12345

    # ``communicate()`` is the primary read path used by the client.
    async def _communicate() -> tuple[bytes, bytes]:
        return stdout_bytes, stderr_bytes
    proc.communicate = _communicate

    # Stub the pipe attributes (some code paths reference them directly).
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()

    return proc


class TestSubprocessAnnotate:
    async def test_annotate_returns_spans(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSON output from opf should be parsed into EntitySpan."""
        opf_output = json.dumps({
            "schema_version": 1,
            "text": "Call me at +7 495 123-45-67",
            "detected_spans": [
                {"start": 11, "end": 26, "label": "private_phone", "text": "+7 495 123-45-67"},
            ],
        }).encode("utf-8")
        mock_proc = _make_mock_proc(stdout_bytes=opf_output)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], device="cpu")
        result = await client.annotate("Call me at +7 495 123-45-67")

        assert len(result) == 1
        assert result[0].start == 11
        assert result[0].end == 26
        assert result[0].entity_type == "private_phone"

    async def test_annotate_empty_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSON with no spans should work (no PII detected)."""
        opf_output = json.dumps({"schema_version": 1, "text": "Hello world", "detected_spans": []}).encode("utf-8")
        mock_proc = _make_mock_proc(stdout_bytes=opf_output)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], device="cpu")
        result = await client.annotate("Hello world")

        assert len(result) == 0

    async def test_annotate_non_zero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero return code should raise PrivacyFilterError."""
        mock_proc = _make_mock_proc(
            stderr_bytes=b"opf: error: model not found",
            returncode=1,
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], device="cpu")
        # The client wraps the process error in PrivacyFilterError.
        with pytest.raises(Exception):
            await client.annotate("some text")

    async def test_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the subprocess hangs past the timeout, the client should
        raise an error (PrivacyFilterError or asyncio.TimeoutError)."""
        mock_proc = _make_mock_proc(stdout_bytes=b"", returncode=0)
        # Simulate a hang by making communicate never return.
        async def _never() -> tuple[bytes, bytes]:
            await asyncio.Event().wait()  # never resolves
            return b"", b""
        mock_proc.communicate = _never
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        # The client reads output in the event loop; with a short
        # timeout, it should eventually raise.
        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], device="cpu", timeout_s=0.001)
        with pytest.raises((asyncio.TimeoutError, Exception)):
            await client.annotate("test")

    async def test_invalid_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If opf outputs garbage (not valid JSON), an error should be raised."""
        mock_proc = _make_mock_proc(stdout_bytes=b"garbage output not json", returncode=0)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], device="cpu")
        with pytest.raises(Exception):
            await client.annotate("test")
