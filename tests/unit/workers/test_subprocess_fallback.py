"""Unit tests for the subprocess-to-mock fallback in :mod:`neironir.workers.pipeline`.

When ``privacy.annotate()`` raises a subprocess-related exception
(NotImplementedError, FileNotFoundError, PrivacyFilterError with
timeout), ``run_job`` should catch it, set ``job.processing_note``,
and re-run with :class:`MockPrivacyFilterClient`.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from neironir.domain.job import Job, JobStatus
from neironir.privacy.client import MockPrivacyFilterClient, PrivacyFilterError
from neironir.storage.local import LocalStorage


class TestSubprocessFallback:
    """Verify that pipeline falls back to mock when subprocess fails."""

    @pytest.mark.asyncio
    async def test_fallback_sets_processing_note(self, tmp_path: Path) -> None:
        """After a successful mock fallback, ``job.processing_note`` must
        contain a non-empty user-facing message about the fallback."""
        from neironir.workers.pipeline import run_job

        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            source_filename="notes.md",
            source_ext="md",
        )
        storage.save_job(job)
        storage.save_source(job_id, "notes.md", b"user@example.com")

        # Use a mock privacy client that fails with NotImplementedError.
        class FailingMock(MockPrivacyFilterClient):
            async def annotate(self, text: str) -> list:
                msg = "simulated subprocess failure"
                raise NotImplementedError(msg)

        from neironir.config import Settings

        settings = Settings(session_secret="test", admin_password="test")

        await run_job(
            job_id,
            settings=settings,
            storage=storage,
            privacy=FailingMock(),
        )

        loaded = storage.load_job(job_id)
        assert loaded.status == JobStatus.COMPLETED, (
            f"expected COMPLETED after fallback, got {loaded.status}"
        )
        assert loaded.processing_note is not None, "processing_note should be set after fallback"
        assert len(loaded.processing_note) > 20, (
            "processing_note should contain a meaningful message"
        )
        # The note should mention the fallback in Russian.
        assert "mock" in loaded.processing_note.lower() or "упрощён" in loaded.processing_note, (
            f"processing_note should mention mock fallback, got: {loaded.processing_note}"
        )

    @pytest.mark.asyncio
    async def test_fallback_on_file_not_found(self, tmp_path: Path) -> None:
        """``FileNotFoundError`` from subprocess should trigger the same
        fallback and produce a COMPLETED job with a processing note."""
        from neironir.workers.pipeline import run_job

        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            source_filename="notes.md",
            source_ext="md",
        )
        storage.save_job(job)
        storage.save_source(job_id, "notes.md", b"email@test.com")

        class FileNotFoundMock(MockPrivacyFilterClient):
            async def annotate(self, text: str) -> list:
                raise FileNotFoundError("opf.exe not found")

        from neironir.config import Settings as Cfg

        settings = Cfg(
            session_secret="test",
            admin_password="test",
        )
        await run_job(
            job_id,
            settings=settings,
            storage=storage,
            privacy=FileNotFoundMock(),
        )

        loaded = storage.load_job(job_id)
        assert loaded.status == JobStatus.COMPLETED
        assert loaded.processing_note is not None

    @pytest.mark.asyncio
    async def test_fallback_on_timeout(self, tmp_path: Path) -> None:
        """``PrivacyFilterError`` (e.g. opf timeout) should trigger
        fallback to mock and complete with a note."""
        from neironir.workers.pipeline import run_job

        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            source_filename="notes.md",
            source_ext="md",
        )
        storage.save_job(job)
        storage.save_source(job_id, "notes.md", b"user@domain.com")

        class TimeoutMock(MockPrivacyFilterClient):
            async def annotate(self, text: str) -> list:
                raise PrivacyFilterError("opf timeout after 120.0s")

        from neironir.config import Settings as Cfg

        settings = Cfg(
            session_secret="test",
            admin_password="test",
        )
        await run_job(
            job_id,
            settings=settings,
            storage=storage,
            privacy=TimeoutMock(),
        )

        loaded = storage.load_job(job_id)
        assert loaded.status == JobStatus.COMPLETED
        assert loaded.processing_note is not None

    @pytest.mark.asyncio
    async def test_fallback_result_contains_mock_annotations(self, tmp_path: Path) -> None:
        """After fallback to mock, the result file should contain
        mock-detected placeholders (e.g. ``<PRIVATE_EMAIL1>``)."""
        import json

        from neironir.workers.pipeline import run_job

        storage = LocalStorage(tmp_path)
        job_id = uuid4()
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            source_filename="notes.md",
            source_ext="md",
        )
        storage.save_job(job)
        storage.save_source(job_id, "notes.md", b"user@example.com")

        class FailingMock(MockPrivacyFilterClient):
            async def annotate(self, text: str) -> list:
                raise PrivacyFilterError("opf timeout")

        from neironir.config import Settings as Cfg

        settings = Cfg(
            session_secret="test",
            admin_password="test",
        )
        await run_job(
            job_id,
            settings=settings,
            storage=storage,
            privacy=FailingMock(),
        )

        # Check the annotations file was written with mock results.
        ann_path = storage.job_dir(job_id) / "annotations.json"
        assert ann_path.is_file()
        annotations = json.loads(ann_path.read_text(encoding="utf-8"))
        assert len(annotations) > 0, "should have at least one annotation"
        # Check the result file exists and has the placeholder.
        result_path = storage.job_dir(job_id) / "result.md"
        assert result_path.is_file()
        content = result_path.read_text(encoding="utf-8")
        assert "<PRIVATE_EMAIL1>" in content


class TestRuntimeSettings:
    """Tests for the runtime settings API (timeout override)."""

    def test_default_timeout_from_env(self) -> None:
        """Without runtime settings file, uses the env default (600)."""
        from neironir.config import Settings

        s = Settings()
        assert s.privacy_filter_timeout == 600

    def test_save_and_load_runtime_timeout(self, tmp_path: Path) -> None:
        """Saving a timeout via the admin API persists it and it loads correctly."""
        from neironir.admin.router import _load_runtime_timeout, _save_runtime_timeout

        _save_runtime_timeout(tmp_path, 300)
        loaded = _load_runtime_timeout(tmp_path)
        assert loaded == 300

    def test_runtime_timeout_used_by_subprocess_client(self, tmp_path: Path) -> None:
        """When runtime_settings.json exists, _build_subprocess_client
        uses the override timeout instead of the env default."""
        from neironir.admin.router import _save_runtime_timeout
        from neironir.api.dependencies import _build_subprocess_client
        from neironir.config import Settings as Cfg

        _save_runtime_timeout(tmp_path, 42)
        settings = Cfg(
            session_secret="test",
            admin_password="test",
            storage_dir=str(tmp_path),
            privacy_filter_cmd="opf",
            privacy_filter_timeout=600,
        )
        client = _build_subprocess_client(settings)
        assert client.timeout_s == 42.0, f"expected timeout 42.0, got {client.timeout_s}"

    def test_runtime_timeout_saves_and_loads_roundtrip(self, tmp_path: Path) -> None:
        """Round-trip: save, load, verify."""
        import json

        from neironir.admin.router import (
            _RUNTIME_SETTINGS_FILE,
            _load_runtime_timeout,
            _save_runtime_timeout,
        )

        _save_runtime_timeout(tmp_path, 999)
        path = tmp_path / _RUNTIME_SETTINGS_FILE
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["privacy_filter_timeout"] == 999
        assert _load_runtime_timeout(tmp_path) == 999
