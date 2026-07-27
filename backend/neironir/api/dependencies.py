"""FastAPI dependency callables for the API layer.

Centralising these in one module makes it straightforward to override
them in tests via ``app.dependency_overrides`` (see
https://fastapi.tiangolo.com/advanced/testing-dependencies/).

* :func:`get_settings` — :class:`neironir.config.Settings` instance.
* :func:`get_storage` — :class:`neironir.storage.local.LocalStorage`.
* :func:`get_privacy` — :class:`neironir.privacy.client.PrivacyFilterClient`.

The factory closures capture a single instance per process, which is
fine for the MVP: the storage and the privacy client are stateless
beyond their construction-time parameters.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends

from neironir.config import Settings
from neironir.privacy.client import (
    MockPrivacyFilterClient,
    PrivacyFilterClient,
    SubprocessPrivacyFilterClient,
)
from neironir.storage.local import LocalStorage


@lru_cache(maxsize=1)
def _settings_cache() -> Settings:
    """Build and cache the :class:`Settings` singleton."""
    return Settings()


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`."""
    return _settings_cache()


def get_storage(settings: Settings = Depends(get_settings)) -> LocalStorage:
    """Return a :class:`LocalStorage` rooted at ``settings.storage_dir``."""
    return LocalStorage(Path(settings.storage_dir))


def get_privacy(settings: Settings = Depends(get_settings)) -> PrivacyFilterClient:
    """Return the privacy-filter client selected by ``settings.privacy_filter_mode``."""
    mode = settings.privacy_filter_mode
    if mode == "mock":
        return MockPrivacyFilterClient()
    if mode == "subprocess":
        cmd = settings.privacy_filter_cmd.split()
        checkpoint_dir = (
            Path(settings.privacy_filter_checkpoint_dir)
            if settings.privacy_filter_checkpoint_dir
            else None
        )
        return SubprocessPrivacyFilterClient(
            opf_cmd=cmd,
            checkpoint_dir=checkpoint_dir,
            device=settings.privacy_filter_device,
            timeout_s=float(settings.privacy_filter_timeout),
        )
    raise ValueError(f"Unknown privacy_filter_mode {mode!r}; expected 'mock' or 'subprocess'.")


__all__ = ["get_privacy", "get_settings", "get_storage"]
