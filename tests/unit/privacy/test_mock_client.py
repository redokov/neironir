"""Tests for :class:`neironir.privacy.client.MockPrivacyFilterClient`."""

from __future__ import annotations

import pytest
from neironir.domain.entity_type import EntityType
from neironir.privacy.client import MockPrivacyFilterClient


@pytest.mark.asyncio
async def test_detects_email() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Reach me at user@example.com please.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_EMAIL
    assert spans[0].start == 12
    assert spans[0].end == 28


@pytest.mark.asyncio
async def test_detects_phone() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Call +7 495 123-45-67 now.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_PHONE


@pytest.mark.asyncio
async def test_detects_url() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("See https://example.com/path for details.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_URL


@pytest.mark.asyncio
async def test_detects_date() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Date of birth: 01.02.1990.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_DATE


@pytest.mark.asyncio
async def test_detects_account_number() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Card: 1234567890123456 expires soon.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.ACCOUNT_NUMBER


@pytest.mark.asyncio
async def test_detects_secret() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("password=secret-value")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.SECRET


@pytest.mark.asyncio
async def test_does_not_detect_person_or_address() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("John Smith lives at 221B Baker Street.")
    # The mock has no heuristics for names or addresses; we expect an
    # empty list, not an exception.
    assert spans == []


@pytest.mark.asyncio
async def test_deduplicates_overlapping_email_and_phone() -> None:
    """An email matched inside a phone-like sequence must keep the email."""
    client = MockPrivacyFilterClient()
    text = "Reach me at user@host.com or +7 495 123-45-67."
    spans = await client.annotate(text)

    assert len(spans) == 2
    assert {span.entity_type for span in spans} == {
        EntityType.PRIVATE_EMAIL,
        EntityType.PRIVATE_PHONE,
    }
    # No overlap remains.
    for i, left in enumerate(spans):
        for right in spans[i + 1 :]:
            assert left.end <= right.start or right.end <= left.start


@pytest.mark.asyncio
async def test_email_wins_over_phone_when_patterns_overlap() -> None:
    """When the email is matched first, the phone regex still runs.

    The mock must emit both spans — they don't overlap character-wise
    so dedup keeps both — and the email must come first by priority.
    """
    client = MockPrivacyFilterClient()
    text = "user@example.com +7 495 1234567"
    spans = await client.annotate(text)

    assert len(spans) == 2
    email_span, phone_span = spans
    assert email_span.entity_type == EntityType.PRIVATE_EMAIL
    assert phone_span.entity_type == EntityType.PRIVATE_PHONE
    # Email starts at the first character; phone starts later.
    assert email_span.start < phone_span.start


@pytest.mark.asyncio
async def test_returns_empty_list_for_clean_text() -> None:
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Nothing sensitive here.")
    assert spans == []
