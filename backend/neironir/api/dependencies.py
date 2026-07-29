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

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import Depends

from neironir.config import Settings


logger = logging.getLogger(__name__)
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


_privacy_client: PrivacyFilterClient | None = None


def _probe_subprocess() -> bool:
    """Return True if ``asyncio.create_subprocess_exec`` is supported.

    The WindowsApps (Microsoft Store) Python distribution raises
    ``NotImplementedError`` for asyncio subprocess operations, so we
    probe at startup and fall back to mock mode if needed.

    This function may be called from inside or outside a running event
    loop — it handles both cases by checking for an existing loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to call asyncio.run().
        try:
            asyncio.run(_probe_coro())
            return True
        except NotImplementedError:
            return False

    # A running loop is present (e.g. uvicorn startup).  Schedule the
    # probe as a task and wait synchronously.
    import concurrent.futures
    future = asyncio.run_coroutine_threadsafe(_probe_coro(), loop)
    try:
        future.result(timeout=10)
        return True
    except NotImplementedError:
        return False
    except concurrent.futures.TimeoutError:
        return False


async def _probe_coro() -> None:
    """Quick async subprocess probe."""
    proc = await asyncio.create_subprocess_exec(
        "cmd", "/c", "exit", "0",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


def get_privacy(settings: Settings = Depends(get_settings)) -> PrivacyFilterClient:
    """Return the privacy-filter client selected by ``settings.privacy_filter_mode``.

    Supported modes:
    * ``mock`` — regex-only stub for development and testing.
    * ``subprocess`` — full OPF neural model via CLI.
    * ``combined`` — neural model **plus** rule-based detector (recommended
      for production with Russian-language documents).

    If the configured mode requires a subprocess but :func:`_probe_subprocess`
    detects that the Python runtime does not support it (e.g. WindowsApps
    Python), the method falls back to ``mock`` with a warning.
    """
    global _privacy_client
    if _privacy_client is not None:
        return _privacy_client

    mode = settings.privacy_filter_mode
    if mode in ("subprocess", "combined") and not _probe_subprocess():
        logger.warning(
            "privacy_filter_mode=%r requires asyncio subprocess support which "
            "is not available on this Python runtime (WindowsApps?). "
            "Falling back to mock mode.",
            mode,
        )
        mode = "mock"

    if mode == "mock":
        _privacy_client = MockPrivacyFilterClient()
    elif mode == "subprocess":
        _privacy_client = _build_subprocess_client(settings)
    elif mode == "combined":
        model_client = _build_subprocess_client(settings)
        rule_detector = RuleBasedDetector()
        # Load any approved dynamic rules from storage.
        RuleBasedDetector.load_dynamic_rules(settings.storage_dir)
        _privacy_client = CombinedPrivacyClient(
            model_client=model_client,
            rule_detector=rule_detector,
        )
    else:
        raise ValueError(
            f"Unknown privacy_filter_mode {mode!r}; "
            f"expected 'mock', 'subprocess', or 'combined'."
        )
    return _privacy_client


def _build_subprocess_client(settings: Settings) -> SubprocessPrivacyFilterClient:
    """Construct a :class:`SubprocessPrivacyFilterClient` from settings."""
    cmd = settings.privacy_filter_cmd.split()
    checkpoint_dir = (
        Path(settings.privacy_filter_checkpoint_dir)
        if settings.privacy_filter_checkpoint_dir
        else None
    )
    # Check for runtime override (set via admin UI).
    timeout_s = float(settings.privacy_filter_timeout)
    try:
        import json
        runtime_path = Path(settings.storage_dir) / "runtime_settings.json"
        if runtime_path.is_file():
            data = json.loads(runtime_path.read_text(encoding="utf-8"))
            timeout_s = float(data.get("privacy_filter_timeout", timeout_s))
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return SubprocessPrivacyFilterClient(
        opf_cmd=cmd,
        checkpoint_dir=checkpoint_dir,
        device=settings.privacy_filter_device,
        timeout_s=timeout_s,
    )


__all__ = ["get_privacy", "get_settings", "get_storage"]
