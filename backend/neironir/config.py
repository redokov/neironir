"""Application configuration loaded from environment variables."""

from __future__ import annotations

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
