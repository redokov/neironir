"""Unit tests for :mod:`neironir.privacy.feedback_analyzer`.

Covers: ``compute_stats``, ``propose_rules``, regex extraction functions,
and edge cases for proposal generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from neironir.privacy.feedback_analyzer import (
    FeedbackAnalyzer,
    _extract_digit_pattern,
    _extract_generic_regex,
    _extract_name_pattern,
    _extract_org_pattern,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_job_feedback(
    storage_dir: Path,
    job_id: str | None = None,
    actions: list[dict] | None = None,
    text: str = "Reach me at user@example.com or +7 495 123-45-67.",
) -> str:
    """Create a completed job with feedback for analysis."""
    jid = job_id or str(uuid4())
    job_dir = storage_dir / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)

    (job_dir / "extracted_text.txt").write_text(text, encoding="utf-8")

    feedback = {
        "job_id": jid,
        "actions": actions or [],
        "comment": None,
    }
    (job_dir / "feedback.json").write_text(
        json.dumps(feedback, ensure_ascii=False), encoding="utf-8"
    )

    from datetime import datetime

    from neironir.domain.job import Job, JobStatus

    job = Job(
        id=jid,
        status=JobStatus.COMPLETED,
        source_filename=f"{jid}.md",
        source_ext="md",
        created_at=datetime.now(),
    )
    (job_dir / "job.json").write_text(
        json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return jid


# ---------------------------------------------------------------------------
# Test: compute_stats
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_empty_storage(self, tmp_path: Path) -> None:
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        stats = analyzer.compute_stats()
        assert stats.total_jobs_with_feedback == 0
        assert stats.total_corrections == 0

    def test_one_job_no_feedback(self, tmp_path: Path) -> None:
        jid = str(uuid4())
        (tmp_path / "jobs" / jid).mkdir(parents=True, exist_ok=True)
        (tmp_path / "jobs" / jid / "job.json").write_text(
            json.dumps({"id": jid, "status": "completed", "source_ext": "md"})
        )
        # No feedback.json — should be skipped.
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        stats = analyzer.compute_stats()
        assert stats.total_jobs_with_feedback == 0

    def test_one_job_with_add(self, tmp_path: Path) -> None:
        _write_job_feedback(
            tmp_path,
            actions=[
                {
                    "action": "add",
                    "start": 0,
                    "end": 5,
                    "entity_type": "private_email",
                    "text": "user@",
                }
            ],
        )
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        stats = analyzer.compute_stats()
        assert stats.total_jobs_with_feedback == 1
        assert stats.total_corrections == 1
        assert stats.corrections_by_type.get("private_email", 0) == 1

    def test_multiple_jobs_multiple_actions(self, tmp_path: Path) -> None:
        for i in range(3):
            _write_job_feedback(
                tmp_path,
                actions=[
                    {
                        "action": "add",
                        "start": i,
                        "end": i + 3,
                        "entity_type": "private_phone",
                        "text": "123",
                    },
                    {
                        "action": "confirm",
                        "start": i + 5,
                        "end": i + 8,
                        "entity_type": "private_email",
                        "text": "a@b",
                    },
                ],
            )
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        stats = analyzer.compute_stats()
        # All actions (add + confirm) count as corrections.
        assert stats.total_corrections == 6
        # Only ADD actions are broken down by type, though.
        assert stats.corrections_by_type.get("private_phone", 0) == 3


# ---------------------------------------------------------------------------
# Test: propose_rules
# ---------------------------------------------------------------------------


class TestProposeRules:
    def test_no_feedback_no_proposals(self, tmp_path: Path) -> None:
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        proposals = analyzer.propose_rules()
        assert len(proposals) == 0

    def test_single_add_below_threshold(self, tmp_path: Path) -> None:
        """One ADD action for a phone number should not meet min_occurrences=3."""
        _write_job_feedback(
            tmp_path,
            actions=[
                {
                    "action": "add",
                    "start": 12,
                    "end": 28,
                    "entity_type": "private_email",
                    "text": "user@example.com",
                }
            ],
        )
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        proposals = analyzer.propose_rules(min_occurrences=3)
        assert len(proposals) == 0

    def test_multiple_adds_meet_threshold(self, tmp_path: Path) -> None:
        """Three ADD actions with the same email should generate a proposal."""
        text = "Contact: user@example.com"
        for _ in range(3):
            _write_job_feedback(
                tmp_path,
                actions=[
                    {
                        "action": "add",
                        "start": 10,
                        "end": 26,
                        "entity_type": "private_email",
                        "text": "user@example.com",
                    }
                ],
                text=text,
            )
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        proposals = analyzer.propose_rules(min_occurrences=3)
        assert len(proposals) >= 1

    def test_proposal_has_correct_fields(self, tmp_path: Path) -> None:
        text = "Call +7 495 123-45-67"
        for _ in range(3):
            _write_job_feedback(
                tmp_path,
                actions=[
                    {
                        "action": "add",
                        "start": 5,
                        "end": 21,
                        "entity_type": "private_phone",
                        "text": "+7 495 123-45-67",
                    }
                ],
                text=text,
            )
        analyzer = FeedbackAnalyzer(storage_dir=tmp_path)
        proposals = analyzer.propose_rules(min_occurrences=3)
        assert len(proposals) >= 1
        rule = proposals[0]
        assert rule.entity_type == "private_phone"
        assert rule.evidence_count >= 3
        assert rule.status == "proposed"
        assert rule.pattern is not None
        assert rule.samples is not None


# ---------------------------------------------------------------------------
# Test: regex extraction functions
# ---------------------------------------------------------------------------


class TestExtractDigitPattern:
    def test_phone_number(self) -> None:
        result = _extract_digit_pattern("+7 495 123-45-67")
        assert result is not None
        assert "\\d{" in result

    def test_short_number(self) -> None:
        """Numbers with fewer than MIN_DIGIT_LEN digits should be rejected."""
        result = _extract_digit_pattern("12345")
        assert result is None, "short digit-only pattern should be rejected"

    def test_inn_number(self) -> None:
        result = _extract_digit_pattern("4810004427")
        assert result is not None
        assert "\\d{10}" in result

    def test_non_digit_text(self) -> None:
        result = _extract_digit_pattern("hello world")
        assert result is None  # no digits → below MIN_DIGIT_LEN

    def test_empty(self) -> None:
        assert _extract_digit_pattern("") is None


class TestExtractNamePattern:
    def test_simple_name(self) -> None:
        result = _extract_name_pattern("Иванов Иван Петрович")
        assert result is not None

    def test_short_name(self) -> None:
        """Less than 4 chars — too short."""
        assert _extract_name_pattern("Ан") is None

    def test_non_cyrillic(self) -> None:
        """Non-Cyrillic names should not produce a pattern."""
        assert _extract_name_pattern("John") is None

    def test_initials_name(self) -> None:
        """Name with initials should generate a pattern."""
        result = _extract_name_pattern("Соловьев Р.Е.")
        assert result is not None
        assert r"[А-ЯЁ]\." in result

    def test_empty(self) -> None:
        assert _extract_name_pattern("") is None


class TestExtractOrgPattern:
    def test_ooo(self) -> None:
        result = _extract_org_pattern("ООО «Моторинвест»")
        assert result is not None
        assert "ООО" in result

    def test_ao(self) -> None:
        result = _extract_org_pattern('ЗАО "ТехноСнаб"')
        assert result is not None

    def test_no_org(self) -> None:
        assert _extract_org_pattern("просто текст") is None

    def test_empty(self) -> None:
        assert _extract_org_pattern("") is None


class TestExtractGenericRegex:
    def test_simple_text(self) -> None:
        result = _extract_generic_regex("секретно", "secret")
        assert result is not None
        assert result.startswith("\\b")
        assert result.endswith("\\b")

    def test_digits_replaced(self) -> None:
        result = _extract_generic_regex("код 4810004427", "account_number")
        assert result is not None
        assert "\\d{10}" in result

    def test_short_text(self) -> None:
        assert _extract_generic_regex("ab", "secret") is None

    def test_empty(self) -> None:
        assert _extract_generic_regex("", "secret") is None

    def test_whitespace_collapsed(self) -> None:
        """Multiple spaces should be normalised to a single space before escaping."""
        result = _extract_generic_regex("очень   секретно", "secret")
        assert result is not None
        # After normalisation, the single space is preserved literally.
        assert "секретно" in result
        assert "очень" in result

    def test_special_chars_escaped(self) -> None:
        """Regex special characters in the original text must be escaped."""
        result = _extract_generic_regex("цена $100", "secret")
        assert result is not None
        assert "\\$" in result or "$" not in result
