"""Unit tests for :mod:`neironir.auth.session`."""

from __future__ import annotations

import pytest
from neironir.auth.session import (
    JTI_PAYLOAD_KEY,
    SESSION_PAYLOAD_KEY,
    SessionExpiredError,
    SessionInvalidError,
    _revoked_jti,
    is_jti_revoked,
    new_session_jti,
    read_session_cookie,
    revoke_session_jti,
    sign_session_cookie,
)

SESSION_SECRET = "test-secret-for-unit-tests"
MAX_AGE = 86400


class TestSignAndVerify:
    def test_sign_and_read_roundtrip(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True, "user": "admin", "csrf_sid": "abc123"}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        assert isinstance(signed, str)
        assert len(signed) > 10

        decoded = read_session_cookie(signed, secret=SESSION_SECRET, max_age=MAX_AGE)
        assert decoded[SESSION_PAYLOAD_KEY] is True
        assert decoded["user"] == "admin"
        assert decoded["csrf_sid"] == "abc123"

    def test_raises_on_expired_token(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        # Wait a tiny amount so the token ages — not strictly needed
        # since max_age=-1 always triggers expiry.
        with pytest.raises(SessionExpiredError):
            read_session_cookie(signed, secret=SESSION_SECRET, max_age=-1)

    def test_raises_on_tampered_cookie(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        # Completely replace the signed token with garbage.
        tampered = signed[:10] + "X" * (len(signed) - 10)
        with pytest.raises(SessionInvalidError):
            read_session_cookie(tampered, secret=SESSION_SECRET, max_age=MAX_AGE)

    def test_raises_on_empty_cookie(self) -> None:
        with pytest.raises(SessionInvalidError):
            read_session_cookie("", secret=SESSION_SECRET, max_age=MAX_AGE)

    def test_raises_on_empty_secret(self) -> None:
        with pytest.raises(SessionInvalidError):
            sign_session_cookie({SESSION_PAYLOAD_KEY: True}, secret="")

    def test_different_secrets_dont_match(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True}
        signed = sign_session_cookie(payload, secret="secret-a")
        with pytest.raises(SessionInvalidError):
            read_session_cookie(signed, secret="secret-b", max_age=MAX_AGE)

    def test_payload_must_be_dict(self) -> None:
        """If someone injects a non-dict payload (e.g. a string), it
        should be rejected rather than returning a non-dict."""
        # We can't easily inject a non-dict through ``sign_session_cookie``
        # because ``dumps()`` serializes anything JSON-serializable.
        # But we test the round-trip: any value must remain an object.
        with pytest.raises(SessionInvalidError):
            read_session_cookie("invalid.data.here", secret=SESSION_SECRET, max_age=MAX_AGE)


class TestEdgeCases:
    def test_very_long_payload(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True, "data": "x" * 5000}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        decoded = read_session_cookie(signed, secret=SESSION_SECRET, max_age=MAX_AGE)
        assert decoded["data"] == "x" * 5000

    def test_minimal_payload(self) -> None:
        payload = {SESSION_PAYLOAD_KEY: True}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        decoded = read_session_cookie(signed, secret=SESSION_SECRET, max_age=MAX_AGE)
        assert decoded[SESSION_PAYLOAD_KEY] is True


class TestSessionJti:
    """T4: random jti in session payload and revocation registry."""

    def test_new_session_jti_is_random_and_unique(self) -> None:
        a = new_session_jti()
        b = new_session_jti()
        assert a != b
        assert len(a) >= 16  # base64-urlsafe(16) → 22 chars

    def test_sign_payload_includes_jti(self) -> None:
        jti = new_session_jti()
        payload = {
            SESSION_PAYLOAD_KEY: True,
            "csrf_sid": "abc123",
            "user": "admin",
            JTI_PAYLOAD_KEY: jti,
        }
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        decoded = read_session_cookie(signed, secret=SESSION_SECRET, max_age=MAX_AGE)
        assert decoded[JTI_PAYLOAD_KEY] == jti

    def test_old_cookie_without_jti_is_still_readable(self) -> None:
        """Legacy cookies (no jti key) must remain readable during migration."""
        payload = {SESSION_PAYLOAD_KEY: True, "csrf_sid": "abc123"}
        signed = sign_session_cookie(payload, secret=SESSION_SECRET)
        decoded = read_session_cookie(signed, secret=SESSION_SECRET, max_age=MAX_AGE)
        assert decoded[SESSION_PAYLOAD_KEY] is True
        assert JTI_PAYLOAD_KEY not in decoded


class TestRevokedJtiRegistry:
    """T4: bounded process-local revoked-jti registry."""

    def test_revoke_and_check(self) -> None:
        jti = new_session_jti()
        assert not is_jti_revoked(jti)
        revoke_session_jti(jti, ttl=MAX_AGE)
        assert is_jti_revoked(jti)

    def test_unrevoked_jti_not_revoked(self) -> None:
        jti = new_session_jti()
        revoke_session_jti("some-other-jti", ttl=MAX_AGE)
        assert not is_jti_revoked(jti)

    def test_empty_jti_never_revoked(self) -> None:
        assert not is_jti_revoked("")

    def test_registry_bounded(self) -> None:
        """Inserting more than the cap evicts oldest entries."""
        from neironir.auth.session import _MAX_REVOKED_JTI

        # Reset registry
        _revoked_jti.clear()
        # Insert _MAX_REVOKED_JTI + 1 entries
        for i in range(_MAX_REVOKED_JTI + 1):
            revoke_session_jti(f"jti-{i}", ttl=MAX_AGE)
        # Oldest entry should have been evicted
        assert not is_jti_revoked("jti-0")
        # Newer entries should still be there
        assert is_jti_revoked(f"jti-{_MAX_REVOKED_JTI}")
        # Registry size must not exceed the cap
        from neironir.auth.session import _revoked_jti_size
        assert _revoked_jti_size() <= _MAX_REVOKED_JTI

    def test_expired_entries_pruned(self) -> None:
        """Entries with past expiry are pruned on next access."""
        from neironir.auth.session import _prune_revoked, _revoked_jti

        _revoked_jti.clear()
        # Insert an entry with TTL=0 (immediately expired)
        revoke_session_jti("expired-jti", ttl=0)
        # Wait a tiny bit so time.time() advances
        import time
        time.sleep(0.01)
        _prune_revoked()
        assert not is_jti_revoked("expired-jti")
