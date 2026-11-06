"""Static-analysis regression for the login open-redirect fix (T9).

``frontend/login.js`` reads ``?next=`` from the URL and assigns it to
``window.location.href`` after a successful login.  Without sanitisation an
attacker can craft ``/login?next=https://evil.example`` (or a
``javascript:`` URL) and harvest the redirect.  The server-side guard
(``_safe_next_url`` in ``api/auth.py``) already rejects such values, but the
client must not blindly follow them either — defense in depth.

See ``code-review-agent-report.md`` finding #21 / spec 006 T9.
"""

from __future__ import annotations

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
LOGIN_JS = FRONTEND_DIR / "login.js"


def _source() -> str:
    assert LOGIN_JS.exists(), f"missing {LOGIN_JS}"
    return LOGIN_JS.read_text(encoding="utf-8")


def test_login_js_has_a_sanitizer() -> None:
    src = _source()
    assert "safeNext" in src, "no allowlist sanitizer function found in login.js"


def test_login_js_does_not_assign_raw_next_to_location() -> None:
    """Every ``window.location.href = ...`` must use the sanitized value."""
    src = _source()
    bad = [
        line.strip()
        for line in src.splitlines()
        if "window.location.href =" in line and "safeNext(" not in line
    ]
    assert not bad, f"raw next assignment: {bad}"


def test_server_side_guard_still_present() -> None:
    auth_py = (
        Path(__file__).resolve().parents[3] / "backend" / "neironir" / "api" / "auth.py"
    )
    src = auth_py.read_text(encoding="utf-8")
    assert "_safe_next_url" in src
    assert 'startswith("//")' in src, "protocol-relative guard missing"
