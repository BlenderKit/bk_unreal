"""Blendkit asset bar — Qt window hosted inside the Unreal editor.

Entry point: :func:`open_asset_bar`.

This is the Unreal counterpart of the Maya ``ui/asset_bar.py``. It renders a
search field, an asset-type selector and a scrollable thumbnail grid. Search
runs through the Go client (:mod:`bk_unreal.core.client_lib`); results and
thumbnails are delivered on the client's report-poll thread and marshalled back
onto the Qt GUI thread via ``QTimer.singleShot``.

Kept deliberately compact for the initial port — the Maya version's smooth
scrolling, badges, drag&drop and detail popups are the planned next steps.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import client_lib
from ..core import search as bk_search
from ..core.qt_host import get_qapp, parent_to_editor

log = logging.getLogger(__name__)

WINDOW_TITLE = "Blendkit"
COLUMNS = 3

_current_bar: AssetBarWidget | None = None


class AssetBarWidget(QWidget):
    """Top-level search + thumbnail-grid window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(520, 640)
        self._tempdir = tempfile.mkdtemp(prefix="bk_unreal_")
        self._tiles: dict[str, QLabel] = {}
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.type_combo = QComboBox()
        for value, label in bk_search.ASSET_TYPES:
            self.type_combo.addItem(label, value)
        root.addWidget(self.type_combo)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search Blendkit…")
        self.search_field.returnPressed.connect(self.run_search)
        root.addWidget(self.search_field)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.run_search)
        root.addWidget(self.search_button)

        self.status = QLabel("")
        root.addWidget(self.status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 1)

    # ── Search ────────────────────────────────────────────────────────────────

    def run_search(self) -> None:
        self._clear_grid()
        asset_type = self.type_combo.currentData() or "model"
        query = bk_search.build_query(asset_type=asset_type, search_text=self.search_field.text())
        self.status.setText("Searching…")

        task_id = client_lib.asset_search(query, self._tempdir, self._on_search_task)
        if task_id is None:
            self.status.setText("Blendkit client not available. Build it with `python dev.py build`.")

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tiles.clear()

    # ── Client callbacks (report-poll thread) ────────────────────────────────

    def _on_search_task(self, task: dict[str, Any]) -> None:
        # Marshal onto the GUI thread.
        QTimer.singleShot(0, lambda: self._apply_search_task(task))

    def _apply_search_task(self, task: dict[str, Any]) -> None:
        result = task.get("result") or {}
        assets = result.get("results") or task.get("results") or []
        if assets:
            self.status.setText(f"{len(assets)} results")
            self._populate(assets)
        # thumbnail-ready messages arrive as follow-up tasks referencing files.
        thumb_path = task.get("thumbnail_path") or task.get("file_path")
        asset_id = task.get("asset_base_id") or task.get("asset_id")
        if thumb_path and asset_id:
            self._set_thumbnail(str(asset_id), str(thumb_path))

    def _populate(self, assets: list[dict[str, Any]]) -> None:
        self._clear_grid()
        for index, asset in enumerate(assets):
            asset_id = str(asset.get("assetBaseId") or asset.get("id") or index)
            tile = QLabel(asset.get("name", "?"))
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setFixedSize(150, 150)
            tile.setStyleSheet("border: 1px solid #444;")
            tile.setWordWrap(True)
            self.grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
            self._tiles[asset_id] = tile

    def _set_thumbnail(self, asset_id: str, path: str) -> None:
        tile = self._tiles.get(asset_id)
        if tile is None or not os.path.isfile(path):
            return
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            tile.setPixmap(pixmap.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio))


def open_asset_bar() -> AssetBarWidget | None:
    """Create (or raise) the asset bar window inside the Unreal editor."""
    global _current_bar
    app = get_qapp()
    if app is None:
        log.error("Cannot open asset bar: Qt is unavailable.")
        return None

    if _current_bar is not None:
        _current_bar.show()
        _current_bar.raise_()
        return _current_bar

    bar = AssetBarWidget()
    bar.show()
    parent_to_editor(bar)
    _current_bar = bar
    return bar
