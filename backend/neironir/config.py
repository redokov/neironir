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
    privacy_filter_cmd: str = "python -m opf"
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

    @property
    def frontend_path(self) -> Path:
        """Return the frontend directory as a :class:`Path`."""
        return Path(self.frontend_dir)
