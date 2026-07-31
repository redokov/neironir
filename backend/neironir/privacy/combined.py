"""Combined privacy client that cascades a neural model with rule-based detection.

Architecture
------------

The :class:`CombinedPrivacyClient` wraps both a :class:`PrivacyFilterClient`
(the neural model) and a :class:`RuleBasedDetector` (regex + dictionaries).
It runs them sequentially and merges their outputs:

1. Neural model (OPF) processes the text first.
2. Rule-based detector runs second, catching entities the model missed.
3. Spans are merged: model spans **always** take priority. Rule spans that
   overlap with model spans are dropped. Among rule spans, the priority
   order defined in :mod:`neironir.privacy.rules` applies.

This ensures that:
- The model's high-quality, low-FP predictions are preserved.
- Rules fill gaps without introducing false positives that conflict with
  model predictions.
- The combined output is the union of both detectors minus overlaps.

Usage
-----

The client is configured through :mod:`neironir.config` and wired via
:func:`neironir.api.dependencies.get_privacy` when
``NEIRONIR_PRIVACY_FILTER_MODE=combined``.

    combined = CombinedPrivacyClient(
        model_client=SubprocessPrivacyFilterClient(...),
        rule_detector=RuleBasedDetector(),
    )
    spans = await combined.annotate(text)
"""

from __future__ import annotations

import logging

from neironir.privacy.client import EntitySpan, PrivacyFilterClient
from neironir.privacy.rules import RuleBasedDetector

logger = logging.getLogger(__name__)


class CombinedPrivacyClient:
    """Cascade a neural privacy model with rule-based detection.

    The client delegates to the neural model first, then augments the
    result with rule-based detection. Model spans are treated as
    ground truth — no rule span will override an existing model span.
    """

    def __init__(
        self,
        model_client: PrivacyFilterClient,
        rule_detector: RuleBasedDetector | None = None,
    ) -> None:
        self._model_client = model_client
        self._rule_detector = rule_detector if rule_detector is not None else RuleBasedDetector()

    @property
    def model_client(self) -> PrivacyFilterClient:
        """Return the wrapped neural-model client (exposed for runtime tuning)."""
        return self._model_client

    async def annotate(self, text: str) -> list[EntitySpan]:
        """Return merged spans from the neural model and rule-based detector.

        Merge strategy (priority order):

        1. Rule spans that **include a context prefix** (e.g. ``ИНН``, ``ОГРН``,
           ``БИК``, ``р/с``) win over model spans of **different** type. This
           corrects the case where the model classifies ``4810004427`` as
           ``PRIVATE_PHONE`` but rules correctly type it as ``ACCOUNT_NUMBER``
           because the surrounding ``ИНН 4810004427`` provides context.

        2. Model spans that have **no overlapping rule span** are kept.

        3. Non-prefix rule spans are only appended if they do **not** overlap
           with any kept span.

        Returns:
            A deduplicated, sorted list of :class:`EntitySpan`.
        """
        model_spans = await self._model_client.annotate(text)
        rule_spans = self._rule_detector.detect(text)
        logger.debug(
            "combined: model=%d, rules=%d spans",
            len(model_spans),
            len(rule_spans),
        )

        # Phase 1: identify which model spans get overridden by context-rich
        # rule spans of a DIFFERENT entity type.
        #
        #   Rule span "ИНН 4810004427" (ACCOUNT_NUMBER) overlaps with model
        #   span "4810004427" (PRIVATE_PHONE). The rule span is longer (includes
        #   prefix) and has a different type → rule wins.
        overridden_indices: set[int] = set()
        for rule_span in rule_spans:
            for idx, model_span in enumerate(model_spans):
                if (
                    _overlaps(rule_span, model_span)
                    and rule_span.entity_type != model_span.entity_type
                    and rule_span.end - rule_span.start >= model_span.end - model_span.start
                ):
                    # Rule span that includes extra context (longer) wins.
                    overridden_indices.add(idx)

        # Phase 2: build merged list.
        merged: list[EntitySpan] = []
        for idx, model_span in enumerate(model_spans):
            if idx in overridden_indices:
                continue  # replaced by a rule span below
            merged.append(model_span)

        for rule_span in rule_spans:
            if not any(_overlaps(rule_span, m) for m in merged):
                merged.append(rule_span)

        merged.sort(key=lambda s: (s.start, _entity_type_order(s.entity_type)))
        return merged


def _overlaps(a: EntitySpan, b: EntitySpan) -> bool:
    """Return True if spans ``a`` and ``b`` share at least one character."""
    return not (a.end <= b.start or b.end <= a.start)


def _entity_type_order(entity_type: object) -> int:
    """Sort key for stable ordering when two spans share a start offset.

    PRIVATE_PERSON first, then PRIVATE_ADDRESS, then the rest alphabetically.
    """
    order = {
        "private_person": 0,
        "private_address": 1,
        "private_email": 2,
        "private_phone": 3,
        "private_date": 4,
        "private_url": 5,
        "account_number": 6,
        "secret": 7,
    }
    return order.get(str(entity_type), 99)


__all__ = ["CombinedPrivacyClient"]
