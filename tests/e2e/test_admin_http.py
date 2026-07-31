"""E2E test: spin up a real uvicorn process and exercise the admin API.

This is the strongest check we can do for the admin feature: the app
must not just import cleanly, it must actually serve HTTP requests for
the admin dashboard endpoints.

All admin and rules endpoints require an authenticated session cookie.
The ``admin_cookies`` fixture obtains one via ``POST /login`` and
shares it across tests.
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

# Auth credentials used for e2e tests.  The server reads them from env.
E2E_ADMIN_USER = "testadmin"
E2E_ADMIN_PASSWORD = "testpass"
E2E_SESSION_SECRET = "e2e-test-secret-do-not-use-in-prod"


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


# ---------------------------------------------------------------------------
# Server fixture (module-scoped)
# ---------------------------------------------------------------------------


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
        # Auth env vars required by the app.
        "NEIRONIR_SESSION_SECRET": E2E_SESSION_SECRET,
        "NEIRONIR_ADMIN_USER": E2E_ADMIN_USER,
        "NEIRONIR_ADMIN_PASSWORD": E2E_ADMIN_PASSWORD,
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


# ---------------------------------------------------------------------------
# Auth fixture — logs in once and returns session + CSRF cookies
# ---------------------------------------------------------------------------


_ADMIN_COOKIES: dict[str, str] | None = None


@pytest.fixture(scope="module")
def admin_cookies(server_url: str) -> dict[str, str]:
    """Return a dict ``{cookie_name: cookie_value}`` for the admin session."""
    global _ADMIN_COOKIES
    if _ADMIN_COOKIES is not None:
        return _ADMIN_COOKIES

    with httpx.Client(base_url=server_url) as client:
        r = client.post(
            "/login",
            data={"username": E2E_ADMIN_USER, "password": E2E_ADMIN_PASSWORD},
            follow_redirects=False,
            timeout=5.0,
        )
        assert r.status_code in (302, 303), f"login failed: {r.status_code} {r.text[:200]}"
        # Collect cookies from the response.
        cookies = {}
        for cookie in r.cookies.jar:
            cookies[cookie.name] = cookie.value
        assert "neironir_session" in cookies, "session cookie not set after login"
        assert "neironir_csrf" in cookies, "csrf cookie not set after login"
        _ADMIN_COOKIES = cookies
        return cookies


# ---------------------------------------------------------------------------
# Helper — build a request that includes the admin session + CSRF header
# ---------------------------------------------------------------------------


def _admin_headers(cookies: dict[str, str]) -> dict[str, str]:
    """Return headers for an authenticated admin request.

    Includes the CSRF token for POST/PUT/DELETE. GET requests don't
    strictly need it, but it does no harm.
    """
    headers = {"X-CSRF-Token": cookies.get("neironir_csrf", "")}
    return headers


def _admin_cookies_httpx(cookies: dict[str, str]) -> dict[str, str]:
    """Return a dict suitable for passing as ``cookies=`` to httpx.

    httpx uses a ``httpx.Cookies`` jar, but passing a plain dict works
    as well: ``httpx.get(..., cookies=... )``.
    """
    return {k: v for k, v in cookies.items()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_admin_stats_endpoint(server_url: str, admin_cookies: dict[str, str]) -> None:
    r = httpx.get(
        f"{server_url}/api/v1/admin/stats",
        cookies=_admin_cookies_httpx(admin_cookies),
        timeout=2.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert "total_jobs" in body
    assert "by_day" in body
    assert isinstance(body["by_day"], dict)


def test_admin_documents_endpoint(server_url: str, admin_cookies: dict[str, str]) -> None:
    r = httpx.get(
        f"{server_url}/api/v1/admin/documents",
        cookies=_admin_cookies_httpx(admin_cookies),
        timeout=2.0,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_training_status_endpoint(server_url: str, admin_cookies: dict[str, str]) -> None:
    r = httpx.get(
        f"{server_url}/api/v1/admin/training/status",
        cookies=_admin_cookies_httpx(admin_cookies),
        timeout=2.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "idle"
    assert body["pid"] is None
    assert body["progress"]["epoch"] == 0


def test_admin_training_stop_when_idle(server_url: str, admin_cookies: dict[str, str]) -> None:
    r = httpx.post(
        f"{server_url}/api/v1/admin/training/stop",
        cookies=_admin_cookies_httpx(admin_cookies),
        headers=_admin_headers(admin_cookies),
        timeout=2.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["signal_sent"] is False


def test_admin_html_redirect_when_unauthenticated(server_url: str) -> None:
    """Without a session cookie, ``GET /admin`` should redirect to /login."""
    r = httpx.get(f"{server_url}/admin", timeout=2.0, follow_redirects=False)
    assert r.status_code in (302, 303), f"expected redirect, got {r.status_code}"
    assert "/login" in r.headers.get("location", "")


def test_admin_html_served_when_authenticated(
    server_url: str, admin_cookies: dict[str, str]
) -> None:
    r = httpx.get(
        f"{server_url}/admin",
        cookies=_admin_cookies_httpx(admin_cookies),
        timeout=2.0,
    )
    assert r.status_code == 200
    assert "Админка" in r.text


def test_admin_static_js_served(server_url: str) -> None:
    """Static files are not behind auth — they are served unconditionally."""
    r = httpx.get(f"{server_url}/static/admin.js", timeout=2.0)
    assert r.status_code == 200
    assert "loadStats" in r.text


def test_admin_unauthorized_without_session(server_url: str) -> None:
    """All admin endpoints should reject unauthenticated callers."""
    r = httpx.get(f"{server_url}/api/v1/admin/stats", timeout=2.0)
    assert r.status_code == 401

    r = httpx.get(f"{server_url}/api/v1/admin/documents", timeout=2.0)
    assert r.status_code == 401

    r = httpx.post(f"{server_url}/api/v1/admin/training/stop", timeout=2.0)
    assert r.status_code == 401


def test_admin_unauthorized_bad_password(server_url: str) -> None:
    """Login with wrong password should be rejected."""
    with httpx.Client(base_url=server_url, follow_redirects=False) as client:
        r = client.post(
            "/login",
            data={"username": E2E_ADMIN_USER, "password": "wrong"},
            timeout=5.0,
        )
        assert r.status_code in (302, 303)
        # Should redirect to /login?error=invalid (no cookies set)
        location = r.headers.get("location", "")
        assert "login" in location
        assert "error=invalid" in location
        # No session cookie should be set
        assert not any(c.name == "neironir_session" for c in r.cookies.jar)
