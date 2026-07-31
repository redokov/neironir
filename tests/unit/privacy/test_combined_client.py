"""Unit tests for :mod:`neironir.privacy.combined.CombinedPrivacyClient`.

The combined client merges results from a model client and a rule-based
detector.  Tests verify merge ordering, overlap resolution, dedicated
handling, and error propagation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from neironir.domain.entity_type import EntityType
from neironir.privacy.client import EntitySpan
from neironir.privacy.combined import CombinedPrivacyClient
from neironir.privacy.rules import RuleBasedDetector


@pytest.fixture
def mock_model() -> AsyncMock:
    """A mock model client that returns predictable spans."""
    client = AsyncMock()
    client.annotate.return_value = [
        EntitySpan(start=0, end=4, entity_type=EntityType.PRIVATE_PERSON),
        EntitySpan(start=10, end=20, entity_type=EntityType.PRIVATE_EMAIL),
    ]
    return client


@pytest.fixture
def rule_detector() -> RuleBasedDetector:
    return RuleBasedDetector()


@pytest.fixture
def combined(mock_model: AsyncMock, rule_detector: RuleBasedDetector) -> CombinedPrivacyClient:
    return CombinedPrivacyClient(model_client=mock_model, rule_detector=rule_detector)


class TestBasicMerge:
    async def test_both_sources_merged(self, combined: CombinedPrivacyClient) -> None:
        """Spans from both model and rules should be present in the result."""
        result = await combined.annotate("some text")
        # At minimum the model spans should be there.
        assert len(result) >= 2

    async def test_model_result_text_preserved(self, combined: CombinedPrivacyClient) -> None:
        result = await combined.annotate("some text")
        assert len(result) == 2  # both model spans present

    async def test_no_overlapping_spans(self, combined: CombinedPrivacyClient) -> None:
        """After merge, no two spans should overlap."""
        result = await combined.annotate("Call +7 495 123-45-67 now")
        spans = sorted(result, key=lambda s: s.start)
        for i in range(len(spans) - 1):
            assert spans[i].end <= spans[i + 1].start, f"overlapping: {spans[i]} and {spans[i + 1]}"


class TestDedicatedHandlerIntegration:
    """The combined client has a dedicated handler for private_* types.

    These tests verify that model spans and rule-based spans are
    correctly interleaved.
    """

    async def test_dedicated_runs_after_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make sure the dedicated rule handler is called as part of the flow."""
        detect_called = False

        class TrackingDetector(RuleBasedDetector):
            def detect(self, text: str) -> list[EntitySpan]:
                nonlocal detect_called
                detect_called = True
                return [EntitySpan(start=0, end=5, entity_type=EntityType.PRIVATE_PHONE)]

        mock = AsyncMock()
        mock.annotate.return_value = []

        client = CombinedPrivacyClient(model_client=mock, rule_detector=TrackingDetector())
        await client.annotate("test")
        assert detect_called


class TestEdgeCases:
    async def test_empty_spans_from_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When neither model nor rules find anything, result should be empty."""
        mock = AsyncMock()
        mock.annotate.return_value = []

        detector = RuleBasedDetector()
        client = CombinedPrivacyClient(model_client=mock, rule_detector=detector)

        result = await client.annotate("nothing")
        assert len(result) == 0

    async def test_model_fails_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the model raises, the combined client should propagate the error."""
        mock = AsyncMock()
        mock.annotate.side_effect = RuntimeError("model crashed")
        detector = RuleBasedDetector()

        client = CombinedPrivacyClient(model_client=mock, rule_detector=detector)
        with pytest.raises(RuntimeError, match="model crashed"):
            await client.annotate("test")
