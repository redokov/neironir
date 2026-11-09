"""Unit tests for the runtime privacy-client tuning in
:mod:`neironir.api.dependencies`."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from neironir.api import dependencies as dep
from neironir.api import dependencies as deps
from neironir.auth.dependencies import is_loopback_client, require_documents_auth
from neironir.config import Settings
from neironir.privacy.client import MockPrivacyFilterClient, SubprocessPrivacyFilterClient
from neironir.privacy.combined import CombinedPrivacyClient
from neironir.privacy.rules import RuleBasedDetector


@pytest.fixture(autouse=True)
def _reset_privacy_singleton() -> Generator[None, None, None]:
    """Restore the module-level singleton after each test."""
    original = deps._privacy_client  # noqa: SLF001
    yield
    deps._privacy_client = original  # noqa: SLF001


class TestUpdatePrivacyTimeout:
    def test_updates_subprocess_client(self) -> None:
        client = SubprocessPrivacyFilterClient(opf_cmd=["opf"], timeout_s=10.0)
        deps._privacy_client = client  # noqa: SLF001

        assert deps.update_privacy_timeout(42.5) is True
        assert client.timeout_s == 42.5

    def test_unwraps_combined_client(self) -> None:
        model = SubprocessPrivacyFilterClient(opf_cmd=["opf"], timeout_s=10.0)
        deps._privacy_client = CombinedPrivacyClient(  # noqa: SLF001
            model_client=model,
            rule_detector=RuleBasedDetector(),
        )

        assert deps.update_privacy_timeout(77.0) is True
        assert model.timeout_s == 77.0

    def test_mock_client_returns_false(self) -> None:
        deps._privacy_client = MockPrivacyFilterClient()  # noqa: SLF001
        assert deps.update_privacy_timeout(5.0) is False

    def test_no_client_returns_false(self) -> None:
        deps._privacy_client = None  # noqa: SLF001
        assert deps.update_privacy_timeout(5.0) is False


class TestCombinedClientAccessors:
    def test_model_client_property(self) -> None:
        model = MockPrivacyFilterClient()
        combined = CombinedPrivacyClient(model_client=model)
        assert combined.model_client is model


class TestSubprocessProbe:
    """Regression for the e2e-fallback probe (combined mode on Windows).

    ``_probe_subprocess`` must not deadlock when called from inside a
    running event loop — the old run_coroutine_threadsafe/future.result
    pattern timed out on the same loop, was misread as "unsupported", and
    silently downgraded combined mode to mock.
    """

    def test_probe_true_outside_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dep, "_subprocess_probe_cache", None)
        assert dep._probe_subprocess() is True

    def test_probe_inside_running_loop_does_not_deadlock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dep, "_subprocess_probe_cache", None)

        async def main() -> bool:
            return dep._probe_subprocess()

        # asyncio.run would hang forever with the old implementation;
        # wait_for gives the test a hard budget.
        async def guarded() -> bool:
            return await asyncio.wait_for(asyncio.shield(main()), timeout=8.0)

        assert asyncio.run(guarded()) is True

    def test_result_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dep, "_subprocess_probe_cache", None)
        first = dep._probe_subprocess()
        assert dep._subprocess_probe_cache is first
        # Second call returns the cache without probing again.
        monkeypatch.setattr(dep, "_probe_coro", None)  # type: ignore[arg-type]
        assert dep._probe_subprocess() is first


class TestIsLoopbackClient:
    """Spec 007 TD-001: ``is_loopback_client`` fail-closed classification.

    Only ``request.client`` is consulted (no ``X-Forwarded-*``); anything
    missing, empty, or unparseable is non-loopback.
    """

    @staticmethod
    def _request_with_client(client: Any) -> MagicMock:
        request = MagicMock()
        request.client = client
        return request

    def _client(self, host: str) -> MagicMock:
        c = MagicMock()
        c.host = host
        return c

    def test_127_0_0_1_is_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(self._client("127.0.0.1"))) is True

    def test_ipv6_loopback_is_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(self._client("::1"))) is True

    def test_other_local_address_is_not_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(self._client("10.0.0.5"))) is False

    def test_missing_client_is_non_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(None)) is False

    def test_empty_host_is_non_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(self._client(""))) is False

    def test_unparseable_host_is_non_loopback(self) -> None:
        assert is_loopback_client(self._request_with_client(self._client("testclient"))) is False

    def test_no_client_attr_is_non_loopback(self) -> None:
        request = MagicMock()
        del request.client
        assert is_loopback_client(request) is False


class _FakeHeaders:
    """Minimal stand-in for ``Request.headers`` (case-insensitive .get)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._mapping.get(key, default)


class TestRequireDocumentsAuthFailClosed:
    """Spec 007 FR-001/FR-002/FR-003: auth guard precedence at unit level."""

    def _settings(self, api_keys: str = "key-1") -> Settings:
        return Settings(
            storage_dir="/tmp/none",
            session_secret="secret",
            admin_user="admin",
            admin_password="pass",
            api_keys=api_keys,
        )

    def _client(self, host: str) -> MagicMock:
        c = MagicMock()
        c.host = host
        return c

    def _request(
        self,
        host: str,
        authorization: str | None,
        method: str = "GET",
    ) -> MagicMock:
        request = MagicMock()
        request.client = self._client(host)
        request.method = method
        header: dict[str, str] = {"authorization": authorization} if authorization else {}
        request.headers = _FakeHeaders(header)
        request.state = MagicMock()
        return request

    def test_loopback_without_header_passes(self) -> None:
        request = self._request("127.0.0.1", None)
        require_documents_auth(request, self._settings())  # no raise

    def test_non_loopback_without_header_raises_401(self) -> None:
        request = self._request("10.0.0.5", None)
        try:
            require_documents_auth(request, self._settings())
        except Exception as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("expected 401")

    def test_non_loopback_missing_client_raises_401(self) -> None:
        request = MagicMock()
        request.client = None
        request.headers = _FakeHeaders({})
        request.method = "GET"
        request.state = MagicMock()
        try:
            require_documents_auth(request, self._settings())
        except Exception as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("expected 401")

    def test_loopback_invalid_bearer_raises_401(self) -> None:
        request = self._request("127.0.0.1", "Bearer wrong")
        try:
            require_documents_auth(request, self._settings())
        except Exception as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("expected 401")

    def test_valid_bearer_passes_and_marks_auth_via(self) -> None:
        request = self._request("10.0.0.5", "Bearer key-1")
        require_documents_auth(request, self._settings())
        assert request.state.auth_via == "api_key"
