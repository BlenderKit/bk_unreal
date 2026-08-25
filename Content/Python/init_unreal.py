# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Unreal Engine auto-startup entry point for the Blendkit plugin.

Unreal's *Python Editor Script Plugin* automatically executes any file named
``init_unreal.py`` found on the Python path. Both dev-mode mechanisms put this
file's directory (``<plugin>/Content/Python``) on that path:

* **Junction install** — ``.vscode/unreal_plugin.py junction`` links the repo
  into ``<Project>/Plugins/Blendkit``. Unreal enables the plugin and adds its
  ``Content/Python`` folder to ``sys.path`` automatically.
* **Python-path install** — ``.vscode/unreal_plugin.py pythonpath`` adds this
  directory to the project's ``AdditionalPaths`` (``DefaultEngine.ini``), so
  Unreal loads it without the plugin being installed.

Either way Unreal ends up running this file, which:
  1. ensures ``<plugin>/Content/Python`` and the vendored ``bk_unreal/lib`` are
     importable, then
  2. hands off to :func:`bk_unreal.unreal_plugin.register` to configure logging
     and build the *Blendkit* editor menu.

Kept import-light and defensive so a failure here never blocks editor startup.
"""

from __future__ import annotations

import os
import sys

_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))  # <plugin>/Content/Python
_LIB_DIR = os.path.join(_PYTHON_DIR, "bk_unreal", "lib")

for _extra in (_PYTHON_DIR, _LIB_DIR):
    if os.path.isdir(_extra) and _extra not in sys.path:
        sys.path.insert(0, _extra)

# Bind qtpy to the vendored PySide6 (the only Qt binding we ship).
os.environ.setdefault("QT_API", "pyside6")


def _bootstrap() -> None:
    try:
        from bk_unreal import unreal_plugin
    except Exception as exc:  # pragma: no cover - never block editor startup
        print(f"[Blendkit] Failed to import plugin package: {exc}")
        return
    try:
        unreal_plugin.register()
    except Exception as exc:  # pragma: no cover
        print(f"[Blendkit] Registration failed: {exc}")


_bootstrap()
