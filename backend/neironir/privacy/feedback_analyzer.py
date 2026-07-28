"""Analyse accumulated user feedback and propose new detection rules.

Each job with feedback stores its corrections in ``feedback.json``. The
:class:`FeedbackAnalyzer` scans all such files, clusters ADD actions by
text pattern, and generates proposed regex rules when the same pattern
appears frequently enough.

Phase 2 of the feedback-improvement loop (see ``docs/architecture.md``):

    User corrections → FeedbackAnalyzer → Proposed rules
                                         → Dictionary entries
                                         → Training data (Phase 3)
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ProposedRule:
    """A rule candidate generated from user feedback.

    When approved, the rule is compiled into a regex pattern and added
    to the :class:`~neironir.privacy.rules.RuleBasedDetector` at the
    next warm-reload.
    """

    # Entity type this rule targets (e.g. ``"account_number"``, ``"private_person"``)
    entity_type: str
    # The proposed regex pattern string
    pattern: str
    # Number of ADD actions that contributed to this proposal
    evidence_count: int
    # Heuristic confidence (0.0 — 1.0); higher is more reliable
    confidence: float
    # Status: ``"proposed"`` | ``"approved"`` | ``"rejected"``
    status: str = "proposed"
    # Human-readable description
    description: str = ""
    # Sample text snippets that triggered this rule
    samples: list[str] = field(default_factory=list)
    # When the action was first observed
    first_seen: str | None = None
    # Unique identifier (set by storage)
    rule_id: str | None = None


@dataclass
class FeedbackStats:
    """Aggregated statistics across all feedback jobs."""

    total_jobs_with_feedback: int = 0
    total_corrections: int = 0
    corrections_by_type: dict[str, int] = field(default_factory=dict)
    missed_types: dict[str, int] = field(default_factory=dict)  # ADD by type
    false_positives: dict[str, int] = field(default_factory=dict)  # REJECT by type


# ---------------------------------------------------------------------------
# Pattern extraction helpers
# ---------------------------------------------------------------------------


# Heuristic patterns for common Russian PII — the analyzer uses these to
# recognise structure in user-provided text snippets.
_PATTERN_TEMPLATES: ClassVar[dict[str, list[str]]] = {
    "account_number": [
        # ИНН: 10 or 12 digits
        r"\d{10}(?:\d{2})?",
        # КПП: 9 digits
        r"\d{9}",
        # ОГРН: 13 or 15 digits
        r"\d{13}(?:\d{2})?",
    ],
    "private_person": [
        # Full name: Фамилия Имя Отчество
        r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+",
        # Surname + initials
        r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.",
        # Initials + surname
        r"[А-ЯЁ]\.\s*[А-ЯЁ]\.\s+[А-ЯЁ][а-яё]+",
    ],
    "private_address": [
        # "г. Город, улица, д. N"
        r"\d{6},\s*\w+[\.\s]\s*\w+",
    ],
}


def _extract_digit_pattern(text: str) -> str | None:
    """Replace variable digit runs with ``\\d{N}`` quantifiers.

    ``ИНН 4810004427`` → ``\\bИНН\\s*\\d{10}\\b``
    ``+7 (495) 123-45-67`` → ``\\+7\\s*\\(\\d{3}\\)\\s*\\d{3}-\\d{2}-\\d{2}``
    """
    # Remove excess whitespace
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return None

    # Tokenise: split into digit-runs and non-digit-runs
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isdigit():
            # Count consecutive digits
            end = pos
            while end < len(text) and text[end].isdigit():
                end += 1
            length = end - pos
            tokens.append(f"\\d{{{length}}}")
            pos = end
        else:
            # Escape regex special characters
            ch = text[pos]
            if ch in r"\^$.|?*+()[]{}":
                tokens.append("\\" + ch)
            elif ch in " \t":
                tokens.append(r"\s*")
            else:
                tokens.append(re.escape(ch))
            pos += 1

    pattern = "".join(tokens)
    # Wrap in word boundaries for safety
    return f"\\b{pattern}\\b"


def _extract_name_pattern(text: str) -> str | None:
    """Generate a flexible pattern for a person name.

    ``Ханин Андрей Анатольевич`` → ``[А-ЯЁ][а-яё]+\\s+[А-ЯЁ][а-яё]+\\s+[А-ЯЁ][а-яё]+``
    ``Соловьев Р.Е.`` → ``[А-ЯЁ][а-яё]+\\s+[А-ЯЁ]\\.\\s*[А-ЯЁ]\\.``
    """
    text = re.sub(r"\s+", " ", text.strip())
    parts = text.split()

    if len(parts) == 3 and all(p[0].isupper() for p in parts):
        # Full name → generic pattern
        return r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+"

    if len(parts) == 2 and parts[0][0].isupper() and "." in parts[1]:
        return r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\."

    return None


def _extract_org_pattern(text: str) -> str | None:
    """Generate a pattern for an organisation name.

    ``ООО «Моторинвест»`` → ``ООО\\s*«[^»]+»``
    """
    match = re.match(r"(ООО|АО|ЗАО|ОАО|ПАО|ИП)\s*[«\"(]([^»\")]+)[»\")]", text)
    if match:
        legal_form = re.escape(match.group(1))
        return rf"{legal_form}\s*«[^»]+»"
    return None


def _extract_generic_regex(text: str, entity_type: str) -> str | None:
    """Generate a conservative regex pattern for ``text``.

    This is the catch-all: escape any literal text, then collapse
    whitespace runs and variable-length digit runs to quantifiers.
    """
    text = re.sub(r"\s+", " ", text.strip())
    if not text or len(text) < 4:
        return None

    escaped = re.escape(text)
    # Replace digit runs with \d{N}
    escaped = re.sub(r"\\d\{(\\d+)\}", lambda m: f"\\d{{{m.group(1)}}}", escaped)
    # Collapse whitespace → \\s+
    escaped = re.sub(r"\\ \*", r"\\s+", escaped)
    # Replace literal 3+ consecutive digits with variable quantifier
    escaped = re.sub(r"\d{3,}", lambda m: f"\\d{{{len(m.group())}}}", escaped)

    return f"\\b{escaped}\\b"


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------


class FeedbackAnalyzer:
    """Scan feedback data and produce proposed rules and statistics.

    Usage::

        analyzer = FeedbackAnalyzer(storage_dir=Path("./storage"))
        stats = analyzer.compute_stats()
        proposals = analyzer.propose_rules(min_occurrences=3)
        for rule in proposals:
            print(rule.pattern, rule.entity_type, rule.evidence_count)
    """

    # Minimum occurrences to generate a proposal
    MIN_OCCURRENCES: ClassVar[int] = 3
    # Minimum confidence to auto-approve (otherwise requires manual review)
    AUTO_APPROVE_CONFIDENCE: ClassVar[float] = 0.95

    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = Path(storage_dir)
        self._jobs_dir = self._storage_dir / "jobs"

    # -- Public API -------------------------------------------------------

    def compute_stats(self) -> FeedbackStats:
        """Aggregate feedback across all jobs with ``feedback.json``."""
        stats = FeedbackStats()

        for fb_path in self._iter_feedback_files():
            try:
                feedback = json.loads(fb_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("skipping malformed feedback %s: %s", fb_path, exc)
                continue

            stats.total_jobs_with_feedback += 1
            actions = feedback.get("actions", [])
            stats.total_corrections += len(actions)

            for action in actions:
                atype = action.get("action")
                etype = action.get("entity_type", "unknown")
                if atype == "add":
                    stats.missed_types[etype] = stats.missed_types.get(etype, 0) + 1
                    stats.corrections_by_type[etype] = stats.corrections_by_type.get(etype, 0) + 1
                elif atype == "reject":
                    stats.false_positives[etype] = stats.false_positives.get(etype, 0) + 1

        return stats

    def propose_rules(
        self,
        min_occurrences: int = MIN_OCCURRENCES,
    ) -> list[ProposedRule]:
        """Analyse feedback ADD actions and propose candidate rules.

        Groups ADD actions by (entity_type, normalised pattern).  When a
        group reaches ``min_occurrences`` items a :class:`ProposedRule`
        is emitted.

        Args:
            min_occurrences: Minimum ADD actions with the same pattern
                to generate a proposal.

        Returns:
            A list of proposed rules, sorted by evidence count descending.
        """
        # Collect all ADD actions grouped by (entity_type, pattern_key).
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

        for fb_path in self._iter_feedback_files():
            try:
                feedback = json.loads(fb_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            for action in feedback.get("actions", []):
                if action.get("action") != "add":
                    continue
                text = action.get("text", "").strip()
                etype = action.get("entity_type", "")
                if not text or not etype:
                    continue

                pattern_key = self._make_pattern_key(text, etype)
                if pattern_key:
                    groups[(etype, pattern_key)].append(action)

        # Generate proposals from groups that meet the threshold.
        proposals: list[ProposedRule] = []
        for (etype, pattern_key), actions in groups.items():
            if len(actions) < min_occurrences:
                continue

            samples = [a.get("text", "") for a in actions[:5]]

            # Determine confidence: exact-digit patterns (ИНН, ОГРН…)
            # have higher confidence than open-ended text patterns.
            confidence = 0.7
            has_digit_pattern = bool(re.search(r"\\d\{\d+\}", pattern_key))
            has_prefix = any(
                prefix in pattern_key
                for prefix in ["ИНН", "КПП", "ОГРН", "БИК", "р/с", "к/с"]
            )
            if has_prefix and has_digit_pattern:
                confidence = 0.95
            elif has_digit_pattern:
                confidence = 0.85

            description = self._make_description(etype, actions, pattern_key)

            proposals.append(
                ProposedRule(
                    entity_type=etype,
                    pattern=pattern_key,
                    evidence_count=len(actions),
                    confidence=confidence,
                    description=description,
                    samples=samples,
                )
            )

        proposals.sort(key=lambda r: r.evidence_count, reverse=True)
        return proposals

    # -- Internal helpers -------------------------------------------------

    def _iter_feedback_files(self) -> list[Path]:
        """Yield all ``feedback.json`` paths in the storage directory."""
        jobs_dir = self._jobs_dir
        if not jobs_dir.is_dir():
            return []
        return sorted(jobs_dir.glob("*/feedback.json"))

    def _make_pattern_key(self, text: str, entity_type: str) -> str | None:
        """Normalise ``text`` into a canonical pattern key.

        Two ADD actions on different text that share the same structure
        (e.g. two different INN numbers) map to the same key and are
        grouped together.
        """
        text = re.sub(r"\s+", " ", text.strip())
        if not text or len(text) < 4:
            return None

        # Try specialised extractors first.
        for extractor in (_extract_digit_pattern, _extract_name_pattern, _extract_org_pattern):
            result = extractor(text)
            if result:
                return result

        # Fallback: generic regex.
        return _extract_generic_regex(text, entity_type)

    @staticmethod
    def _make_description(
        entity_type: str,
        actions: list[dict],
        pattern: str,
    ) -> str:
        """Generate a human-readable description for a proposal."""
        samples = [a.get("text", "") for a in actions[:3]]
        sample_str = ", ".join(f'"{s}"' for s in samples)
        return (
            f"Detected {len(actions)}x ADD for type '{entity_type}': "
            f"e.g. {sample_str}"
        )


__all__ = [
    "FeedbackAnalyzer",
    "FeedbackStats",
    "ProposedRule",
]
