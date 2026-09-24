"""Host a Qt event loop inside the Unreal editor.

Unreal runs its own Slate UI loop and cannot ``exec_()`` a Qt ``QApplication``
(that would block the editor). The standard technique is to create the
``QApplication`` once and *pump* its event queue from a Slate post-tick
callback, so Qt widgets stay responsive while Unreal keeps ticking.

macOS caveat: Unreal's game/Slate loop runs on ``FCocoaGameThread``, *not* the
real Cocoa main thread. Qt's Cocoa platform (and the HIToolbox APIs it calls)
must only be touched from the main thread — creating the ``QApplication`` off
the main thread traps with ``SIGTRAP`` in ``dispatch_assert_queue``. So on macOS
every Qt operation is marshalled to the main thread via libdispatch, while
Unreal-API callbacks stay on the game thread (see :func:`run_on_editor_thread`).

This module is import-safe outside Unreal (the ``unreal`` import is deferred and
guarded) so the rest of the package — and the test suite — can import it freely.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_IS_MACOS = sys.platform == "darwin"

_qapp: Any = None
_tick_handle: Any = None
_main_thread_calls: list[Callable[[], None]] = []
_main_thread_calls_lock = threading.Lock()

# Lazily-populated libdispatch/pthread bindings used to reach the macOS main
# thread. Empty (and unused) on other platforms.
_macos: dict[str, Any] = {}
_macos_pump_cb: Any = None
_macos_pump_pending = False


def _import_qt() -> Any:
    """Import the Qt widgets module via qtpy (PySide2/PySide6), or ``None``."""
    try:
        from qtpy import QtWidgets
    except Exception as exc:
        log.error("qtpy/PySide not importable: %s. Run `python dev.py vendor`.", exc)
        return None
    return QtWidgets


def _macos_setup() -> dict[str, Any]:
    """Bind (once) the libdispatch/pthread symbols used to reach the main thread."""
    if _macos:
        return _macos
    import ctypes

    lib = ctypes.CDLL(None)
    # dispatch_get_main_queue() expands to &_dispatch_main_q, i.e. the address
    # of the exported symbol — not the value stored there.
    main_q = ctypes.cast(
        ctypes.addressof(ctypes.c_char.in_dll(lib, "_dispatch_main_q")),
        ctypes.c_void_p,
    )
    work_t = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    sync_f = lib.dispatch_sync_f
    sync_f.restype = None
    sync_f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, work_t]
    async_f = lib.dispatch_async_f
    async_f.restype = None
    async_f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, work_t]
    lib.pthread_main_np.restype = ctypes.c_int
    _macos.update(lib=lib, main_q=main_q, work_t=work_t, sync_f=sync_f, async_f=async_f)
    return _macos


def _on_macos_main_thread() -> bool:
    """True when the current thread is the real Cocoa main thread."""
    return _macos_setup()["lib"].pthread_main_np() != 0


def call_on_ui_thread(fn: Callable[[], Any]) -> Any:
    """Run *fn* on the thread that owns Qt and return its result.

    On Windows/Linux the editor/game thread already is the UI thread, so *fn*
    runs inline. On macOS it is marshalled to the Cocoa main thread (blocking
    until it completes), because Qt's Cocoa platform is not thread-safe.
    """
    if not _IS_MACOS or _on_macos_main_thread():
        return fn()

    m = _macos_setup()
    box: dict[str, Any] = {}

    def _trampoline(_ctx: Any) -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # re-raised on the caller thread
            box["error"] = exc

    # ``dispatch_sync_f`` blocks with the GIL released (ctypes drops it around
    # the C call), so the trampoline can re-acquire it on the main thread — no
    # deadlock. ``cb`` must stay alive for the duration of the call.
    cb = m["work_t"](_trampoline)
    m["sync_f"](m["main_q"], None, cb)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def get_qapp() -> Any:
    """Return the process-wide ``QApplication``, creating it if needed."""
    global _qapp
    QtWidgets = _import_qt()
    if QtWidgets is None:
        return None

    def _create() -> Any:
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
        return app

    _qapp = call_on_ui_thread(_create)
    _install_tick_pump()
    return _qapp


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
        # Editor-thread callbacks touch the Unreal API, which is game-thread
        # only, so always drain them here on the Slate tick.
        with _main_thread_calls_lock:
            calls = _main_thread_calls[:]
            _main_thread_calls.clear()
        for callback in calls:
            try:
                callback()
            except Exception:
                log.exception("Queued editor-thread callback failed")

        app = _qapp
        if app is None:
            return
        if _IS_MACOS:
            # Qt lives on the Cocoa main thread; pump it there, not here.
            _request_macos_qt_pump()
        else:
            app.processEvents()

    _tick_handle = unreal.register_slate_post_tick_callback(_pump)
    log.info("Qt event pump installed on Slate post-tick.")


def _macos_qt_pump_trampoline(_ctx: Any = None) -> None:
    """Run ``processEvents`` on the macOS main thread (libdispatch callback)."""
    global _macos_pump_pending
    _macos_pump_pending = False
    app = _qapp
    if app is not None:
        try:
            app.processEvents()
        except Exception:
            log.exception("Qt processEvents failed on macOS main thread")


def _request_macos_qt_pump() -> None:
    """Queue one Qt pump on the main thread, coalescing pending requests."""
    global _macos_pump_pending, _macos_pump_cb
    if _macos_pump_pending:
        return
    m = _macos_setup()
    if _macos_pump_cb is None:
        # Kept alive for the process lifetime; reused every frame.
        _macos_pump_cb = m["work_t"](_macos_qt_pump_trampoline)
    _macos_pump_pending = True
    m["async_f"](m["main_q"], None, _macos_pump_cb)


def run_on_editor_thread(callback: Callable[[], None]) -> None:
    """Queue *callback* for the next Slate tick on Unreal's editor thread."""
    if get_qapp() is None:
        raise RuntimeError("Qt host is not available to schedule an editor-thread callback.")
    with _main_thread_calls_lock:
        _main_thread_calls.append(callback)


def parent_to_editor(widget: Any) -> None:
    """Best-effort: parent a top-level Qt window to the Unreal main window."""
    try:
        win_id = call_on_ui_thread(lambda: int(widget.winId()))
    except Exception as exc:
        log.debug("Could not read window handle: %s", exc)
        return

    def _parent() -> None:
        try:
            import unreal

            unreal.parent_external_window_to_slate(win_id)
        except Exception as exc:
            log.debug("Could not parent window to Slate: %s", exc)

    # ``parent_external_window_to_slate`` is an Unreal API (game-thread only);
    # on macOS the caller may be the Cocoa main thread, so hop to the editor
    # thread. Elsewhere the caller already is the game thread.
    if _IS_MACOS and _on_macos_main_thread():
        run_on_editor_thread(_parent)
    else:
        _parent()


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
