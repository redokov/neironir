"""Application configuration loaded from environment variables."""

from __future__ import annotations

import shlex
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the neironir service.

    All values are read from environment variables with the ``NEIRONIR_`` prefix.

    The caller (``create_app`` in ``main.py``) validates that
    ``admin_password`` is non-empty when authentication is required.
    """

    model_config = SettingsConfigDict(
        env_prefix="NEIRONIR_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    storage_dir: str = "./storage"
    max_file_size: int = 20971520
    privacy_filter_cmd: str = ".venv-opf/Scripts/opf.exe"
    privacy_filter_timeout: int = 600
    privacy_filter_mode: str = "combined"
    privacy_filter_device: str = "cpu"
    privacy_filter_checkpoint_dir: str = ""
    frontend_dir: str = "frontend"
    log_level: str = "INFO"

    # Path where admin "Запустить дообучение" stores the JSONL dataset
    # and the new fine-tuned checkpoint.  Relative paths resolve from
    # the process CWD, just like ``storage_dir``.
    admin_training_output_dir: str = ""

    # Maximum number of feedback records that ``build_training_dataset``
    # will pack into a single JSONL file.  Keeps the training command
    # line tractable on extremely large feedback corpora.
    admin_training_max_records: int = 100000

    # --- Authentication (variant C: login + signed session cookie + CSRF) ---
    # Secret used to sign the session cookie. MUST be set in production;
    # startup fails loudly if it is empty (see main.py).
    session_secret: str = ""
    # Lifetime of the signed session cookie, in seconds (default 24h).
    session_max_age: int = 86400
    # Single admin account (MVP: one-user app). Both env vars are
    # required; an empty password disables login but the secret alone
    # protects endpoints that only need the cookie.
    admin_user: str = "admin"
    admin_password: str = ""
    # Cookie name used for the admin session and the CSRF token.
    session_cookie_name: str = "neironir_session"
    csrf_cookie_name: str = "neironir_csrf"
    # CSRF header name expected from JS clients.
    csrf_header_name: str = "X-CSRF-Token"

    @property
    def frontend_path(self) -> Path:
        """Return the frontend directory as a :class:`Path`."""
        return Path(self.frontend_dir)


def parse_opf_cmd(raw: str) -> list[str]:
    """Tokenise the ``privacy_filter_cmd`` setting for subprocess exec.

    The first whitespace-delimited token is the executable — its path
    may contain backslashes that ``shlex.split`` would mangle on
    Windows, so it is split off first.  The remaining arguments are
    parsed with ``shlex`` (POSIX mode) to honour quoting; on unbalanced
    quotes we fall back to a naive whitespace split.

    A quoted executable (``\"C:\\Program Files\\opf\\opf.exe\" --flag``)
    is unquoted and kept as a single token.
    """
    raw = raw.strip()
    if not raw:
        return ["opf"]

    head: str
    rest: str
    if raw[0] in "\"'":
        quote = raw[0]
        end = raw.find(quote, 1)
        if end > 0:
            head = raw[1:end]
            rest = raw[end + 1 :].strip()
        else:
            # Unbalanced quote right at the start — treat literally.
            parts = raw.split(None, 1)
            head = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
    else:
        parts = raw.split(None, 1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

    if rest:
        try:
            return [head, *shlex.split(rest, posix=True)]
        except ValueError:
            return [head, *rest.split()]
    return [head]
