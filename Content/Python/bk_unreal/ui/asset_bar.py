"""Blendkit asset bar — Qt window hosted inside the Unreal editor.

Entry point: :func:`open_asset_bar`.

This is the Unreal counterpart of the Maya ``ui/asset_bar.py``. It renders a
search field, an asset-type selector and a scrollable thumbnail grid. Search
runs through the Go client (:mod:`bk_unreal.core.client_lib`); results and
thumbnails are delivered on the client's report-poll thread and marshalled back
onto the Qt GUI thread via queued ``Signal`` emissions (a ``QTimer`` created on
the poll thread would never fire — that thread has no Qt event loop).

Kept deliberately compact for the initial port — the Maya version's smooth
scrolling, badges, drag&drop and detail popups are the planned next steps.
"""

from __future__ import annotations

import logging
import os
import tempfile
import webbrowser
from typing import Any

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtGui import QCursor, QPixmap
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import bookmarks as bk_bookmarks
from ..core import client_lib
from ..core import icons as bk_icons
from ..core import search as bk_search
from ..core.qt_host import get_qapp, parent_to_editor

log = logging.getLogger(__name__)

WINDOW_TITLE = "Blendkit"
THUMB_SIZE = 150
GRID_SPACING = 6
BADGE_SIZE = 20
# Watchdog re-probes (ms) for thumbnails the client reported before the tile
# existed: the finished task is dropped after one report, but the file is on disk.
_THUMB_RECHECK_MS = (1500, 3500, 6500)


def _find_cached_thumb(tempdir: str, asset: dict[str, Any]) -> str:
    """Return an already-downloaded thumbnail path for *asset*, or ''.

    The Go client writes thumbnails into *tempdir* using the URL basename
    (sometimes percent-encoded). Prefer the larger ``Middle`` image, falling
    back to the ``Small`` one, trying both raw and percent-encoded names.
    """
    import urllib.parse

    candidates: list[str] = []
    for key in (
        "thumbnailMiddleUrlWebp",
        "thumbnailMiddleUrl",
        "thumbnailSmallUrlWebp",
        "thumbnailSmallUrl",
    ):
        url = asset.get(key) or ""
        if not url:
            continue
        raw = url.rsplit("/", 1)[-1].split("?", 1)[0]
        if not raw:
            continue
        candidates.append(raw)
        quoted = urllib.parse.quote(raw, safe="")
        if quoted != raw:
            candidates.append(quoted)
    for name in candidates:
        path = os.path.join(tempdir, name)
        if os.path.isfile(path):
            return path
    return ""


class AssetDetailDialog(QDialog):
    """Read-only asset detail popup (name, author, description, tags, link)."""

    def __init__(self, asset: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._asset = asset
        self.setWindowTitle(str(asset.get("name", "Asset")))
        self.setMinimumWidth(380)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        title = QLabel(str(self._asset.get("name", "Unnamed")))
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f0f0f0;")
        title.setWordWrap(True)
        root.addWidget(title)

        author = self._asset.get("author") or {}
        author_name = author.get("fullName") or author.get("firstName") or ""
        if author_name:
            root.addWidget(QLabel(f"<b>Author:</b> {author_name}"))

        desc = str(self._asset.get("description", "")).strip()
        if desc:
            body = QLabel(desc)
            body.setWordWrap(True)
            body.setStyleSheet("QLabel { background: #252525; color: #dedede; border: 1px solid #444; padding: 6px; }")
            root.addWidget(body)

        tags = self._asset.get("tags") or []
        if tags:
            tag_lbl = QLabel("<b>Tags:</b> " + ", ".join(str(t) for t in tags[:20]))
            tag_lbl.setWordWrap(True)
            tag_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
            root.addWidget(tag_lbl)

        row = QHBoxLayout()
        row.addStretch()
        slug = self._asset.get("slug") or self._asset.get("assetBaseId") or ""
        if slug:
            view = QPushButton("View on Blendkit.com")
            view.clicked.connect(lambda: webbrowser.open(f"https://www.blendkit.com/asset-gallery-detail/{slug}/"))
            row.addWidget(view)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)


