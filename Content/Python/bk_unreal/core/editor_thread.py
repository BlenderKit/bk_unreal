"""Dispatch background callbacks onto Unreal's Slate editor thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)
_lock = threading.Lock()
_callbacks: list[Callable[[], None]] = []
_tick_handle: Any = None


def _drain(_delta_seconds: float) -> None:
    with _lock:
        callbacks = _callbacks[:]
        _callbacks.clear()
    for callback in callbacks:
        try:
            callback()
        except Exception:
            log.exception("Queued editor-thread callback failed")


def install() -> None:
    """Register the dispatcher from Unreal's editor thread."""
    global _tick_handle
    if _tick_handle is not None:
        return
    import unreal

    _tick_handle = unreal.register_slate_post_tick_callback(_drain)


def run_on_editor_thread(callback: Callable[[], None]) -> None:
    """Queue a callback for the next editor tick without importing Qt."""
    if _tick_handle is None:
        raise RuntimeError("Editor-thread dispatcher is not installed.")
    with _lock:
        _callbacks.append(callback)


def shutdown() -> None:
    """Unregister the dispatcher and discard pending callbacks."""
    global _tick_handle
    if _tick_handle is not None:
        import unreal

        unreal.unregister_slate_post_tick_callback(_tick_handle)
        _tick_handle = None
    with _lock:
        _callbacks.clear()
