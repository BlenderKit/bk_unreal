"""Blendkit Unreal plugin registration.

Called by ``Content/Python/init_unreal.py`` at editor startup. Configures
logging and adds a **Blendkit** menu with native Slate asset and settings tabs.

All Unreal API access is deferred and guarded so importing this module outside
the editor (e.g. in the test suite) never fails.
"""

from __future__ import annotations

import logging

from ._version import get_version
from .core import log as bk_log

log = logging.getLogger(__name__)

MENU_NAME = "LevelEditor.MainMenu.Blendkit"
MENU_LABEL = "Blendkit"
_registered = False


def _open_asset_bar_impl() -> None:
    """Open the native Slate asset browser."""
    try:
        from .ui_native import asset_bar_native
    except Exception as exc:
        log.error("Failed to import native asset bar UI: %s", exc)
        return
    asset_bar_native.open_native_asset_bar()


def _open_settings_impl() -> None:
    """Open the native Slate preferences tab."""
    try:
        from .ui_native import settings
    except Exception as exc:
        log.error("Failed to import settings UI: %s", exc)
        return
    settings.open_settings()


def _register_menu() -> None:
    import unreal

    menus = unreal.ToolMenus.get()
    main_menu = menus.find_menu("LevelEditor.MainMenu")
    if main_menu is None:
        log.warning("LevelEditor.MainMenu not found; skipping menu registration.")
        return

    main_menu.add_sub_menu(
        owner="Blendkit",
        section_name="",
        name="Blendkit",
        label=MENU_LABEL,
        tool_tip="Blendkit asset browser",
    )

    bk_menu = menus.extend_menu(MENU_NAME)

    entry = unreal.ToolMenuEntry(
        name="Blendkit.OpenAssetBar",
        type=unreal.MultiBlockType.MENU_ENTRY,
    )
    entry.set_label("Open Asset Bar")
    entry.set_tool_tip("Search and browse Blendkit assets")
    # Route the click back into this module via a one-line Python command.
    entry.set_string_command(
        unreal.ToolMenuStringCommandType.PYTHON,
        "",
        "from bk_unreal import unreal_plugin as _p; _p._open_asset_bar_impl()",
    )
    bk_menu.add_menu_entry("Actions", entry)

    settings_entry = unreal.ToolMenuEntry(
        name="Blendkit.OpenSettings",
        type=unreal.MultiBlockType.MENU_ENTRY,
    )
    settings_entry.set_label("Settings")
    settings_entry.set_tool_tip("Edit Blendkit preferences")
    settings_entry.set_string_command(
        unreal.ToolMenuStringCommandType.PYTHON,
        "",
        "from bk_unreal import unreal_plugin as _p; _p._open_settings_impl()",
    )
    bk_menu.add_menu_entry("Actions", settings_entry)
    menus.refresh_all_widgets()


def register() -> None:
    """Configure logging and build the editor menu (idempotent)."""
    global _registered
    if _registered:
        return

    bk_log.configure_loggers()
    log.info("Blendkit for Unreal v%s starting…", get_version())

    try:
        _register_menu()
    except Exception as exc:
        log.error("Menu registration failed: %s", exc)

    try:
        from .core import client_lib

        client_lib.warm_up()
    except Exception as exc:
        log.debug("Client warm-up skipped: %s", exc)

    try:
        from .core import editor_thread
        from .ui_native import asset_bar_native, settings

        editor_thread.install()
        asset_bar_native.install()
        settings.install()
    except Exception as exc:
        log.error("Native UI initialization failed: %s. Rebuild the BlendkitViewport module.", exc)

    _registered = True
    log.info("Blendkit for Unreal registered.")


def unregister() -> None:
    """Tear down menus, native bindings and editor callbacks."""
    global _registered
    try:
        import unreal

        menus = unreal.ToolMenus.get()
        menus.unregister_owner_by_name("Blendkit")
        menus.refresh_all_widgets()
    except Exception as exc:
        log.debug("Menu unregister skipped: %s", exc)

    try:
        from .core import editor_thread
        from .ui_native import asset_bar_native, settings

        settings.shutdown()
        asset_bar_native.shutdown()
        editor_thread.shutdown()
    except Exception:
        log.exception("Native UI teardown failed")

    _registered = False