class _ClickableBadge(QLabel):
    """A small icon label that emits :attr:`clicked` on left-press."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class AssetTile(QFrame):
    """Single asset card: just the thumbnail plus two corner badges.

    - Bottom-right: a lock icon shown when the asset cannot be downloaded (user
      not logged in / no valid subscription).
    - Top-right: a bookmark badge — an outline silhouette appears on hover; a
      filled bookmark is shown permanently once the asset is bookmarked.

    No name label or other badges. Right-click opens the asset detail popup.
    """

    def __init__(self, asset: dict[str, Any], tempdir: str) -> None:
        super().__init__()
        self._asset = asset
        self._asset_id = str(asset.get("assetBaseId") or asset.get("id") or "")
        self._tempdir = tempdir
        self._thumb_path = ""
        self._hovering = False

        self.setFixedSize(THUMB_SIZE + 8, THUMB_SIZE + 8)
        self.setStyleSheet(
            "AssetTile { background: #252525; border-radius: 4px; } AssetTile:hover { background: #2f2f2f; }"
        )
        self.setToolTip(asset.get("name", ""))

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet("background: #1a1a1a; border-radius: 3px;")
        notready = bk_icons.notready_pixmap(THUMB_SIZE)
        if not notready.isNull():
            self._thumb.setPixmap(notready)
        root.addWidget(self._thumb)

        self._build_badges()
        self._start_thumb()

    # ── Badges ────────────────────────────────────────────────────────────────

    def _icon_badge(self, name: str, x: int, y: int, *, tooltip: str = "") -> None:
        pix = bk_icons.icon(name, size=BADGE_SIZE)
        if pix.isNull():
            return
        badge = QLabel(self._thumb)
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        badge.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        badge.setPixmap(pix)
        if tooltip:
            badge.setToolTip(tooltip)
        badge.move(x, y)
        badge.show()
        badge.raise_()

    def _build_badges(self) -> None:
        asset = self._asset
        edge = THUMB_SIZE - BADGE_SIZE - 4

        # Bottom-right: lock — user not logged in / no valid subscription.
        if not asset.get("canDownload", True):
            self._icon_badge("locked", edge, edge, tooltip="Login / subscription required")

        # Top-right: bookmark toggle (outline on hover, filled when bookmarked).
        self._bookmark = _ClickableBadge(self._thumb)
        self._bookmark.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        self._bookmark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bookmark.move(edge, 4)
        self._bookmark.clicked.connect(self._toggle_bookmark)
        self._refresh_bookmark()

    def _refresh_bookmark(self) -> None:
        marked = bk_bookmarks.is_bookmarked(self._asset_id)
        if marked:
            self._bookmark.setPixmap(bk_icons.icon("bookmark_full", size=BADGE_SIZE))
            self._bookmark.setToolTip("Bookmarked — click to remove")
            self._bookmark.show()
            self._bookmark.raise_()
        elif self._hovering:
            self._bookmark.setPixmap(bk_icons.icon("bookmark_empty", size=BADGE_SIZE))
            self._bookmark.setToolTip("Click to bookmark")
            self._bookmark.show()
            self._bookmark.raise_()
        else:
            self._bookmark.hide()

    def _toggle_bookmark(self) -> None:
        bk_bookmarks.toggle(self._asset_id)
        self._refresh_bookmark()

    # ── Events ────────────────────────────────────────────────────────────────

    def enterEvent(self, event) -> None:  # Qt override
        self._hovering = True
        self._refresh_bookmark()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # Qt override
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            return super().leaveEvent(event)
        self._hovering = False
        self._refresh_bookmark()
        return super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # Qt override
        dlg = AssetDetailDialog(self._asset, parent=self.window())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
        event.accept()

    # ── Thumbnail ───────────────────────────────────────────────────────────

    def _start_thumb(self) -> None:
        # 1) A path the poller already cached, or 2) a file already on disk.
        path = client_lib.get_thumbnail_path(self._asset_id) or _find_cached_thumb(self._tempdir, self._asset)
        if path:
            self.set_thumbnail(path)
            return
        # 3) Otherwise wait for the live callback, with on-disk re-probes in case
        # the client dropped the finished task before this tile registered.
        for delay in _THUMB_RECHECK_MS:
            QTimer.singleShot(delay, self._recheck)

    def _recheck(self) -> None:
        if self._thumb_path:
            return
        path = client_lib.get_thumbnail_path(self._asset_id) or _find_cached_thumb(self._tempdir, self._asset)
        if path:
            self.set_thumbnail(path)

    def set_thumbnail(self, path: str) -> None:
        if self._thumb_path == path or not path or not os.path.isfile(path):
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self._thumb_path = path
        self._thumb.setPixmap(
            pixmap.scaled(
                THUMB_SIZE,
                THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


_current_bar: AssetBarWidget | None = None


class AssetBarWidget(QWidget):
    """Top-level search + thumbnail-grid window.

    The grid reflows its column count to the viewport width and lazily loads the
    next page of results when the user scrolls near the bottom (infinite scroll).
    """

    # Emitted from the client's report-poll thread; queued onto the GUI thread.
    _search_ready = Signal(object)
    _more_ready = Signal(object)
    _thumb_ready = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(520, 640)
        self._tempdir = tempfile.mkdtemp(prefix="bk_unreal_")
        self._tiles: dict[str, AssetTile] = {}
        self._tile_order: list[AssetTile] = []
        self._cols = 0
        # Pagination state for infinite scroll.
        self._next_url = ""
        self._loading = False
        self._asset_type = "model"
        self._search_text = ""
        self._search_ready.connect(self._apply_search_task)
        self._more_ready.connect(self._apply_more_task)
        self._thumb_ready.connect(self._set_thumbnail)
        self._build_ui()
        client_lib.register_thumbnail_callback(self._on_thumbnail)

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
        self.grid.setSpacing(GRID_SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        root.addWidget(self.scroll, 1)

    # ── Search ────────────────────────────────────────────────────────────────

    def run_search(self) -> None:
        self._clear_grid()
        client_lib.clear_thumbnail_cache()
        self._next_url = ""
        self._loading = True
        self._asset_type = self.type_combo.currentData() or "model"
        self._search_text = self.search_field.text()
        query = bk_search.build_query(asset_type=self._asset_type, search_text=self._search_text)
        self.status.setText("Searching…")

        task_id = client_lib.asset_search(query, self._tempdir, self._on_search_task)
        if task_id is None:
            self._loading = False
            self.status.setText("Blendkit client not available. Build it with `python dev.py build`.")

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tiles.clear()
        self._tile_order.clear()
        self._cols = 0

    # ── Responsive grid ───────────────────────────────────────────────────────

    def _column_count(self) -> int:
        tile_w = THUMB_SIZE + 8
        avail = self.scroll.viewport().width() - 2 * GRID_SPACING
        return max(1, (avail + GRID_SPACING) // (tile_w + GRID_SPACING))

    def _reflow(self, force: bool = False) -> None:
        cols = self._column_count()
        if cols == self._cols and not force:
            return
        self._cols = cols
        for index, tile in enumerate(self._tile_order):
            self.grid.removeWidget(tile)
            self.grid.addWidget(tile, index // cols, index % cols)

    def resizeEvent(self, event) -> None:  # Qt override name
        super().resizeEvent(event)
        self._reflow()

    # ── Infinite scroll ───────────────────────────────────────────────────────

    def _maybe_load_more(self, value: int) -> None:
        if self._loading or not self._next_url:
            return
        bar = self.scroll.verticalScrollBar()
        if value >= bar.maximum() - THUMB_SIZE:
            self._load_more()

    def _load_more(self) -> None:
        self._loading = True
        self.status.setText(f"{len(self._tile_order)} results — loading more…")
        query = bk_search.build_query(asset_type=self._asset_type, search_text=self._search_text)
        query["next"] = self._next_url
        task_id = client_lib.asset_search(query, self._tempdir, self._on_more_task)
        if task_id is None:
            self._loading = False

    # ── Client callbacks (report-poll thread) ────────────────────────────────

    def _on_search_task(self, task: dict[str, Any]) -> None:
        # Called on the poll thread; the queued signal hops to the GUI thread.
        self._search_ready.emit(task)

    def _on_more_task(self, task: dict[str, Any]) -> None:
        self._more_ready.emit(task)

    def _apply_search_task(self, task: dict[str, Any]) -> None:
        status = task.get("status", "")
        if status == "error":
            self._loading = False
            self.status.setText(task.get("message") or "Search failed")
            return
        if status != "finished":
            return
        result = task.get("result") or {}
        assets = result.get("results") or task.get("results") or []
        self._next_url = result.get("next") or ""
        self._loading = False
        self._clear_grid()
        self._append_assets(assets)
        self.status.setText(f"{len(self._tile_order)} results")

    def _apply_more_task(self, task: dict[str, Any]) -> None:
        if task.get("status") != "finished":
            if task.get("status") == "error":
                self._loading = False
            return
        result = task.get("result") or {}
        assets = result.get("results") or []
        self._next_url = result.get("next") or ""
        self._append_assets(assets)
        self._loading = False
        self.status.setText(f"{len(self._tile_order)} results")
        # If the viewport still isn't full, keep pulling pages.
        QTimer.singleShot(0, lambda: self._maybe_load_more(self.scroll.verticalScrollBar().value()))

    def _on_thumbnail(self, asset_id: str, path: str) -> None:
        # Called on the poll thread; the queued signal hops to the GUI thread.
        self._thumb_ready.emit(asset_id, path)

    def _append_assets(self, assets: list[dict[str, Any]]) -> None:
        for asset in assets:
            asset_id = str(asset.get("assetBaseId") or asset.get("id") or "")
            if not asset_id or asset_id in self._tiles:
                continue
            tile = AssetTile(asset, self._tempdir)
            self._tiles[asset_id] = tile
            self._tile_order.append(tile)
        self._reflow(force=True)

    def _set_thumbnail(self, asset_id: str, path: str) -> None:
        tile = self._tiles.get(asset_id)
        if tile is not None:
            tile.set_thumbnail(path)


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
