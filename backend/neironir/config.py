"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the neironir service.

    All values are read from environment variables with the ``NEIRONIR_`` prefix.
    """

    model_config = SettingsConfigDict(env_prefix="NEIRONIR_")

    host: str = "127.0.0.1"
    port: int = 8000
    storage_dir: str = "./storage"
    max_file_size: int = 20971520
    privacy_filter_cmd: str = "python -m privacy_filter"
    privacy_filter_timeout: int = 600
    privacy_filter_mode: str = "mock"
    privacy_filter_device: str = "cpu"
    privacy_filter_checkpoint_dir: str = ""
    frontend_dir: str = "frontend"
    log_level: str = "INFO"

    @property
    def frontend_path(self) -> Path:
        """Return the frontend directory as a :class:`Path`."""
        return Path(self.frontend_dir)
