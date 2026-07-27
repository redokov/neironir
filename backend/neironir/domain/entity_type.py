"""Entity types recognised by the redaction pipeline.

The eight values mirror the entity names emitted by the privacy-filter model
(see `docs/architecture.md`, section "Типы сущностей и плейсхолдеры"). The
placeholder templates are stored separately so the renderer can format
`<NAME{n}>` for a given entity type via :class:`PlaceholderCounter`.
"""

from __future__ import annotations

from enum import Enum


class EntityType(str, Enum):  # noqa: UP042
    """Closed set of entity types the service knows how to redact.

    The spec (``docs/agents/02-domain-and-contracts.md``) prescribes the
    ``(str, Enum)`` form. We keep it verbatim even though Python 3.11's
    ``enum.StrEnum`` would be a slightly cleaner choice, because the contract
    is the source of truth.
    """

    PRIVATE_PERSON = "private_person"
    PRIVATE_ADDRESS = "private_address"
    PRIVATE_EMAIL = "private_email"
    PRIVATE_PHONE = "private_phone"
    PRIVATE_DATE = "private_date"
    PRIVATE_URL = "private_url"
    ACCOUNT_NUMBER = "account_number"
    SECRET = "secret"


TEMPLATE_FORMAT: dict[EntityType, str] = {
    EntityType.PRIVATE_PERSON: "<PRIVATE_PERSON{n}>",
    EntityType.PRIVATE_ADDRESS: "<PRIVATE_ADDRESS{n}>",
    EntityType.PRIVATE_EMAIL: "<PRIVATE_EMAIL{n}>",
    EntityType.PRIVATE_PHONE: "<PRIVATE_PHONE{n}>",
    EntityType.PRIVATE_DATE: "<PRIVATE_DATE{n}>",
    EntityType.PRIVATE_URL: "<PRIVATE_URL{n}>",
    EntityType.ACCOUNT_NUMBER: "<ACCOUNT_NUMBER{n}>",
    EntityType.SECRET: "<SECRET{n}>",
}

__all__ = ["EntityType", "TEMPLATE_FORMAT"]
