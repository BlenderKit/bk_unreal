"""Host a Qt event loop inside the Unreal editor.

Unreal runs its own Slate UI loop on the main thread, so a Qt ``QApplication``
cannot ``exec_()`` (that would block the editor). The standard technique is to
create the ``QApplication`` once and *pump* its event queue from a Slate
post-tick callback, so Qt widgets stay responsive while Unreal keeps ticking.

This module is import-safe outside Unreal (the ``unreal`` import is deferred and
guarded) so the rest of the package — and the test suite — can import it freely.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_qapp: Any = None
_tick_handle: Any = None
_main_thread_calls: list[Callable[[], None]] = []
_main_thread_calls_lock = threading.Lock()


def _import_qt() -> Any:
    """Import the Qt widgets module via qtpy (PySide2/PySide6), or ``None``."""
    try:
        from qtpy import QtWidgets
    except Exception as exc:
        log.error("qtpy/PySide not importable: %s. Run `python dev.py vendor`.", exc)
        return None
    return QtWidgets


def get_qapp() -> Any:
    """Return the process-wide ``QApplication``, creating it if needed."""
    global _qapp
    QtWidgets = _import_qt()
    if QtWidgets is None:
        return None
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _qapp = app
    _install_tick_pump()
    return app


def _install_tick_pump() -> None:
    """Register a Slate post-tick callback that processes Qt events each frame."""
    global _tick_handle
    if _tick_handle is not None:
        return
    try:
        import unreal
    except Exception:
        # Outside Unreal (e.g. tests / standalone) there is no Slate loop to
        # pump; the caller is responsible for running the Qt loop itself.
        return

    def _pump(_delta_seconds: float) -> None:
        app = _qapp
        if app is not None:
            app.processEvents()
        with _main_thread_calls_lock:
            calls = _main_thread_calls[:]
            _main_thread_calls.clear()
        for callback in calls:
            try:
                callback()
            except Exception:
                log.exception("Queued editor-thread callback failed")

    _tick_handle = unreal.register_slate_post_tick_callback(_pump)
    log.info("Qt event pump installed on Slate post-tick.")


def run_on_editor_thread(callback: Callable[[], None]) -> None:
    """Queue *callback* for the next Slate tick on Unreal's editor thread."""
    if get_qapp() is None:
        raise RuntimeError("Qt host is not available to schedule an editor-thread callback.")
    with _main_thread_calls_lock:
        _main_thread_calls.append(callback)


def parent_to_editor(widget: Any) -> None:
    """Best-effort: parent a top-level Qt window to the Unreal main window."""
    try:
        import unreal

        unreal.parent_external_window_to_slate(int(widget.winId()))
    except Exception as exc:
        log.debug("Could not parent window to Slate: %s", exc)


def shutdown() -> None:
    """Remove the Slate tick callback (called on plugin unregister)."""
    global _tick_handle
    if _tick_handle is None:
        return
    try:
        import unreal

        unreal.unregister_slate_post_tick_callback(_tick_handle)
    except Exception:
        pass
    _tick_handle = None
