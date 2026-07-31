"""E2E tests for the output-format checkbox and apply-feedback endpoint.

Spins up a real uvicorn process and exercises the new HTTP surface
through the full network stack.
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


def _build_docx_bytes(path: Path, paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))
    return path.read_bytes()


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


def _upload(
    server_url: str,
    content: bytes,
    filename: str,
    content_type: str,
    data: dict | None = None,
) -> httpx.Response:
    return httpx.post(
        f"{server_url}/api/v1/documents/",
        files={"file": (filename, content, content_type)},
        data=data or {},
        timeout=10.0,
    )


def _wait_completed(server_url: str, job_id: str) -> dict:
    for _ in range(40):
        r = httpx.get(f"{server_url}/api/v1/documents/{job_id}", timeout=2.0)
        if r.status_code == 200:
            body = r.json()
            if body["status"] in {"completed", "failed"}:
                return body
        time.sleep(0.2)
    raise AssertionError("job did not complete in time")


def test_upload_with_output_format_md_returns_md_file(server_url: str) -> None:
    """uploading a .docx with output_format=md produces a .md download."""
    docx_path = Path("test_upload_output.docx")
    try:
        docx_bytes = _build_docx_bytes(docx_path, ["Reach me at user@example.com."])
    finally:
        if docx_path.exists():
            docx_path.unlink()

    r = _upload(
        server_url,
        docx_bytes,
        "contract.docx",
        "application/octet-stream",
        data={"output_format": "md"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["output_ext"] == "md"
    job_id = body["id"]

    job = _wait_completed(server_url, job_id)
    assert job["status"] == "completed"

    dl = httpx.get(f"{server_url}/api/v1/documents/{job_id}/download", timeout=5.0)
    assert dl.status_code == 200
    assert "text/markdown" in dl.headers["content-type"]
    cleaned = dl.text
    assert "<PRIVATE_EMAIL1>" in cleaned
    assert "user@example.com" not in cleaned


def test_apply_feedback_endpoint_updates_result_file(server_url: str) -> None:
    """POST /apply-feedback rewrites the cleaned file in place."""
    r = _upload(
        server_url,
        b"user@example.com",
        "notes.md",
        "text/markdown",
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    _wait_completed(server_url, job_id)

    payload = {
        "actions": [
            {
                "action": "reject",
                "start": 0,
                "end": 16,
                "entity_type": "private_email",
                "text": "user@example.com",
                "original_span_index": 0,
            }
        ],
        "comment": "not actually PII",
    }
    r2 = httpx.post(
        f"{server_url}/api/v1/documents/{job_id}/apply-feedback",
        json=payload,
        timeout=5.0,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["rejected"] == 1

    # Re-download to confirm the on-disk change.
    dl = httpx.get(f"{server_url}/api/v1/documents/{job_id}/download", timeout=5.0)
    assert dl.status_code == 200
    assert "user@example.com" in dl.text
    assert "<PRIVATE_EMAIL" not in dl.text
