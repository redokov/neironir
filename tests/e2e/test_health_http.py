"""E2E tests for Phase 0: spin up a real uvicorn process and hit it over HTTP.

This is the strongest check we can do at this phase: the app must not just
import cleanly, it must actually serve an HTTP request and respond with the
documented payload.
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


def _wait_ready(url: str, timeout: float = 10.0) -> None:
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
def server_url() -> Generator[str, None, None]:
    port = _free_port()
    env = {**os.environ, "NEIRONIR_LOG_LEVEL": "WARNING"}
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
        _wait_ready(f"{base}/api/v1/health", timeout=10.0)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_health_endpoint_returns_200_and_ok_payload(server_url: str) -> None:
    response = httpx.get(f"{server_url}/api/v1/health", timeout=2.0)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_endpoint_returns_404(server_url: str) -> None:
    response = httpx.get(f"{server_url}/api/v1/does-not-exist", timeout=2.0)
    assert response.status_code == 404
