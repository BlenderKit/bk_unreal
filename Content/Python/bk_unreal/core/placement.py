"""Blendkit Unreal - drag-to-place asset system.

Port of the Maya ``ui/placement.py`` drag session. Engine-specific work
(mouse ray, line-trace, viewport preview) is delegated to
:mod:`bk_unreal.core.viewport_ue`, which guards all ``unreal`` imports so this
module stays importable (and unit-testable) outside the editor.

Flow, mirroring bk_maya:
  1. :func:`start_drag` builds a :class:`_State` (bbox in Unreal cm, any
     already-cached proxor wireframe/mesh) and starts a poll timer.
  2. Each tick, :meth:`DragSession._poll_cursor` asks ``viewport_ue`` for the
     current mouse world-ray, raycasts the level, and updates the green/cyan/
     red preview actor.
  3. LMB places the asset (currently a placeholder spawn - full download+
     import lands with the Content Browser import pipeline); RMB/Esc cancels.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from . import client_lib, viewport_ue
from . import prefs as _prefs_mod

log = logging.getLogger(__name__)

WHEEL_STEP = 5.0  # degrees per wheel notch

# ``bk_proxor._unreal.draw`` already expands PRX into Unreal Z-up centimetres.
# Keep the proxor's coordinate-system difference as a draw transform only:
# default proxor forward Y+ -> Unreal X+.
_PROXOR_BASIS_ROTATION_X_DEG = -90.0
_PROXOR_BASIS_ROTATION_Z_DEG = -90.0

# Set False if the hologram is still front/back (or left/right) mirrored
# relative to the placed asset after the X-rotation above - a mirror can't
# be fixed by rotation alone.
_PROXOR_FLIP_Y = True


def _meters_to_internal() -> float:
    """Blendkit bboxes are metres; Unreal's internal unit is centimetres."""
    return 100.0


@dataclass
class _State:
    asset_data: dict[str, Any]
    thumb_path: str
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_z: float = 0.0
    active: bool = False
    has_hit: bool = False
    hit_floor: bool = False
    surface_normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    proxor_lines: list = field(default_factory=list)
    proxor_mesh: list = field(default_factory=list)
    arrow_lines: list = field(default_factory=list)
    basis_rotation_x: float = 0.0
    basis_rotation_z: float = 0.0
    downloading: bool = False
    download_progress: float = 0.0
    download_status: str = ""


_active_state = _State(asset_data={}, thumb_path="", bbox_min=(0, 0, 0), bbox_max=(0, 0, 0))
_session: DragSession | None = None


def _proxor_cache_path(asset_data: dict[str, Any]) -> str:
    asset_base_id = asset_data.get("assetBaseId", "")
    if not asset_base_id:
        return ""
    base = _prefs_mod.prefs.global_dir_resolved()
    return os.path.join(base, "proxors", f"{asset_base_id}.prxc")


def _prxc_download_url(asset_data: dict[str, Any]) -> str:
    """Best-effort ``.prxc`` download URL from a search-result asset dict."""
    url = asset_data.get("prxcUrl") or asset_data.get("prxc_url") or ""
    if url:
        return str(url)
    for f in asset_data.get("files") or []:
        if isinstance(f, dict) and f.get("fileType") in ("proxor", "prxc"):
            return str(f.get("url") or f.get("downloadUrl") or "")
    return ""


def _parse_prxc(prxc_path: str) -> dict[str, Any]:
    """Read a ``.prxc`` from disk and return ``{"lines": [...], "mesh": [...]}``.

    Points are already scaled to Unreal centimetres and mirrored to Unreal's
    left-handed frame (see ``bk_proxor._unreal.draw``).
    """
    out: dict[str, Any] = {"lines": [], "mesh": [], "arrow": []}
    try:
        from ..bk_proxor import prx_format as pf
        from ..bk_proxor._unreal.draw import prx_to_arrow_segments, prx_to_line_segments, prx_to_mesh_triangles
    except Exception as exc:
        log.debug("bk_proxor unavailable: %s", exc)
        return out
    try:
        payload = pf.read_prx(prxc_path)
        scale = _meters_to_internal()
        out["lines"] = prx_to_line_segments(payload, world_scale=scale, flip_y=_PROXOR_FLIP_Y)
        out["mesh"] = prx_to_mesh_triangles(payload, world_scale=scale, flip_y=_PROXOR_FLIP_Y)
        out["arrow"] = prx_to_arrow_segments(payload, world_scale=scale, flip_y=_PROXOR_FLIP_Y)
        _prepare_proxor_payload(out)
    except Exception as exc:
        log.debug("Proxor parse failed for %s: %s", prxc_path, exc)
        return out
    log.debug(
        "Proxor loaded from %s: %d segments, %d mesh verts",
        prxc_path,
        len(out["lines"]),
        len(out["mesh"]),
    )
    return out


