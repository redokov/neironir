"""Dedup tests for :class:`MockPrivacyFilterClient`.

The basic per-rule detection tests live in
:mod:`tests.unit.privacy.test_mock_client`. This file focuses on the
overlap resolution — that a more specific rule (e.g. email) wins over a
less specific one (phone), and that the resulting spans don't overlap.
"""

from __future__ import annotations

import pytest
from neironir.domain.entity_type import EntityType
from neironir.privacy.client import MockPrivacyFilterClient


def _has_overlap(spans: list) -> bool:
    """Return True if any pair of spans shares a character."""
    ordered = sorted(spans, key=lambda s: s.start)
    return any(ordered[i].end > ordered[i + 1].start for i in range(len(ordered) - 1))


@pytest.mark.asyncio
async def test_dedup_keeps_email_over_10_digit_run() -> None:
    """Email regex matches ``a@b.com`` and account-number regex matches the 10 digits.

    The 10-digit run is below the 16-digit threshold for
    ``ACCOUNT_NUMBER``, so neither pattern claims it as an
    ``ACCOUNT_NUMBER``. The phone regex *would* match the 10-digit
    run, but the dedup keeps the email and rejects the phone because
    the email sits at a higher priority and starts earlier. The
    resulting span list has no overlaps.
    """
    client = MockPrivacyFilterClient()
    text = "a@b.com 1234567890"
    spans = await client.annotate(text)

    types = {span.entity_type for span in spans}
    assert EntityType.PRIVATE_EMAIL in types
    assert not _has_overlap(spans), f"overlap remains: {spans}"


@pytest.mark.asyncio
async def test_dedup_drops_phone_inside_16_digit_card() -> None:
    r"""A 16-20 digit run is detected as ``ACCOUNT_NUMBER``, not phone.

    The phone regex has a ``(?!\d{16,})`` guard for this case. We
    verify it explicitly.
    """
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Card 1234567890123456 here.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.ACCOUNT_NUMBER


@pytest.mark.asyncio
async def test_dedup_email_wins_over_url_when_overlap_exists() -> None:
    """If a URL is anchored to start with an email-like local part, the
    email pattern matches first and overlaps with the URL.

    Emails have higher priority than URLs, so the URL must be dropped.
    """
    client = MockPrivacyFilterClient()
    # The email pattern matches ``user@example.com`` (up to and
    # including the first TLD label). The URL pattern would match
    # ``https://user@example.com/path`` — which starts earlier and
    # therefore captures the email substring. Email is higher priority
    # and matches first, but its position is the *same range* the URL
    # would have started from — so dedup must drop the URL.
    text = "See https://user@example.com/path"
    spans = await client.annotate(text)

    # We expect only one span — the email — because the URL would
    # overlap with it and is lower priority.
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_EMAIL
    assert not _has_overlap(spans)


@pytest.mark.asyncio
async def test_dedup_two_rules_at_same_offset_keeps_higher_priority() -> None:
    """When two rules overlap, the higher-priority one wins.

    We drive the dedup helper directly with a synthetic pair and a
    synthetic triplet to verify the priority rule regardless of the
    order in which the spans are passed.
    """
    from neironir.privacy.client import EntitySpan, _deduplicate

    email = EntitySpan(0, 17, EntityType.PRIVATE_EMAIL)
    url = EntitySpan(0, 21, EntityType.PRIVATE_URL)  # overlaps with email
    assert _deduplicate([email, url]) == [email]
    assert _deduplicate([url, email]) == [email]


@pytest.mark.asyncio
async def test_dedup_keeps_url_when_no_email_match_exists() -> None:
    """Sanity check: a bare URL produces exactly one URL span."""
    client = MockPrivacyFilterClient()
    spans = await client.annotate("Visit https://example.com/x?y=1 today.")
    assert len(spans) == 1
    assert spans[0].entity_type == EntityType.PRIVATE_URL


@pytest.mark.asyncio
async def test_dedup_three_overlapping_rules_keeps_highest_priority() -> None:
    """Three rules with overlapping windows: only the highest-priority one survives.

    ``PRIVATE_EMAIL`` (priority 0) is the highest priority of the three
    rules used here, so it must always win — regardless of the input
    order. The other two spans must be evicted.
    """
    from neironir.privacy.client import EntitySpan, _deduplicate

    a = EntitySpan(0, 20, EntityType.PRIVATE_URL)  # priority 2 (lowest)
    b = EntitySpan(2, 15, EntityType.PRIVATE_EMAIL)  # priority 0 (highest)
    c = EntitySpan(5, 12, EntityType.PRIVATE_PHONE)  # priority 1

    assert _deduplicate([a, b, c]) == [b]
    assert _deduplicate([c, b, a]) == [b]
    assert _deduplicate([b, a, c]) == [b]


@pytest.mark.asyncio
async def test_dedup_keeps_non_overlapping_spans() -> None:
    """Spans that don't overlap must all be kept, regardless of priority."""
    from neironir.privacy.client import EntitySpan, _deduplicate

    a = EntitySpan(0, 5, EntityType.PRIVATE_EMAIL)
    b = EntitySpan(10, 20, EntityType.PRIVATE_URL)
    c = EntitySpan(30, 40, EntityType.PRIVATE_PHONE)
    kept = _deduplicate([a, b, c])
    assert kept == [a, b, c]


@pytest.mark.asyncio
async def test_dedup_handles_adjacent_spans_without_gap() -> None:
    """Spans touching at one character must not be considered overlapping.

    ``[0..5)`` and ``[5..10)`` share the boundary but no character.
    Both should be kept.
    """
    from neironir.privacy.client import EntitySpan, _deduplicate

    a = EntitySpan(0, 5, EntityType.PRIVATE_EMAIL)
    b = EntitySpan(5, 10, EntityType.PRIVATE_URL)
    kept = _deduplicate([a, b])
    assert kept == [a, b]
