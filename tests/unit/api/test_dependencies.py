"""Unit tests for the runtime privacy-client tuning in
:mod:`neironir.api.dependencies`."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from neironir.api import dependencies as dep
from neironir.api import dependencies as deps
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
