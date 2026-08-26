"""Blendkit Unreal plugin registration.

Called by ``Content/Python/init_unreal.py`` at editor startup. Configures
logging and adds a **Blendkit** menu to the Unreal main menu bar (via
``unreal.ToolMenus``) with an *Open Asset Bar* action.

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
    """Import lazily so a missing Qt vendor dir doesn't break registration."""
    try:
        from .ui import asset_bar
    except Exception as exc:
        log.error("Failed to import asset bar UI: %s", exc)
        return
    asset_bar.open_asset_bar()


def _open_settings_impl() -> None:
    """Import lazily so a missing Qt vendor dir doesn't break registration."""
    try:
        from .ui import settings_dialog
    except Exception as exc:
        log.error("Failed to import settings UI: %s", exc)
        return
    settings_dialog.open_settings()


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

    _registered = True
    log.info("Blendkit for Unreal registered.")


def unregister() -> None:
    """Tear down the menu and Qt pump (useful for live-reload during dev)."""
    global _registered
    try:
        import unreal

        menus = unreal.ToolMenus.get()
        menus.unregister_owner_by_name("Blendkit")
        menus.refresh_all_widgets()
    except Exception as exc:
        log.debug("Menu unregister skipped: %s", exc)

    try:
        from .core import qt_host

        qt_host.shutdown()
    except Exception:
        pass

    _registered = False
