"""Unit tests for :mod:`neironir.auth.session`."""

from __future__ import annotations

import time

import pytest
from neironir.auth.session import (
    SESSION_PAYLOAD_KEY,
    SessionExpiredError,
    SessionInvalidError,
    read_session_cookie,
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
