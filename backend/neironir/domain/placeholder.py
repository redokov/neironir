"""Per-document placeholder numbering.

Each :class:`PlaceholderCounter` instance owns an independent counter for every
entity type, so two counters running in parallel (e.g. for two jobs) do not
share state. The counter is a simple integer that starts at 0 and the first
emitted placeholder uses ``n=1`` — matching the spec in
`docs/architecture.md` ("нумерация сквозная по документу, не по всему сервису").
"""

from __future__ import annotations

from neironir.domain.entity_type import TEMPLATE_FORMAT, EntityType


class PlaceholderCounter:
    """Issue sequential ``<NAME{n}>`` placeholders for a single document."""

    def __init__(self) -> None:
        self._counters: dict[EntityType, int] = {entity_type: 0 for entity_type in EntityType}

    def next(self, entity_type: EntityType) -> str:
        """Increment the counter for ``entity_type`` and return the placeholder."""
        self._counters[entity_type] += 1
        return TEMPLATE_FORMAT[entity_type].format(n=self._counters[entity_type])

    def reset(self) -> None:
        """Zero all counters; mainly useful for tests."""
        for entity_type in EntityType:
            self._counters[entity_type] = 0


__all__ = ["PlaceholderCounter"]
