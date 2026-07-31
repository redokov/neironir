"""Unit tests for the runtime privacy-client tuning in
:mod:`neironir.api.dependencies`."""

from __future__ import annotations

from collections.abc import Generator

import pytest
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
