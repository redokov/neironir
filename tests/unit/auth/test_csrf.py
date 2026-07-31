"""Unit tests for :mod:`neironir.auth.csrf`."""

from __future__ import annotations

from neironir.auth.csrf import (
    generate_csrf_token,
    new_csrf_session_id,
    sign_csrf_value,
    verify_csrf_token,
)

_TEST_SECRET = "test-secret-for-csrf"


def _signed(sid: str, token: str | None = None) -> str:
    """Produce a signed CSRF value for the given session id.

    If ``token`` is omitted a fresh token is generated.
    """
    if token is None:
        token = generate_csrf_token(sid)
    return sign_csrf_value(sid, token, secret=_TEST_SECRET)


class TestGenerateAndVerify:
    def test_generate_returns_long_token(self) -> None:
        session_id = new_csrf_session_id()
        token = generate_csrf_token(session_id)
        assert isinstance(token, str)
        assert len(token) >= 32

    def test_session_id_is_random(self) -> None:
        id1 = new_csrf_session_id()
        id2 = new_csrf_session_id()
        assert id1 != id2

    def test_token_from_same_session_different(self) -> None:
        """Each call to ``generate_csrf_token`` produces a new value,
        even for the same session id (the id is just metadata)."""
        sid = new_csrf_session_id()
        t1 = generate_csrf_token(sid)
        t2 = generate_csrf_token(sid)
        assert t1 != t2

    def test_verify_matching(self) -> None:
        sid = new_csrf_session_id()
        signed = _signed(sid)
        assert (
            verify_csrf_token(
                header_token=signed,
                cookie_token=signed,
                header_csrf_sid=sid,
                secret=_TEST_SECRET,
            )
            is True
        )

    def test_verify_mismatch(self) -> None:
        sid_a = new_csrf_session_id()
        sid_b = new_csrf_session_id()
        signed_a = _signed(sid_a)
        signed_b = _signed(sid_b)
        assert (
            verify_csrf_token(
                header_token=signed_a,
                cookie_token=signed_b,
                header_csrf_sid=sid_a,
                secret=_TEST_SECRET,
            )
            is False
        )

    def test_verify_wrong_session(self) -> None:
        """Token signed for session A must not verify for session B."""
        sid_a = new_csrf_session_id()
        sid_b = new_csrf_session_id()
        signed_a = _signed(sid_a)
        assert (
            verify_csrf_token(
                header_token=signed_a,
                cookie_token=signed_a,
                header_csrf_sid=sid_b,  # wrong session!
                secret=_TEST_SECRET,
            )
            is False
        )

    def test_verify_both_missing(self) -> None:
        assert verify_csrf_token(header_token=None, cookie_token=None, secret=_TEST_SECRET) is False

    def test_verify_one_missing(self) -> None:
        sid = new_csrf_session_id()
        signed = _signed(sid)
        assert (
            verify_csrf_token(header_token=signed, cookie_token=None, secret=_TEST_SECRET) is False
        )
        assert (
            verify_csrf_token(header_token=None, cookie_token=signed, secret=_TEST_SECRET) is False
        )

    def test_verify_too_short(self) -> None:
        assert (
            verify_csrf_token(
                header_token="short",
                cookie_token="short",
                secret=_TEST_SECRET,
            )
            is False
        )

    def test_verify_empty(self) -> None:
        assert verify_csrf_token(header_token="", cookie_token="", secret=_TEST_SECRET) is False

    def test_verify_wrong_secret(self) -> None:
        """A token signed with one secret must not verify with another."""
        sid = new_csrf_session_id()
        signed = _signed(sid)
        assert (
            verify_csrf_token(
                header_token=signed,
                cookie_token=signed,
                header_csrf_sid=sid,
                secret="different-secret",
            )
            is False
        )

    def test_const_time_comparison(self) -> None:
        """The comparison should use ``secrets.compare_digest`` which is
        constant-time. We can't verify timing in a unit test, but we
        can verify correctness: large tokens should still match."""
        sid = new_csrf_session_id()
        token = generate_csrf_token(sid)
        signed = sign_csrf_value(sid, token, secret=_TEST_SECRET)
        # A different token signed with the same sid
        other = sign_csrf_value(sid, "A" * len(token), secret=_TEST_SECRET)
        assert (
            verify_csrf_token(
                header_token=signed,
                cookie_token=signed,
                header_csrf_sid=sid,
                secret=_TEST_SECRET,
            )
            is True
        )
        assert (
            verify_csrf_token(
                header_token=signed,
                cookie_token=other,
                header_csrf_sid=sid,
                secret=_TEST_SECRET,
            )
            is False
        )
