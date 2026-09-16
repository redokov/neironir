"""Unit tests for :mod:`neironir.auth.api_key` (M2M Bearer keys).

Red stage (TDD): these tests fail with ``NotImplementedError`` until
T08 implements the helpers. They encode the contracts from
``design.md`` §3.1:

* ``parse_api_keys`` — comma-separated, strip, drop empties;
  empty input → empty set (M2M disabled, NFR-004);
* ``is_valid_api_key`` — exact membership, empty token / empty key
  set → False (no accidental pass-through);
* ``get_bearer_token`` — ``""`` sentinel for a malformed scheme,
  ``None`` only when the header is absent entirely.
"""

from __future__ import annotations

import pytest
from fastapi import Request
from neironir.auth.api_key import (
    get_bearer_token,
    is_valid_api_key,
    parse_api_keys,
)
from neironir.config import Settings


def _request(authorization: str | None) -> Request:
    """Build a minimal Starlette request with the given Authorization header.

    ``authorization=None`` means the header is absent from the scope.
    """
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)  # type: ignore[arg-type]


class TestParseApiKeys:
    def test_empty_string_yields_empty_set(self) -> None:
        assert parse_api_keys("") == frozenset()

    def test_single_key(self) -> None:
        assert parse_api_keys("k1") == frozenset({"k1"})

    def test_multiple_keys_are_split(self) -> None:
        assert parse_api_keys("k1,k2") == frozenset({"k1", "k2"})

    def test_whitespace_is_stripped(self) -> None:
        assert parse_api_keys("k1, k2") == frozenset({"k1", "k2"})
        assert parse_api_keys("  k1 , k2  ") == frozenset({"k1", "k2"})

    def test_empty_entries_are_dropped(self) -> None:
        assert parse_api_keys("k1,, k2") == frozenset({"k1", "k2"})

    def test_only_separators_and_spaces_yields_empty_set(self) -> None:
        assert parse_api_keys(" , ") == frozenset()
        assert parse_api_keys(",,") == frozenset()

    def test_returns_frozenset_type(self) -> None:
        result = parse_api_keys("k1")
        assert isinstance(result, frozenset)


class TestIsValidApiKey:
    KEYS = frozenset({"alpha-secret", "beta-secret"})

    def test_exact_match_is_valid(self) -> None:
        assert is_valid_api_key("alpha-secret", self.KEYS) is True

    def test_other_key_is_invalid(self) -> None:
        assert is_valid_api_key("gamma-secret", self.KEYS) is False

    def test_empty_token_is_invalid(self) -> None:
        assert is_valid_api_key("", self.KEYS) is False

    def test_empty_key_set_is_invalid(self) -> None:
        """Empty set = M2M disabled (NFR-004) — nothing may pass."""
        assert is_valid_api_key("alpha-secret", frozenset()) is False

    def test_prefix_of_a_key_is_invalid(self) -> None:
        """A key must match exactly — no substring/prefix leniency."""
        assert is_valid_api_key("alpha", self.KEYS) is False
        assert is_valid_api_key("alpha-secret-extra", self.KEYS) is False


class TestGetBearerToken:
    def test_valid_bearer_header(self) -> None:
        request = _request("Bearer tok")
        assert get_bearer_token(request) == "tok"

    def test_basic_scheme_returns_empty_sentinel(self) -> None:
        """A non-Bearer scheme is malformed → sentinel ``""`` (401 path)."""
        request = _request("Basic xyz")
        assert get_bearer_token(request) == ""

    def test_bare_bearer_returns_empty_sentinel(self) -> None:
        request = _request("Bearer")
        assert get_bearer_token(request) == ""

    def test_bearer_with_empty_token_returns_empty_sentinel(self) -> None:
        request = _request("Bearer ")
        assert get_bearer_token(request) == ""

    def test_missing_header_returns_none(self) -> None:
        """``None`` means: no Authorization header at all → pass-through."""
        request = _request(None)
        assert get_bearer_token(request) is None


class TestSettingsApiKeySet:
    def test_settings_property_parses_comma_separated_keys(self) -> None:
        settings = Settings(api_keys="a,b")
        assert settings.api_key_set == frozenset({"a", "b"})

    def test_settings_empty_api_keys_yields_empty_set(self) -> None:
        settings = Settings(api_keys="")
        assert settings.api_key_set == frozenset()

    def test_settings_default_api_keys_is_empty(self) -> None:
        assert Settings().api_keys == ""


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
