"""E2E tests for subprocess-to-mock fallback and runtime settings API.

These tests spin up a real uvicorn instance and verify that:

1. The runtime settings API (GET/PUT /api/v1/admin/settings) works.
2. When a job times out against a very slow subprocess command, the
   pipeline falls back to mock mode and sets ``processing_note``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def live_server() -> Generator[str, None, None]:
    """Start a fresh uvicorn instance with mock-friendly config."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["NEIRONIR_SESSION_SECRET"] = "test-secret-for-e2e"
    env["NEIRONIR_ADMIN_PASSWORD"] = "test-pass"
    env["NEIRONIR_PRIVACY_FILTER_MODE"] = "combined"
    # Use a command that hangs forever so the fallback kicks in.
    if sys.platform == "win32":
        env["NEIRONIR_PRIVACY_FILTER_CMD"] = "ping -n 300 127.0.0.1"
    else:
        env["NEIRONIR_PRIVACY_FILTER_CMD"] = "sleep 300"
    env["NEIRONIR_PRIVACY_FILTER_TIMEOUT"] = "3"  # very short timeout
    env["NEIRONIR_STORAGE_DIR"] = str(REPO_ROOT / "storage_e2e_fallback")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "neironir.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for the server to become ready.
    for _ in range(30):
        try:
            r = httpx.get(f"{url}/api/v1/health", timeout=2)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(0.5)
    else:
        proc.kill()
        proc.wait()
        pytest.fail("Server did not start in time")

    yield url

    proc.kill()
    proc.wait()
    import shutil

    shutil.rmtree(REPO_ROOT / "storage_e2e_fallback", ignore_errors=True)


class TestRuntimeSettingsEndpoint:
    """Admin runtime settings API integration tests."""

    def test_get_settings_returns_default(self, live_server: str) -> None:
        # Admin endpoints require a session — log in first.
        with httpx.Client(base_url=live_server, timeout=5) as client:
            login = client.post(
                "/login",
                data={"username": "admin", "password": "test-pass"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            r = client.get("/api/v1/admin/settings")
            assert r.status_code == 200
            data = r.json()
            assert "privacy_filter_timeout" in data
            # The env override sets it to 3.
            assert data["privacy_filter_timeout"] == 3

    def test_auth_required(self, live_server: str) -> None:
        """Without admin session, GET /api/v1/admin/settings should 401."""
        r = httpx.get(f"{live_server}/api/v1/admin/settings", timeout=5)
        assert r.status_code == 401


class TestSubprocessFallback:
    """The subprocess should time out quickly (3s) and fall back to mock."""

    def test_job_completes_after_fallback(self, live_server: str) -> None:
        """Upload a file, wait for completion, check the job has a
        processing_note about the mock fallback."""
        r = httpx.post(
            f"{live_server}/api/v1/documents/",
            files={"file": ("test.md", b"user@example.com", "text/markdown")},
            timeout=30,
        )
        assert r.status_code == 202
        job_id = r.json()["id"]

        # Poll for completion (should be fast since mock is fast).
        for _ in range(60):
            r = httpx.get(f"{live_server}/api/v1/documents/{job_id}", timeout=5)
            job = r.json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(1)
        else:
            pytest.fail("Job did not complete within 60s")

        assert job["status"] == "completed", (
            f"expected completed, got {job['status']}: {job.get('error')}"
        )
        assert job.get("processing_note"), (
            "job should have a processing_note after subprocess fallback"
        )
        # The note should mention mock or simplified mode.
        note = job["processing_note"].lower()
        assert "mock" in note or "упрощён" in note, (
            f"processing_note should mention mock fallback, got: {job['processing_note']}"
        )
        # The result should still contain the mock-discovered placeholder.
        r = httpx.get(f"{live_server}/api/v1/documents/{job_id}/download", timeout=5)
        assert r.status_code == 200
        assert "<PRIVATE_EMAIL1>" in r.text, (
            "mock-detected email placeholder should be in the result"
        )
