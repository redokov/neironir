"""Unit tests for the conservative ReDoS guard (spec 007, T5 / TD-004).

Covers :func:`neironir.privacy.regex_guard.validate_dynamic_pattern`
directly and exercises the loader's skip-with-warning behaviour via
:func:`RuleBasedDetector.load_dynamic_rules` on a temp storage dir.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from neironir.privacy.regex_guard import (
    DEFAULT_DYNAMIC_RULE_MAX_LENGTH,
    REASON_BACKREFERENCE,
    REASON_NESTED_QUANTIFIER,
    REASON_PATTERN_TOO_LONG,
    UNSAFE_REJECTION,
    DynamicPatternPolicy,
    DynamicRuleRejection,
    validate_dynamic_pattern,
)
from neironir.privacy.rules import RuleBasedDetector


@pytest.fixture(autouse=True)
def _clear_dynamic_rules() -> Iterator[None]:
    """Isolate the class-level dynamic rule list per test."""
    with RuleBasedDetector._dynamic_rules_lock:
        RuleBasedDetector._DYNAMIC_RULES.clear()
    yield
    with RuleBasedDetector._dynamic_rules_lock:
        RuleBasedDetector._DYNAMIC_RULES.clear()


# ---------------------------------------------------------------------------
# Direct validator tests
# ---------------------------------------------------------------------------


class TestLengthLimit:
    def test_pattern_at_limit_is_accepted(self) -> None:
        # 200-char pattern that is syntactically safe.
        pattern = "a" * 198 + "+"  # length 199, safe
        assert len(pattern) == 199
        assert validate_dynamic_pattern(pattern) is None

    def test_pattern_over_limit_is_rejected(self) -> None:
        pattern = "a" * (DEFAULT_DYNAMIC_RULE_MAX_LENGTH + 1)
        result = validate_dynamic_pattern(pattern)
        assert result is not None
        assert result.reason == REASON_PATTERN_TOO_LONG
        assert result.offset == -1

    def test_default_limit_matches_design_constant(self) -> None:
        # Spec design TD-004 mandates 200 for T5 scope.
        assert DEFAULT_DYNAMIC_RULE_MAX_LENGTH == 200


class TestNestedQuantifier:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+$",  # classic
            r"(a+)+",
            r"(a*)*",
            r"(a+)?",
            r"((a+)+)",
            r"(a{2,})*",
            r"(\w+){2}",
            r"(ab+)+",
            r"(a+){2,3}",
        ],
        ids=[
            "a-plus-plus-dollar",
            "a-plus-plus",
            "a-star-star",
            "a-plus-question",
            "double-nested",
            "braces-outer",
            "word-braces",
            "ab-plus-plus",
            "plus-braces",
        ],
    )
    def test_dangerous_patterns_rejected(self, pattern: str) -> None:
        result = validate_dynamic_pattern(pattern)
        assert result is not None, f"pattern {pattern!r} should be rejected"
        assert result.reason == REASON_NESTED_QUANTIFIER

    @pytest.mark.parametrize(
        "pattern",
        [
            r"ZZZ\d{8}",  # existing integration test pattern
            r"\d{20}",
            r"\d{1,2}\s+\d{4}",
            r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.",
            r"\w{5,10}",
            r"(?:foo|bar)\d{2}",
            r"\b\d{9}\b",
            r"р/с\s*[:/]?\s*(\d{20})",
        ],
        ids=[
            "zzz-8digits",
            "20digits",
            "day-year",
            "surname-initials",
            "word-bounded",
            "alternation-digit2",
            "bik-style",
            "bank-account",
        ],
    )
    def test_safe_patterns_accepted(self, pattern: str) -> None:
        assert validate_dynamic_pattern(pattern) is None, f"pattern {pattern!r} should be accepted"


class TestBackreference:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(\w+) \1",  # word repeated via \1
            r"(foo)\2",  # \2
            r"(\w+) \9",  # \9 (highest decimal)
            r"\g<1>(\w+)",  # \g<name>
            r"(\w+)\g<2>",
        ],
        ids=["backref-1", "backref-2", "backref-9", "g-bracket-1", "g-bracket-2"],
    )
    def test_backreferences_rejected(self, pattern: str) -> None:
        result = validate_dynamic_pattern(pattern)
        assert result is not None
        assert result.reason == REASON_BACKREFERENCE

    @pytest.mark.parametrize(
        "pattern",
        [
            r"\d+",
            r"\b\w+\b",
            r"\s*\d",
            r"[а-я]+\b",
        ],
        ids=["digit-quantifier", "word-boundaries", "space-digit", "cyrillic-b"],
    )
    def test_non_backref_escapes_accepted(self, pattern: str) -> None:
        assert validate_dynamic_pattern(pattern) is None


# ---------------------------------------------------------------------------
# Loader-level: unsafe persisted rules are skipped with warning
# ---------------------------------------------------------------------------


def _write_rule(storage: Path, rule_id: str, pattern: str, entity_type: str) -> Path:
    rules_dir = storage / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    fpath = rules_dir / f"rule_{rule_id}.json"
    fpath.write_text(
        json.dumps(
            {
                "rule_id": rule_id,
                "entity_type": entity_type,
                "pattern": pattern,
                "evidence_count": 1,
                "confidence": 0.5,
                "status": "approved",
                "description": "test",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return fpath


class TestLoaderSkipsUnsafe:
    def test_nested_quantifier_rule_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_rule(tmp_path, "redos1", r"(a+)+$", "private_phone")

        with caplog.at_level(logging.WARNING, logger="neironir.privacy.rules"):
            count = RuleBasedDetector.load_dynamic_rules(tmp_path)

        assert count == 0
        with RuleBasedDetector._dynamic_rules_lock:
            assert RuleBasedDetector._DYNAMIC_RULES == []

        # The warning must log the rule id and reason — never the pattern.
        warnings = [r for r in caplog.records if r.name == "neironir.privacy.rules"]
        assert warnings, "expected a warning to be logged"
        msg = warnings[0].getMessage()
        assert "redos1" in msg
        assert "nested_quantifier" in msg
        # The pattern text itself must NOT appear in the log message.
        assert "(a+)+" not in msg

    def test_backreference_rule_is_skipped(self, tmp_path: Path) -> None:
        _write_rule(tmp_path, "backref1", r"(\w+) \1", "private_person")
        count = RuleBasedDetector.load_dynamic_rules(tmp_path)
        assert count == 0

    def test_over_long_rule_is_skipped(self, tmp_path: Path) -> None:
        long_pattern = "a" * (DEFAULT_DYNAMIC_RULE_MAX_LENGTH + 10)
        _write_rule(tmp_path, "long1", long_pattern, "private_phone")
        count = RuleBasedDetector.load_dynamic_rules(tmp_path)
        assert count == 0

    def test_safe_rule_is_loaded_and_detects(self, tmp_path: Path) -> None:
        _write_rule(tmp_path, "ok1", r"ZZZ\d{8}", "private_phone")
        count = RuleBasedDetector.load_dynamic_rules(tmp_path)
        assert count == 1

        detector = RuleBasedDetector()
        spans = detector.detect("code ZZZ12345678 here")
        assert any(s.entity_type.value == "private_phone" for s in spans)

    def test_mixed_rules_only_safe_are_loaded(self, tmp_path: Path) -> None:
        _write_rule(tmp_path, "bad1", r"(a+)+", "private_phone")
        _write_rule(tmp_path, "ok1", r"QQQ\d{4}", "private_phone")
        count = RuleBasedDetector.load_dynamic_rules(tmp_path)
        assert count == 1
        with RuleBasedDetector._dynamic_rules_lock:
            names = [n for _, _, n in RuleBasedDetector._DYNAMIC_RULES]
        assert "dynamic:rule_ok1" in names
        assert "dynamic:rule_bad1" not in names


# ---------------------------------------------------------------------------
# Public API surface sanity
# ---------------------------------------------------------------------------


class TestPublicApi:
    def test_rejection_dataclass_fields(self) -> None:
        r = DynamicRuleRejection(reason="x", detail="y", offset=3)
        assert r.reason == "x"
        assert r.detail == "y"
        assert r.offset == 3

    def test_policy_class_matches_function(self) -> None:
        safe = r"ZZZ\d{8}"
        unsafe = r"(a+)+"
        assert DynamicPatternPolicy.validate(safe) is None
        assert DynamicPatternPolicy.validate(unsafe) is not None

    def test_unsafe_rejection_code_constant(self) -> None:
        assert UNSAFE_REJECTION == "unsafe_regex"

    def test_rejection_reasons_are_stable_strings(self) -> None:
        assert REASON_PATTERN_TOO_LONG == "pattern_too_long"
        assert REASON_NESTED_QUANTIFIER == "nested_quantifier"
        assert REASON_BACKREFERENCE == "backreference"
