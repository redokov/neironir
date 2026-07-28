"""Feedback data models for the review-and-correct loop.

Each anonymisation job can receive feedback from a human reviewer who
verifies the detected PII spans and adds any that were missed. The
accumulated feedback drives two improvement loops:

* **Phase 2 — Auto-rules**: regex patterns and dictionary entries are
  generated from frequent ADD corrections.
* **Phase 3 — Model fine-tuning**: corrected annotations are converted
  into the OPF training format (``.jsonl``) for ``opf train``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class FeedbackAction(str, Enum):
    """What the user did with a detected/potential entity."""

    # The user explicitly confirmed that the model's span is correct.
    CONFIRM = "confirm"
    # The user flagged the span as a false positive (should not have been
    # replaced).
    REJECT = "reject"
    # The user added a span that the model/rules missed.
    ADD = "add"


@dataclass
class FeedbackItem:
    """A single user action on one entity span.

    For ``CONFIRM`` and ``REJECT`` actions the span matches the original
    model/rule span. For ``ADD`` the user drew a new span on the text.
    """

    action: FeedbackAction
    start: int
    end: int
    entity_type: str
    text: str

    # Optional — set when the action refers to an existing span
    # (confirm / reject).  ``None`` for brand-new ADD spans.
    original_span_index: int | None = None


@dataclass
class AnnotationFeedback:
    """The complete set of user corrections for a single document.

    Stored as ``feedback.json`` in the job directory alongside the
    original annotations.
    """

    job_id: UUID
    timestamp: datetime = field(default_factory=datetime.now)
    actions: list[FeedbackItem] = field(default_factory=list)
    comment: str | None = None


__all__ = [
    "AnnotationFeedback",
    "FeedbackAction",
    "FeedbackItem",
]
