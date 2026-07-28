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
from neironir.privacy.combined import CombinedPrivacyClient
from neironir.privacy.rules import RuleBasedDetector
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
    """Return the privacy-filter client selected by ``settings.privacy_filter_mode``.

    Supported modes:
    * ``mock`` — regex-only stub for development and testing.
    * ``subprocess`` — full OPF neural model via CLI.
    * ``combined`` — neural model **plus** rule-based detector (recommended
      for production with Russian-language documents).
    """
    mode = settings.privacy_filter_mode
    if mode == "mock":
        return MockPrivacyFilterClient()
    if mode == "subprocess":
        return _build_subprocess_client(settings)
    if mode == "combined":
        model_client = _build_subprocess_client(settings)
        rule_detector = RuleBasedDetector()
        # Load any approved dynamic rules from storage.
        RuleBasedDetector.load_dynamic_rules(settings.storage_dir)
        return CombinedPrivacyClient(
            model_client=model_client,
            rule_detector=rule_detector,
        )
    raise ValueError(
        f"Unknown privacy_filter_mode {mode!r}; "
        f"expected 'mock', 'subprocess', or 'combined'."
    )


def _build_subprocess_client(settings: Settings) -> SubprocessPrivacyFilterClient:
    """Construct a :class:`SubprocessPrivacyFilterClient` from settings."""
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


__all__ = ["get_privacy", "get_settings", "get_storage"]
