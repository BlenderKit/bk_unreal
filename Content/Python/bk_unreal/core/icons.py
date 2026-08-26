"""Icon registry for the Blendkit Unreal plugin.

Loads ``bk_unreal/data/icons/*.png`` and ``*.jpg`` as ``QPixmap`` objects on
first access and caches them for the session. This is the shared Blendkit icon
set (same files as the Maya plugin and the Blender addon).

Kept engine-agnostic: Qt is imported lazily inside the functions so the headless
pytest suite can import this module without Qt present.

Usage::

    from bk_unreal.core import icons as bk_icons

    pix = bk_icons.icon("free_plan", size=20)
    path = bk_icons.icon_path("cc0")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qtpy.QtGui import QPixmap

log = logging.getLogger(__name__)

_ICON_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "icons")
"""Absolute path to the icons directory shipped with bk_unreal."""

_cache: dict[str, QPixmap] = {}  # 'name.ext' → QPixmap


def icon_path(name: str, ext: str = "png") -> str:
    """Return the absolute path for icon *name* (``name`` has no extension).

    The file is not guaranteed to exist.
    """
    return os.path.join(_ICON_DIR, f"{name}.{ext}")


def icon(name: str, ext: str = "png", size: int | None = None) -> QPixmap:
    """Return a (cached) ``QPixmap`` for icon *name*.

    A transparent 1x1 placeholder is returned when the file is missing so
    callers never have to guard against ``None``. When *size* is given the
    pixmap is scaled to *size x size*, keeping aspect ratio.
    """
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QPixmap

    cache_key = f"{name}.{ext}"
    pix = _cache.get(cache_key)
    if pix is None:
        path = icon_path(name, ext)
        if os.path.exists(path):
            pix = QPixmap(path)
            if pix.isNull():
                log.warning("Icon loaded as null pixmap: %s", path)
                pix = QPixmap(1, 1)
                pix.fill(Qt.GlobalColor.transparent)
        else:
            log.debug("Icon not found: %s", path)
            pix = QPixmap(1, 1)
            pix.fill(Qt.GlobalColor.transparent)
        _cache[cache_key] = pix

    if size is not None and not pix.isNull():
        return pix.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pix


def notready_pixmap(size: int) -> QPixmap:
    """Return the ``thumbnail_notready.jpg`` placeholder scaled to *size x size*."""
    return icon("thumbnail_notready", ext="jpg", size=size)
