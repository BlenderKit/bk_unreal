"""Glue between the native Slate asset bar and the Blendkit Python core.

The Slate widget (``SBlendkitAssetBar``) and the ``UBlendkitBridge`` UObject live
in the C++ ``BlendkitViewport`` module. This module binds the bridge's delegates
and drives them with the *same* engine-agnostic core the Qt UI uses
(:mod:`bk_unreal.core.search`, ``client_lib``, ``placement``, ``bookmarks``):

    Slate ─▶ bridge delegate ─▶ (here) ─▶ core  (search / drag / bookmark)
    core  ─▶ background callback ─▶ (here, marshaled to game thread) ─▶ bridge ─▶ Slate

``unreal`` and ``UBlendkitBridge`` are game-thread only, but ``client_lib``
callbacks arrive on background threads, so everything that touches the bridge is
hopped onto Slate's post-tick via :func:`_run_on_game_thread`.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.parse
from typing import Any

from ..core import bookmarks as bk_bookmarks
from ..core import client_lib, placement
from ..core import search as bk_search

log = logging.getLogger(__name__)

# Web detail page (mirrors ui/asset_bar.py).
_WEB_DETAIL = "https://www.blendkit.com/asset-gallery-detail/{slug}/"

# Module state (all mutated on the game thread only).
_installed = False
_tempdir = ""
_assets: dict[str, dict[str, Any]] = {}
_asset_type = "model"
_search_text = ""
_next_url = ""
_loading = False

# Game-thread dispatch queue (fed from background client callbacks).
_queue_lock = threading.Lock()
_queue: list = []
_tick_handle: Any = None


# ── game-thread marshaling ────────────────────────────────────────────────────


def _run_on_game_thread(func) -> None:
    """Queue *func* to run on the next Slate post-tick (the game thread)."""
    with _queue_lock:
        _queue.append(func)


def _drain(_delta: float) -> None:
    with _queue_lock:
        pending, _queue[:] = list(_queue), []
    for func in pending:
        try:
            func()
        except Exception:  # never let one callback kill the tick
            log.debug("native asset bar: queued callback failed", exc_info=True)


def _ensure_tick() -> None:
    global _tick_handle
    if _tick_handle is not None:
        return
    import unreal

    _tick_handle = unreal.register_slate_post_tick_callback(_drain)


# ── helpers ───────────────────────────────────────────────────────────────────


def _bridge():
    import unreal

    return unreal.BlendkitBridge.get()


def _thumb_for(asset: dict[str, Any]) -> str:
    """Return a decoded-friendly thumbnail path for *asset*, or ''.

    Prefers non-webp images (png/jpg) because Slate's ImageWrapper cannot decode
    webp on every engine build; falls back to whatever the client downloaded.
    """
    candidates: list[str] = []
    for key in (
        "thumbnailMiddleUrl",
        "thumbnailSmallUrl",
        "thumbnailMiddleUrlWebp",
        "thumbnailSmallUrlWebp",
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
        path = os.path.join(_tempdir, name)
        if os.path.isfile(path):
            return path

    asset_id = str(asset.get("assetBaseId") or asset.get("id") or "")
    return client_lib.get_thumbnail_path(asset_id) or ""


def _make_item(asset: dict[str, Any]):
    import unreal

    asset_id = str(asset.get("assetBaseId") or asset.get("id") or "")
    return unreal.BlendkitAssetItem(
        asset_id=asset_id,
        name=str(asset.get("name") or ""),
        asset_type=str(asset.get("assetType") or _asset_type),
        can_download=bool(asset.get("canDownload", True)),
        bookmarked=bk_bookmarks.is_bookmarked(asset_id),
        thumbnail_path=_thumb_for(asset),
    )


def _push_results() -> None:
    """Push the accumulated result set to the bridge (game thread only)."""
    bridge = _bridge()
    if bridge is None:
        return
    bridge.set_results([_make_item(a) for a in _assets.values()])
    suffix = " — loading more…" if _loading else ""
    bridge.set_status(f"{len(_assets)} results{suffix}")


def _apply_page(assets: list[dict[str, Any]], next_url: str, *, replace: bool) -> None:
    """Merge a search page into the model and refresh the grid (game thread)."""
    global _next_url, _loading
    if replace:
        _assets.clear()
    for asset in assets:
        asset_id = str(asset.get("assetBaseId") or asset.get("id") or "")
        if asset_id and asset_id not in _assets:
            _assets[asset_id] = asset
    _next_url = next_url or ""
    _loading = False
    _push_results()


def _run_query(query: dict[str, Any], *, replace: bool, failed_msg: str) -> None:
    """Dispatch a search query; callbacks marshal back to the game thread."""

    def _on_task(task: dict[str, Any]) -> None:
        # Runs on the report-poll thread — marshal before touching the bridge.
        status = task.get("status", "")
        if status == "error":

            def _err() -> None:
                global _loading
                _loading = False
                bridge = _bridge()
                if bridge is not None:
                    bridge.set_status(task.get("message") or "Search failed")

            _run_on_game_thread(_err)
            return
        if status != "finished":
            return
        result = task.get("result") or {}
        assets = result.get("results") or task.get("results") or []
        next_url = result.get("next") or ""
        _run_on_game_thread(lambda: _apply_page(assets, next_url, replace=replace))

    def _on_failed() -> None:
        def _fail() -> None:
            global _loading
            _loading = False
            bridge = _bridge()
            if bridge is not None and failed_msg:
                bridge.set_status(failed_msg)

        _run_on_game_thread(_fail)

    client_lib.asset_search_async(query, _tempdir, _on_task, on_failed=_on_failed)


# ── bridge delegate handlers ──────────────────────────────────────────────────


def _on_search(query_text: str, asset_type: str) -> None:
    """Slate requested a search (already on the game thread)."""
    global _asset_type, _search_text, _next_url, _loading
    _asset_type = asset_type or "model"
    _search_text = query_text or ""
    _assets.clear()
    _next_url = ""
    _loading = True
    client_lib.clear_thumbnail_cache()

    bridge = _bridge()
    if bridge is not None:
        bridge.set_status("Searching…")

    query = bk_search.build_query(asset_type=_asset_type, search_text=_search_text)
    _run_query(
        query,
        replace=True,
        failed_msg="Blendkit client not available. Build it with `python dev.py build`.",
    )


def _on_load_more() -> None:
    """Grid scrolled near the end — fetch the next page (game thread)."""
    global _loading
    if _loading or not _next_url:
        return
    _loading = True
    query = bk_search.build_query(asset_type=_asset_type, search_text=_search_text)
    query["next"] = _next_url
    _run_query(query, replace=False, failed_msg="")


def _on_thumbnail(asset_id: str, path: str) -> None:
    """Client downloaded a thumbnail (report-poll thread)."""
    _run_on_game_thread(lambda: _bridge() and _bridge().set_thumbnail(asset_id, path))


def _on_drag(asset_id: str) -> None:
    """Slate tile drag crossed the threshold (game thread)."""
    asset = _assets.get(asset_id)
    if asset is None:
        return
    thumb = _thumb_for(asset)
    try:
        placement.start_drag(asset, thumb)
    except Exception:
        log.debug("native asset bar: start_drag failed", exc_info=True)


def _on_activated(asset_id: str) -> None:
    """Double-click / "Open on website" (game thread)."""
    import webbrowser

    asset = _assets.get(asset_id)
    if asset is None:
        return
    slug = asset.get("slug") or asset.get("assetBaseId") or asset_id
    try:
        webbrowser.open(_WEB_DETAIL.format(slug=slug))
    except Exception:
        log.debug("native asset bar: open website failed", exc_info=True)


def _on_bookmark_toggled(asset_id: str) -> None:
    """Bookmark badge / context menu toggle (game thread)."""
    bk_bookmarks.toggle(asset_id)
    # Re-push current results so the star reflects the new state.
    _push_results()


# ── public entry point ────────────────────────────────────────────────────────


def open_native_asset_bar() -> None:
    """Install the bridge bindings (once) and open/focus the native tab."""
    global _installed, _tempdir

    import unreal

    bridge = unreal.BlendkitBridge.get()
    if bridge is None:
        log.error("Cannot open native asset bar: BlendkitBridge is unavailable.")
        return

    if not _installed:
        import tempfile

        _tempdir = tempfile.mkdtemp(prefix="bk_unreal_native_")
        _ensure_tick()
        bridge.on_search_requested.add_callable(_on_search)
        bridge.on_asset_drag_started.add_callable(_on_drag)
        bridge.on_asset_activated.add_callable(_on_activated)
        bridge.on_bookmark_toggled.add_callable(_on_bookmark_toggled)
        bridge.on_load_more_requested.add_callable(_on_load_more)
        client_lib.register_thumbnail_callback(_on_thumbnail)
        _installed = True

    bridge.open_tab()