def _load_proxor_payload(asset_data: dict[str, Any]) -> dict[str, Any]:
    """Return a cached ``.prxc`` payload, or empty lists if not on disk yet."""
    path = _proxor_cache_path(asset_data)
    if path and os.path.isfile(path):
        return _parse_prxc(path)
    return {"lines": [], "mesh": [], "arrow": []}


def _bounds_from_points(
    points: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if not points:
        return None
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))


def _translate_point(p: tuple, origin: tuple[float, float, float]) -> tuple[float, float, float]:
    return (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2])


def _prepare_proxor_payload(prxc: dict[str, Any]) -> None:
    """Use expanded proxor coordinates as-is, anchored at their bottom center."""
    lines = prxc.get("lines") or []
    mesh = prxc.get("mesh") or []
    arrow = prxc.get("arrow") or []
    points = [p for seg in lines for p in seg] + list(mesh)
    bounds = _bounds_from_points(points)
    if bounds is None:
        return
    bbox_min, bbox_max = bounds
    origin = ((bbox_min[0] + bbox_max[0]) * 0.5, (bbox_min[1] + bbox_max[1]) * 0.5, bbox_min[2])
    prxc["lines"] = [[_translate_point(a, origin), _translate_point(b, origin)] for a, b in lines]
    prxc["mesh"] = [_translate_point(p, origin) for p in mesh]
    prxc["arrow"] = [[_translate_point(a, origin), _translate_point(b, origin)] for a, b in arrow]
    prxc["bbox_min"] = _translate_point(bbox_min, origin)
    prxc["bbox_max"] = _translate_point(bbox_max, origin)


def _coerce_bbox(v: Any, default: tuple[float, float, float], scale: float) -> tuple[float, float, float]:
    if v is None:
        return default
    if isinstance(v, dict):
        seq = (v.get("x", 0.0), v.get("y", 0.0), v.get("z", 0.0))
    else:
        try:
            seq = tuple(v)
        except TypeError:
            return default
    try:
        return (float(seq[0]) * scale, float(seq[1]) * scale, float(seq[2]) * scale)
    except (TypeError, ValueError, IndexError):
        return default


