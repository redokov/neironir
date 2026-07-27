"""Tests for ``neironir.domain.entity_type``."""

from __future__ import annotations

from neironir.domain.entity_type import TEMPLATE_FORMAT, EntityType


def test_entity_type_has_exactly_eight_values() -> None:
    assert len(EntityType) == 8


def test_entity_type_string_values_match_architecture() -> None:
    # Names are explicitly asserted as strings so a silent rename of the enum
    # value (e.g. PRIVATE_PERSON = "personal_name") would fail loudly.
    assert EntityType.PRIVATE_PERSON.value == "private_person"
    assert EntityType.PRIVATE_ADDRESS.value == "private_address"
    assert EntityType.PRIVATE_EMAIL.value == "private_email"
    assert EntityType.PRIVATE_PHONE.value == "private_phone"
    assert EntityType.PRIVATE_DATE.value == "private_date"
    assert EntityType.PRIVATE_URL.value == "private_url"
    assert EntityType.ACCOUNT_NUMBER.value == "account_number"
    assert EntityType.SECRET.value == "secret"


def test_entity_type_is_str_enum() -> None:
    # Required so FastAPI / Pydantic serialise the value as a bare string.
    assert isinstance(EntityType.PRIVATE_PERSON, str)
    assert EntityType.PRIVATE_PERSON == "private_person"


def test_template_format_covers_every_entity_type() -> None:
    assert set(TEMPLATE_FORMAT.keys()) == set(EntityType)
    assert len(TEMPLATE_FORMAT) == 8


def test_template_format_strings_match_architecture() -> None:
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_PERSON] == "<PRIVATE_PERSON{n}>"
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_ADDRESS] == "<PRIVATE_ADDRESS{n}>"
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_EMAIL] == "<PRIVATE_EMAIL{n}>"
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_PHONE] == "<PRIVATE_PHONE{n}>"
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_DATE] == "<PRIVATE_DATE{n}>"
    assert TEMPLATE_FORMAT[EntityType.PRIVATE_URL] == "<PRIVATE_URL{n}>"
    # Note: the non-PRIVATE_* types do not carry a PRIVATE_ prefix.
    assert TEMPLATE_FORMAT[EntityType.ACCOUNT_NUMBER] == "<ACCOUNT_NUMBER{n}>"
    assert TEMPLATE_FORMAT[EntityType.SECRET] == "<SECRET{n}>"
