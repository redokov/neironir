"""HTTP-level integration tests for the auth API (login / logout / whoami).

These tests use ``TestClient`` and override auth dependencies to bypass
the session check so we can verify the login/logout flow end-to-end
without needing a real browser.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.auth.dependencies import require_admin_auth, verify_csrf
from neironir.config import Settings
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.storage.local import LocalStorage

TEST_SECRET = "test-secret-for-auth-api-tests"
TEST_USER = "testadmin"
TEST_PASSWORD = "testpass123"


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Build a fully configured TestClient with auth credentials."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    storage = LocalStorage(storage_dir)
    privacy = MockPrivacyFilterClient()

    real_settings = Settings(
        storage_dir=str(storage_dir),
        session_secret=TEST_SECRET,
        admin_user=TEST_USER,
        admin_password=TEST_PASSWORD,
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy
    # Keep the admin-UI middleware in sync with the overridden settings
    # (it reads ``request.app.state.settings`` per request).
    app.state.settings = real_settings
    # Bypass auth guards on admin/rules endpoints so we can verify the
    # login flow itself (which is not protected by these dep overrides).
    app.dependency_overrides[require_admin_auth] = lambda: {"is_admin": True, "user": TEST_USER}
    app.dependency_overrides[verify_csrf] = lambda: None

    with TestClient(app) as c:
        yield c


class TestLogin:
    def test_get_login_page(self, client: TestClient) -> None:
        r = client.get("/login")
        assert r.status_code == 200
        assert "Вход" in r.text

    def test_login_success_sets_cookies(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        location = r.headers.get("location", "")
        assert "admin" in location

        # Verify both cookies are present
        set_cookie = r.headers.get("set-cookie", "")
        assert "neironir_session" in set_cookie
        assert "neironir_csrf" in set_cookie
        # Session cookie must be HttpOnly
        assert "HttpOnly" in set_cookie

    def test_login_bad_password_redirects_with_error(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            data={"username": TEST_USER, "password": "wrong"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        location = r.headers.get("location", "")
        assert "login" in location
        assert "error=invalid" in location
        # No session cookie should be set
        set_cookie = r.headers.get("set-cookie", "")
        assert "neironir_session" not in (set_cookie or "")

    def test_login_unknown_user(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            data={"username": "nobody", "password": "anything"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        location = r.headers.get("location", "")
        assert "error=invalid" in location

    def test_logout_clears_cookies(self, client: TestClient) -> None:
        # First log in to get valid cookies
        r = client.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        session_cookie = client.cookies.get("neironir_session", domain="")
        csrf_cookie = client.cookies.get("neironir_csrf", domain="")

        # Now log out
        r = client.post(
            "/logout",
            cookies={"neironir_session": session_cookie, "neironir_csrf": csrf_cookie},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        location = r.headers.get("location", "")
        assert "login" in location

        # The cookies should be cleared (Max-Age=0 or deleted)
        set_cookie = r.headers.get("set-cookie", "")
        assert "neironir_session" in (set_cookie or "")
        assert "neironir_csrf" in (set_cookie or "")


class TestLoginNextRedirect:
    """Open-redirect protection for the ``?next=`` parameter (issue 3.2)."""

    def _login(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)

    def test_get_login_external_next_rejected(self, client: TestClient) -> None:
        self._login(client)
        r = client.get(
            "/login",
            params={"next": "https://evil.example"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/admin"

    def test_get_login_protocol_relative_next_rejected(self, client: TestClient) -> None:
        self._login(client)
        r = client.get(
            "/login",
            params={"next": "//evil.example"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/admin"

    def test_get_login_backslash_next_rejected(self, client: TestClient) -> None:
        self._login(client)
        r = client.get(
            "/login",
            params={"next": "/\\evil.example"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/admin"

    def test_get_login_relative_next_allowed(self, client: TestClient) -> None:
        self._login(client)
        r = client.get(
            "/login",
            params={"next": "/admin"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/admin"

    def test_post_login_external_next_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            params={"next": "https://evil.example"},
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin"

    def test_post_login_relative_next_allowed(self, client: TestClient) -> None:
        r = client.post(
            "/login",
            params={"next": "/admin"},
            data={"username": TEST_USER, "password": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/admin"


class TestWhoami:
    def test_whoami_unauthenticated(self, client: TestClient) -> None:
        r = client.get("/api/v1/auth/whoami")
        assert r.status_code == 200
        assert r.json() == {"is_admin": False}

    def test_whoami_authenticated(self, client: TestClient) -> None:
        # Log in first
        client.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASSWORD},
        )
        r = client.get("/api/v1/auth/whoami")
        assert r.status_code == 200
        body = r.json()
        assert body["is_admin"] is True
        assert body["user"] == TEST_USER


class TestAdminEndpointsRequireAuth:
    """Verify that admin endpoints return 401 when the auth override
    is NOT applied. We test this by creating a *second* TestClient
    that does NOT override auth dependencies."""

    def test_stats_requires_session(self, tmp_path: Path) -> None:
        storage_dir = tmp_path / "storage-noauth"
        storage_dir.mkdir()

        app = create_app()
        # Do NOT override auth dependencies — they should reject.
        test_settings = Settings(
            storage_dir=str(storage_dir),
            session_secret=TEST_SECRET,
            admin_user=TEST_USER,
            admin_password=TEST_PASSWORD,
        )
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_storage] = lambda: LocalStorage(storage_dir)
        app.dependency_overrides[get_privacy] = lambda: MockPrivacyFilterClient()

        with TestClient(app) as client:
            r = client.get("/api/v1/admin/stats")
            assert r.status_code == 401

    def test_rules_requires_session(self, tmp_path: Path) -> None:
        storage_dir = tmp_path / "storage-noauth-rules"
        storage_dir.mkdir()

        app = create_app()
        test_settings = Settings(
            storage_dir=str(storage_dir),
            session_secret=TEST_SECRET,
            admin_user=TEST_USER,
            admin_password=TEST_PASSWORD,
        )
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_storage] = lambda: LocalStorage(storage_dir)
        app.dependency_overrides[get_privacy] = lambda: MockPrivacyFilterClient()

        with TestClient(app) as client:
            r = client.get("/api/v1/rules")
            assert r.status_code == 401
