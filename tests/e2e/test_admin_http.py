"""E2E test: spin up a real uvicorn process and exercise the admin API.

This is the strongest check we can do for the admin feature: the app
must not just import cleanly, it must actually serve HTTP requests for
the admin dashboard endpoints.
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

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.2)
    raise AssertionError(f"server not ready at {url}: {last_err!r}")


@pytest.fixture(scope="module")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """Start uvicorn with a private storage dir and yield the base URL."""
    storage = tmp_path_factory.mktemp("admin_e2e_storage")
    port = _free_port()
    env = {
        **os.environ,
        "NEIRONIR_LOG_LEVEL": "WARNING",
        "NEIRONIR_STORAGE_DIR": str(storage),
        "NEIRONIR_PRIVACY_FILTER_MODE": "mock",
    }
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "neironir.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(f"{base}/api/v1/health", timeout=15.0)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_admin_stats_endpoint(server_url: str) -> None:
    r = httpx.get(f"{server_url}/api/v1/admin/stats", timeout=2.0)
    assert r.status_code == 200
    body = r.json()
    assert "total_jobs" in body
    assert "by_day" in body
    assert isinstance(body["by_day"], dict)


def test_admin_documents_endpoint(server_url: str) -> None:
    r = httpx.get(f"{server_url}/api/v1/admin/documents", timeout=2.0)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_training_status_endpoint(server_url: str) -> None:
    r = httpx.get(f"{server_url}/api/v1/admin/training/status", timeout=2.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "idle"
    assert body["pid"] is None
    assert body["progress"]["epoch"] == 0


def test_admin_training_stop_when_idle(server_url: str) -> None:
    r = httpx.post(f"{server_url}/api/v1/admin/training/stop", timeout=2.0)
    assert r.status_code == 200
    body = r.json()
    assert body["signal_sent"] is False


def test_admin_html_served(server_url: str) -> None:
    r = httpx.get(f"{server_url}/admin", timeout=2.0)
    assert r.status_code == 200
    assert "Админка" in r.text


def test_admin_static_js_served(server_url: str) -> None:
    r = httpx.get(f"{server_url}/static/admin.js", timeout=2.0)
    assert r.status_code == 200
    assert "loadStats" in r.text