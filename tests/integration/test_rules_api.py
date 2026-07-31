"""HTTP-level integration tests for the rules API."""

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

TEST_SECRET = "test-secret-for-rules-api-tests"


def _clear_dynamic_rules() -> None:
    """Reset the class-level dynamic rule list (test isolation)."""
    with RuleBasedDetector._dynamic_rules_lock:  # noqa: SLF001
        RuleBasedDetector._DYNAMIC_RULES.clear()  # noqa: SLF001


@pytest.fixture
def client(tmp_path: Path) -> Generator[tuple[TestClient, Path], None, None]:
    """Build a TestClient with auth bypassed and a temp storage dir."""
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


class TestManualRules:
    def test_add_manual_rule_success(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, storage = client
        r = c.post(
            "/api/v1/rules",
            params={
                "entity_type": "private_phone",
                "pattern": r"ZZZ\d{8}",
                "description": "synthetic test rule",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["entity_type"] == "private_phone"

        rule_file = storage / "rules" / f"rule_{body['rule_id']}.json"
        assert rule_file.is_file()
        data = json.loads(rule_file.read_text(encoding="utf-8"))
        assert data["entity_type"] == "private_phone"
        assert data["pattern"] == r"ZZZ\d{8}"
        assert data["status"] == "approved"

    def test_add_manual_rule_hot_reloads_detector(
        self, client: tuple[TestClient, Path]
    ) -> None:
        """A manually added rule must be active immediately, matching the
        behaviour of the approve endpoint."""
        c, _storage = client
        r = c.post(
            "/api/v1/rules",
            params={"entity_type": "private_phone", "pattern": r"ZZZ\d{8}"},
        )
        assert r.status_code == 200, r.text

        with RuleBasedDetector._dynamic_rules_lock:  # noqa: SLF001
            patterns = [p.pattern for _, p, _ in RuleBasedDetector._DYNAMIC_RULES]  # noqa: SLF001
        assert r"ZZZ\d{8}" in patterns

    def test_add_manual_rule_invalid_entity_type(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, storage = client
        r = c.post(
            "/api/v1/rules",
            params={"entity_type": "not_a_type", "pattern": r"\d{10}"},
        )
        assert r.status_code == 422
        # Nothing must be persisted for an invalid rule.
        rules_dir = storage / "rules"
        assert not rules_dir.is_dir() or list(rules_dir.glob("rule_*.json")) == []

    def test_add_manual_rule_invalid_regex(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, _storage = client
        r = c.post(
            "/api/v1/rules",
            params={"entity_type": "private_phone", "pattern": "([unclosed"},
        )
        assert r.status_code == 422

    def test_add_manual_rule_pattern_too_short(
        self, client: tuple[TestClient, Path]
    ) -> None:
        c, _storage = client
        r = c.post(
            "/api/v1/rules",
            params={"entity_type": "private_phone", "pattern": "ab"},
        )
        assert r.status_code == 422
