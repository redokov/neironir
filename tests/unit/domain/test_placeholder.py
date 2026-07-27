"""Tests for ``neironir.domain.placeholder.PlaceholderCounter``."""

from __future__ import annotations

from neironir.domain.entity_type import EntityType
from neironir.domain.placeholder import PlaceholderCounter


def test_first_call_for_each_type_yields_n_equals_1() -> None:
    counter = PlaceholderCounter()
    for entity_type in EntityType:
        assert counter.next(entity_type).endswith("1>"), entity_type
        assert counter.next(entity_type).endswith("2>"), entity_type


def test_counter_increments_within_same_type() -> None:
    counter = PlaceholderCounter()
    assert counter.next(EntityType.PRIVATE_PERSON) == "<PRIVATE_PERSON1>"
    assert counter.next(EntityType.PRIVATE_PERSON) == "<PRIVATE_PERSON2>"
    assert counter.next(EntityType.PRIVATE_PERSON) == "<PRIVATE_PERSON3>"


def test_counters_are_isolated_per_type() -> None:
    counter = PlaceholderCounter()
    # Advance PRIVATE_PERSON and PRIVATE_EMAIL; the other types must not be
    # affected.
    counter.next(EntityType.PRIVATE_PERSON)
    counter.next(EntityType.PRIVATE_PERSON)
    counter.next(EntityType.PRIVATE_EMAIL)
    counter.next(EntityType.PRIVATE_EMAIL)
    counter.next(EntityType.PRIVATE_EMAIL)

    # PRIVATE_PERSON is at 2, PRIVATE_EMAIL is at 3, untouched types stay at 0
    # so the next call yields 1.
    assert counter.next(EntityType.PRIVATE_PERSON) == "<PRIVATE_PERSON3>"
    assert counter.next(EntityType.PRIVATE_EMAIL) == "<PRIVATE_EMAIL4>"
    assert counter.next(EntityType.SECRET) == "<SECRET1>"
    assert counter.next(EntityType.ACCOUNT_NUMBER) == "<ACCOUNT_NUMBER1>"


def test_fresh_instance_starts_at_1_per_type() -> None:
    counter_a = PlaceholderCounter()
    counter_b = PlaceholderCounter()
    for _ in range(5):
        counter_a.next(EntityType.PRIVATE_PERSON)
    # counter_b must not share the global state — its first call is 1.
    assert counter_b.next(EntityType.PRIVATE_PERSON) == "<PRIVATE_PERSON1>"


def test_template_shape_is_preserved() -> None:
    counter = PlaceholderCounter()
    placeholder = counter.next(EntityType.ACCOUNT_NUMBER)
    assert placeholder.startswith("<ACCOUNT_NUMBER")
    assert placeholder.endswith(">")
