"""Authentication subsystem (variant C).

Three building blocks:

* :mod:`neironir.auth.session` — sign / verify the ``neironir_session``
  cookie using ``itsdangerous.URLSafeTimedSerializer``. The cookie is
  ``HttpOnly`` + ``SameSite=Lax`` + ``Secure`` (only when the request
  arrived over HTTPS).
* :mod:`neironir.auth.csrf` — double-submit-cookie CSRF token, delivered
  to the browser as a non-``HttpOnly`` cookie and expected back from
  JS clients as the ``X-CSRF-Token`` header.
* :mod:`neironir.auth.dependencies` — FastAPI ``Depends`` helpers used
  by protected routers.

The :mod:`neironir.api.auth` module wires these into HTTP endpoints
(``/login``, ``/logout``).
"""

from __future__ import annotations

from neironir.auth.csrf import (
    CSRF_TOKEN_MAX_AGE,
    generate_csrf_token,
    new_csrf_session_id,
    verify_csrf_token,
)
from neironir.auth.session import (
    SESSION_PAYLOAD_KEY,
    SessionExpiredError,
    SessionInvalidError,
    read_session_cookie,
    sign_session_cookie,
)

__all__ = [
    "CSRF_TOKEN_MAX_AGE",
    "SESSION_PAYLOAD_KEY",
    "SessionExpiredError",
    "SessionInvalidError",
    "generate_csrf_token",
    "new_csrf_session_id",
    "read_session_cookie",
    "sign_session_cookie",
    "verify_csrf_token",
]