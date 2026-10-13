"""Native Slate settings and account actions backed by the Python core."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ..core import auth, blender_runner, editor_thread
from ..core.prefs import RESOLUTIONS, prefs

log = logging.getLogger(__name__)
_installed = False
_generation = 0
_probe_id = 0
_state: dict[str, Any] = {}


def _bridge() -> Any:
    import unreal

    return unreal.BlendkitBridge.get()


def _publish() -> None:
    import unreal

    _bridge().set_settings(unreal.BlendkitSettings(**_state))


def _refresh_account() -> None:
    logged_in = auth.is_logged_in()
    _state["logged_in"] = logged_in
    _state["account_status"] = (
        "Waiting for browser login..." if _state.get("login_pending") else "Logged in" if logged_in else "Not logged in"
    )
    _publish()


def _queue(callback: Callable[[], None]) -> None:
    generation = _generation

    def apply() -> None:
        if _installed and generation == _generation:
            callback()

    if _installed:
        editor_thread.run_on_editor_thread(apply)


def _on_account_changed() -> None:
    _queue(_refresh_account)


def _on_settings_requested() -> None:
    _state.update(
        global_dir=prefs.global_dir,
        blender_executable=prefs.blender_exe,
        resolution=prefs.resolution,
        verify_ssl=prefs.ssl_verification,
        message="",
    )
    _refresh_account()
    _on_detect_blender(prefs.blender_exe)


def _on_settings_saved(settings: Any) -> None:
    if settings.resolution not in RESOLUTIONS:
        _state["message"] = "Invalid import resolution."
        _publish()
        return
    prefs.global_dir = settings.global_dir.strip()
    prefs.ssl_verification = bool(settings.verify_ssl)
    prefs.resolution = settings.resolution
    prefs.blender_exe = blender_runner.resolve_macos_app(settings.blender_executable.strip())
    prefs.save()
    _state.update(
        global_dir=prefs.global_dir,
        blender_executable=prefs.blender_exe,
        resolution=prefs.resolution,
        verify_ssl=prefs.ssl_verification,
        message="Settings saved.",
    )
    _publish()
    _on_detect_blender(prefs.blender_exe)


def _on_login_finished(ok: bool) -> None:
    def apply() -> None:
        _state["login_pending"] = False
        _state["message"] = "Login completed." if ok else "Login failed or timed out. Try again."
        _refresh_account()

    _queue(apply)


def _on_login() -> None:
    if _state.get("login_pending"):
        return
    _state["login_pending"] = True
    _state["message"] = ""
    _refresh_account()
    generation = _generation

    def on_done(ok: bool) -> None:
        if _installed and generation == _generation:
            _on_login_finished(ok)

    auth.login_async(on_done)


def _on_logout() -> None:
    auth.logout()
    _state["message"] = "Logged out."
    _refresh_account()


def _on_manual_key(value: str) -> None:
    if value.strip():
        auth.set_manual_api_key(value.strip())
        _state["message"] = "API key applied."
        _refresh_account()


def _on_detect_blender(path: str) -> None:
    global _probe_id
    _probe_id += 1
    probe_id = _probe_id
    generation = _generation
    _state["blender_pending"] = True
    _state["blender_status"] = "Checking Blender..."
    _publish()

    def worker() -> None:
        try:
            executable = (
                blender_runner.resolve_macos_app(path.strip())
                if path.strip()
                else blender_runner.find_blender_executable()
            )
            version = blender_runner.query_blender_version(executable) if executable else None
            if not executable:
                status = f"No Blender found. Blender {blender_runner.MIN_BLENDER_MAJOR}.0 or newer is required."
            elif version is None:
                status = f"Could not read Blender version: {executable}"
            elif not blender_runner.version_meets_min(version):
                status = (
                    f"Blender {'.'.join(map(str, version))} is too old. "
                    f"Blender {blender_runner.MIN_BLENDER_MAJOR}.0 or newer is required."
                )
            else:
                status = f"Blender {'.'.join(map(str, version))} detected: {executable}"
        except Exception:
            log.exception("Blender detection failed")
            status = "Blender detection failed. Check the executable path."

        def apply() -> None:
            if probe_id == _probe_id and generation == _generation:
                _state["blender_pending"] = False
                _state["blender_status"] = status
                _publish()

        _queue(apply)

    threading.Thread(target=worker, name="bk-unreal-blender-detect", daemon=True).start()


def _bindings(bridge: Any) -> tuple[tuple[Any, Any], ...]:
    return (
        (bridge.on_settings_requested, _on_settings_requested),
        (bridge.on_settings_saved, _on_settings_saved),
        (bridge.on_login_requested, _on_login),
        (bridge.on_logout_requested, _on_logout),
        (bridge.on_manual_key_requested, _on_manual_key),
        (bridge.on_blender_detect_requested, _on_detect_blender),
    )


def install() -> None:
    """Bind native settings events once on the editor thread."""
    global _installed
    if _installed:
        return
    bridge = _bridge()
    for delegate, callback in _bindings(bridge):
        delegate.add_callable(callback)
    _state.update(login_pending=False, blender_pending=False, blender_status="", message="")
    _installed = True
    auth.add_login_listener(_on_account_changed)
    _on_settings_requested()


def open_settings() -> None:
    """Open or focus the dockable native settings tab."""
    install()
    _bridge().open_settings_tab()


def shutdown() -> None:
    """Unbind settings events and ignore late background callbacks."""
    global _installed, _generation
    if not _installed:
        return
    _installed = False
    _generation += 1
    auth.remove_login_listener(_on_account_changed)
    for delegate, callback in _bindings(_bridge()):
        delegate.remove_callable(callback)
    _state.clear()
