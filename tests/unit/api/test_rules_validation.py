"""Unit tests for ``POST /api/v1/rules`` regex safety validation (spec 007, T5).

Verifies the endpoint returns a stable 422 ``unsafe_regex`` for dangerous
patterns and that no rule is persisted. The auth/CSRF dependencies are
overridden the same way as in ``tests/integration/test_rules_api.py``
so that only the validation logic under test is exercised.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from neironir.api.dependencies import get_privacy, get_settings, get_storage
from neironir.auth.dependencies import require_admin_auth, verify_csrf
from neironir.config import Settings
from neironir.main import create_app
from neironir.privacy.client import MockPrivacyFilterClient
from neironir.privacy.rules import RuleBasedDetector
from neironir.storage.local import LocalStorage

TEST_SECRET = "test-secret-for-rules-validation-tests"


def _clear_dynamic_rules() -> None:
    with RuleBasedDetector._dynamic_rules_lock:
        RuleBasedDetector._DYNAMIC_RULES.clear()


@pytest.fixture
def client(tmp_path: Path) -> Generator[tuple[TestClient, Path], None, None]:
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    real_settings = Settings(
        storage_dir=str(storage_dir),
        session_secret=TEST_SECRET,
        admin_user="admin",
        admin_password="pw",
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: LocalStorage(storage_dir)
    app.dependency_overrides[get_privacy] = lambda: MockPrivacyFilterClient()
    app.state.settings = real_settings
    app.dependency_overrides[require_admin_auth] = lambda: {"is_admin": True, "user": "admin"}
    app.dependency_overrides[verify_csrf] = lambda: None

    _clear_dynamic_rules()
    with TestClient(app) as c:
        yield c, storage_dir
    _clear_dynamic_rules()


def _rules_dir(storage: Path) -> Path:
    return storage / "rules"


def _persisted_rule_files(storage: Path) -> list[Path]:
    d = _rules_dir(storage)
    if not d.is_dir():
        return []
    return sorted(d.glob("rule_*.json"))


class TestUnsafeRegex422:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+$",
            r"(a+)+",
            r"(a*)*",
            r"(\w+)\1",
            r"(\w+) \1",
            "x" * 201,  # over the 200-char limit
        ],
        ids=[
            "nested-plus-dollar",
            "nested-plus",
            "nested-star",
            "backref-g",
            "backref-space",
            "too-long",
        ],
    )
    def test_unsafe_pattern_returns_422_with_code(
        self, client: tuple[TestClient, Path], pattern: str
    ) -> None:
        c, storage = client
        r = c.post(
            "/api/v1/rules",
            json={"entity_type": "private_phone", "pattern": pattern},
        )
        assert r.status_code == 422, r.text
        body = r.json()
        # Stable machine-readable code so clients can distinguish
        # "unsafe regex" from "syntactically invalid regex".
        detail = body.get("detail")
        assert isinstance(detail, dict), f"expected dict detail, got {type(detail)}"
        assert detail.get("code") == "unsafe_regex"
        assert detail.get("reason") in (
            "nested_quantifier",
            "backreference",
            "pattern_too_long",
        )
        # Nothing persisted for an unsafe rule.
        assert _persisted_rule_files(storage) == []
        with RuleBasedDetector._dynamic_rules_lock:
            assert RuleBasedDetector._DYNAMIC_RULES == []


class TestSafePatternStillWorks:
    def test_safe_pattern_is_accepted_and_active(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, storage = client
        r = c.post(
            "/api/v1/rules",
            json={
                "entity_type": "private_phone",
                "pattern": r"ZZZ\d{8}",
                "description": "T5 sanity",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        # Rule persisted
        files = _persisted_rule_files(storage)
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["pattern"] == r"ZZZ\d{8}"
        # Rule is active in the detector
        with RuleBasedDetector._dynamic_rules_lock:
            patterns = [p.pattern for _, p, _ in RuleBasedDetector._DYNAMIC_RULES]
        assert r"ZZZ\d{8}" in patterns

    def test_safe_alternation_pattern_accepted(self, client: tuple[TestClient, Path]) -> None:
        c, _ = client
        r = c.post(
            "/api/v1/rules",
            json={
                "entity_type": "private_phone",
                "pattern": r"(?:foo|bar)\d{2}",
            },
        )
        assert r.status_code == 200, r.text


class TestInvalidRegexNotConfusedWithUnsafe:
    def test_unbalanced_paren_is_422_but_not_unsafe_code(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, _ = client
        r = c.post(
            "/api/v1/rules",
            json={"entity_type": "private_phone", "pattern": "([unclosed"},
        )
        assert r.status_code == 422
        # This is a compile error, not an unsafe_regex rejection —
        # the detail should NOT carry the `unsafe_regex` code.
        detail = r.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") != "unsafe_regex"
        else:
            assert "Invalid regex pattern" in detail