def _asset_bbox(asset_data: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return fallback ``(bbox_min, bbox_max)`` from search data in Unreal cm."""
    scale = _meters_to_internal()
    default_min = (-0.5 * scale, -0.5 * scale, 0.0)
    default_max = (0.5 * scale, 0.5 * scale, 1.0 * scale)

    raw_min = asset_data.get("bbox_min")
    raw_max = asset_data.get("bbox_max")
    if raw_min is None or raw_max is None:
        params = asset_data.get("dictParameters") or asset_data.get("parameters") or {}
        try:
            mn = (float(params["boundBoxMinX"]), float(params["boundBoxMinY"]), float(params["boundBoxMinZ"]))
            mx = (float(params["boundBoxMaxX"]), float(params["boundBoxMaxY"]), float(params["boundBoxMaxZ"]))
            raw_min = mn if raw_min is None else raw_min
            raw_max = mx if raw_max is None else raw_max
        except (KeyError, TypeError, ValueError):
            log.warning("Asset %s has no usable bbox - using 1 m default cube", asset_data.get("name", "?"))

    bbox_min = _coerce_bbox(raw_min, default_min, scale)
    bbox_max = _coerce_bbox(raw_max, default_max, scale)

    # Blendkit/Blender is Z-up right-handed; Unreal is Z-up left-handed, so
    # only Y mirrors (see bk_proxor._unreal.draw for the same convention).
    sw_min = (bbox_min[0], -bbox_min[1], bbox_min[2])
    sw_max = (bbox_max[0], -bbox_max[1], bbox_max[2])
    out_min = tuple(min(sw_min[i], sw_max[i]) for i in range(3))
    out_max = tuple(max(sw_min[i], sw_max[i]) for i in range(3))
    return out_min, out_max


def _preview_from_payload(
    prxc: dict[str, Any],
    fallback_bbox_min: tuple[float, float, float],
    fallback_bbox_max: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], list, list, list, float, float]:
    if prxc.get("bbox_min") and prxc.get("bbox_max"):
        return (
            prxc["bbox_min"],
            prxc["bbox_max"],
            prxc.get("lines", []),
            prxc.get("mesh", []),
            prxc.get("arrow", []),
            _PROXOR_BASIS_ROTATION_X_DEG,
            _PROXOR_BASIS_ROTATION_Z_DEG,
        )
    return fallback_bbox_min, fallback_bbox_max, [], [], [], 0.0, 0.0


class DragSession:
    """Singleton drag-session state machine (mirrors bk_maya's ``DragSession``)."""

    _instance: DragSession | None = None

    def __init__(self) -> None:
        self._preview_actor: Any = None
        self._tick_handle: Any = None
        self._tick_count = 0

    @classmethod
    def get(cls) -> DragSession:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self, asset_data: dict[str, Any], thumb_path: str) -> None:
        global _active_state
        if _active_state.active:
            log.debug("DragSession already active; ignoring start()")
            return

        bbox_min, bbox_max = _asset_bbox(asset_data)
        prxc = _load_proxor_payload(asset_data)
        bbox_min, bbox_max, lines, mesh, arrow, basis_rotation_x, basis_rotation_z = _preview_from_payload(
            prxc,
            bbox_min,
            bbox_max,
        )
        _active_state = _State(
            asset_data=asset_data,
            thumb_path=thumb_path,
            bbox_min=bbox_min,
            bbox_max=bbox_max,
            active=True,
            proxor_lines=lines,
            proxor_mesh=mesh,
            arrow_lines=arrow,
            basis_rotation_x=basis_rotation_x,
            basis_rotation_z=basis_rotation_z,
        )
        log.info("Drag start: asset=%s bbox_min=%s bbox_max=%s", asset_data.get("name", "?"), bbox_min, bbox_max)
        log.debug(
            "Proxor state after fit: %d lines, %d mesh verts, %d arrow segments",
            len(_active_state.proxor_lines),
            len(_active_state.proxor_mesh),
            len(_active_state.arrow_lines),
        )

        self._preview_actor = viewport_ue.spawn_preview_actor()
        if not _active_state.proxor_lines and not _active_state.proxor_mesh:
            self._start_proxor_fetch(asset_data)

        viewport_ue.reset_wheel_accum()
        viewport_ue.set_wheel_capture_active(True)
        self._tick_count = 0
        self._tick_handle = viewport_ue.install_tick(self._poll_cursor)

    def cancel(self) -> None:
        global _active_state
        if not _active_state.active:
            return
        _active_state.active = False
        _active_state.downloading = False
        viewport_ue.set_wheel_capture_active(False)
        viewport_ue.uninstall_tick(self._tick_handle)
        self._tick_handle = None
        viewport_ue.destroy_preview_actor(self._preview_actor)
        self._preview_actor = None
        log.info("Drag cancelled.")

    def _on_drop(self) -> None:
        global _active_state
        if not _active_state.active:
            return
        state = _active_state
        _active_state.downloading = True
        _active_state.download_progress = 0.0
        _active_state.download_status = "Starting"
        viewport_ue.set_wheel_capture_active(False)
        if not state.has_hit:
            _active_state.active = False
            _active_state.downloading = False
            viewport_ue.uninstall_tick(self._tick_handle)
            self._tick_handle = None
            viewport_ue.destroy_preview_actor(self._preview_actor)
            self._preview_actor = None
            log.info("Drop ignored: no valid surface under cursor.")
            return

        log.info(
            "Drop: asset=%s at %s (floor=%s) - starting model download/import pipeline.",
            state.asset_data.get("name", "?"),
            state.location,
            state.hit_floor,
        )
        try:
            from . import download

            controller = download.DownloadController(
                asset_data=state.asset_data,
                drop_location=state.location,
                drop_normal=state.surface_normal,
                drop_rotation_z=state.rotation_z,
                progress_callback=self._on_download_progress,
                finished_callback=self._on_download_finished,
            )
            controller.start()
        except Exception as exc:  # pragma: no cover - editor-side fallback
            _active_state.download_status = "Failed"
            log.exception("Unhandled model import pipeline start failed: %s", exc)

    def _on_download_progress(self, progress: float, status: str) -> None:
        _active_state.download_progress = max(0.0, min(1.0, float(progress)))
        _active_state.download_status = status

    def _on_download_finished(self, success: bool, status: str) -> None:
        _active_state.download_progress = 1.0 if success else _active_state.download_progress
        _active_state.download_status = status
        _active_state.downloading = False
        _active_state.active = False

    # ── Proxor async fetch ───────────────────────────────────────────────

    def _start_proxor_fetch(self, asset_data: dict[str, Any]) -> None:
        asset_type = asset_data.get("assetType")
        if asset_type not in ("model", "printable"):
            return
        url = _prxc_download_url(asset_data)
        path = _proxor_cache_path(asset_data)
        if not (url and path):
            return
        asset_base_id = asset_data.get("assetBaseId", "")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            log.debug("Could not create proxor cache dir: %s", exc)
            return

        client_lib.prxc_registry.register(asset_base_id, self._on_proxor_ready)

        def _worker() -> None:
            try:
                task_id = client_lib.asset_prxc_download(asset_base_id=asset_base_id, download_url=url, file_path=path)
                log.info("[PROXOR] download scheduled task=%s -> %s", task_id, path)
            except Exception as exc:
                log.debug("asset_prxc_download failed: %s", exc)
                client_lib.prxc_registry.unregister(asset_base_id)

        threading.Thread(target=_worker, name=f"bk-prxc-fetch-{asset_base_id[:8]}", daemon=True).start()

    def _on_proxor_ready(self, prxc_path: str) -> None:
        if not _active_state.active:
            return
        payload = _parse_prxc(prxc_path)
        bbox_min, bbox_max, lines, mesh, arrow, basis_rotation_x, basis_rotation_z = _preview_from_payload(
            payload,
            _active_state.bbox_min,
            _active_state.bbox_max,
        )
        if not lines and not mesh:
            return
        _active_state.bbox_min = bbox_min
        _active_state.bbox_max = bbox_max
        _active_state.proxor_lines = lines
        _active_state.proxor_mesh = mesh
        _active_state.arrow_lines = arrow
        _active_state.basis_rotation_x = basis_rotation_x
        _active_state.basis_rotation_z = basis_rotation_z
        log.info(
            "[PROXOR] live swap-in: %d segments, %d mesh-verts, %d arrow segments", len(lines), len(mesh), len(arrow)
        )

    # ── Poll tick ────────────────────────────────────────────────────────

    def _poll_cursor(self, _delta_seconds: float = 0.0) -> None:
        if not _active_state.active:
            viewport_ue.uninstall_tick(self._tick_handle)
            self._tick_handle = None
            viewport_ue.destroy_preview_actor(self._preview_actor)
            self._preview_actor = None
            return
        self._tick_count += 1

        if _active_state.downloading:
            viewport_ue.update_preview_actor(self._preview_actor, _active_state)
            return

        buttons = viewport_ue.consume_mouse_buttons()
        if buttons.get("rmb_up") or buttons.get("esc"):
            self.cancel()
            return
        if buttons.get("lmb_up"):
            self._on_drop()
            return

        wheel = viewport_ue.consume_wheel_delta()
        if wheel:
            _active_state.rotation_z -= float(wheel) * WHEEL_STEP

        ray = viewport_ue.get_mouse_world_ray()
        if ray is None:
            return
        has_hit, loc, normal, hit_floor = viewport_ue.raycast(*ray)
        _active_state.has_hit = has_hit
        _active_state.hit_floor = hit_floor
        _active_state.surface_normal = normal
        _active_state.location = loc

        viewport_ue.update_preview_actor(self._preview_actor, _active_state)


def start_drag(asset_data: dict[str, Any], thumb_path: str) -> None:
    """Entry point called from the asset bar's tile drag threshold."""
    DragSession.get().start(asset_data, thumb_path)
