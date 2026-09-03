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
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from . import client_lib, viewport_ue
from . import prefs as _prefs_mod

log = logging.getLogger(__name__)

WHEEL_STEP = 5.0  # degrees per wheel notch

# Proxor coordinates are a normalized [0,1] bounding volume anchored at the
# bottom-center (Z up, X left), same topology as the asset bbox - so the fit
# below only needs a fixed axis correction, not a guessed value: confirmed
# the proxor's own "up" axis needs a -90 deg rotation about X (not a Z yaw)
# to line up with the bbox's Z-up frame.
_PROXOR_ROTATE_X_DEG = -90.0

# Set False if the hologram is still front/back (or left/right) mirrored
# relative to the placed asset after the X-rotation above - a mirror can't
# be fixed by rotation alone.
_PROXOR_FLIP_Y = True


def _rotate_x(y: float, z: float, degrees: float) -> tuple[float, float]:
    """Rotate (y, z) by *degrees* about the X axis (right-hand rule)."""
    rad = math.radians(degrees)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return y * cos_r - z * sin_r, y * sin_r + z * cos_r


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
    out: dict[str, Any] = {"lines": [], "mesh": []}
    try:
        from ..bk_proxor import prx_format as pf
        from ..bk_proxor._unreal.draw import prx_to_line_segments, prx_to_mesh_triangles
    except Exception as exc:
        log.debug("bk_proxor unavailable: %s", exc)
        return out
    try:
        payload = pf.read_prx(prxc_path)
        scale = _meters_to_internal()
        out["lines"] = prx_to_line_segments(payload, world_scale=scale, flip_y=_PROXOR_FLIP_Y)
        out["mesh"] = prx_to_mesh_triangles(payload, world_scale=scale, flip_y=_PROXOR_FLIP_Y)
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
    return {"lines": [], "mesh": []}


def _bbox_arrow(
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
) -> list[list[tuple[float, float, float]]]:
    """Two floor-level lines from the bbox's front-bottom corners to a tip in front.

    Mirrors the default green bounding-box arrow drawn by the Blender/bk_maya
    proxor handler. Built directly from the already-correct, already-fitted
    asset bbox rather than the raw ``.prx`` mesh space: routing it through
    the proxor library's own raw-space arrow math (``prx_to_arrow_segments``)
    produced a tip offset wildly out of proportion for real (non-unit-cube)
    assets - the bbox is always right-sized/positioned since the wire itself
    is drawn from it, so building the arrow from the same numbers guarantees
    it stays attached and proportional.
    """
    width = bbox_max[0] - bbox_min[0]
    if width <= 0:
        return []
    cx = (bbox_min[0] + bbox_max[0]) / 2.0
    z = bbox_min[2]
    p_left = (bbox_min[0], bbox_min[1], z)
    p_right = (bbox_max[0], bbox_min[1], z)
    tip = (cx, bbox_min[1] - width / 2.0, z)
    return [[p_left, tip], [p_right, tip]]


def _fit_proxor_to_bbox(
    prxc: dict[str, Any],
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
) -> None:
    """Uniformly rescale + translate proxor geometry to sit inside *bbox_min/max*.

    Earlier versions rescaled X/Y/Z independently to force an exact bbox
    match, but a non-uniform scale visibly squishes/stretches the shape
    when the proxor's own aspect ratio doesn't match the bbox's - so this
    uses a single uniform scale (the smallest per-axis ratio, i.e. "contain"
    not "stretch") to preserve proportions, then centers the result on X/Y
    and sits it on the bbox floor (min Z) like a normal placed asset.
    ``_PROXOR_ROTATE_X_DEG`` corrects the proxor's up-axis before fitting.
    """
    lines = prxc.get("lines") or []
    mesh = prxc.get("mesh") or []
    if _PROXOR_ROTATE_X_DEG % 360:
        deg = _PROXOR_ROTATE_X_DEG

        def _pitch(p: tuple) -> tuple[float, float, float]:
            y, z = _rotate_x(p[1], p[2], deg)
            return (p[0], y, z)

        lines = [[_pitch(a), _pitch(b)] for a, b in lines]
        mesh = [_pitch(p) for p in mesh]

    points = [p for seg in lines for p in seg] + list(mesh)
    if not points:
        return
    proxor_min = [min(p[i] for p in points) for i in range(3)]
    proxor_max = [max(p[i] for p in points) for i in range(3)]
    proxor_span = [proxor_max[i] - proxor_min[i] for i in range(3)]
    bbox_span = [bbox_max[i] - bbox_min[i] for i in range(3)]
    ratios = [bbox_span[i] / proxor_span[i] for i in range(3) if proxor_span[i] > 1e-6]
    scale = min(ratios) if ratios else 1.0

    proxor_center = [(proxor_min[i] + proxor_max[i]) / 2.0 for i in range(3)]
    target_center = [(bbox_min[i] + bbox_max[i]) / 2.0 for i in range(2)] + [bbox_min[2] - proxor_min[2] * scale]

    def _fit(p: tuple) -> tuple[float, float, float]:
        x = target_center[0] + (p[0] - proxor_center[0]) * scale
        y = target_center[1] + (p[1] - proxor_center[1]) * scale
        z = target_center[2] + p[2] * scale
        return (x, y, z)

    prxc["lines"] = [[_fit(a), _fit(b)] for a, b in lines]
    prxc["mesh"] = [_fit(p) for p in mesh]


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
    """Return ``(bbox_min, bbox_max)`` in Unreal cm, mirrored to its frame."""
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
        _fit_proxor_to_bbox(prxc, bbox_min, bbox_max)
        _active_state = _State(
            asset_data=asset_data,
            thumb_path=thumb_path,
            bbox_min=bbox_min,
            bbox_max=bbox_max,
            active=True,
            proxor_lines=prxc.get("lines", []),
            proxor_mesh=prxc.get("mesh", []),
            arrow_lines=_bbox_arrow(bbox_min, bbox_max),
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
        _active_state.active = False
        viewport_ue.set_wheel_capture_active(False)
        viewport_ue.uninstall_tick(self._tick_handle)
        self._tick_handle = None
        viewport_ue.destroy_preview_actor(self._preview_actor)
        self._preview_actor = None
        if state.has_hit:
            log.info(
                "Drop: asset=%s at %s (floor=%s) - import/spawn pending Content Browser pipeline.",
                state.asset_data.get("name", "?"),
                state.location,
                state.hit_floor,
            )
        else:
            log.info("Drop ignored: no valid surface under cursor.")

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
        _fit_proxor_to_bbox(payload, _active_state.bbox_min, _active_state.bbox_max)
        lines = payload.get("lines", [])
        mesh = payload.get("mesh", [])
        if not lines and not mesh:
            return
        _active_state.proxor_lines = lines
        _active_state.proxor_mesh = mesh
        log.info("[PROXOR] live swap-in: %d segments, %d mesh-verts", len(lines), len(mesh))

    # ── Poll tick ────────────────────────────────────────────────────────

    def _poll_cursor(self, _delta_seconds: float = 0.0) -> None:
        if not _active_state.active:
            return
        self._tick_count += 1

        buttons = viewport_ue.consume_mouse_buttons()
        if buttons.get("rmb_up") or buttons.get("esc"):
            self.cancel()
            return
        if buttons.get("lmb_up"):
            self._on_drop()
            return

        wheel = viewport_ue.consume_wheel_delta()
        if wheel:
            _active_state.rotation_z += wheel * WHEEL_STEP

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
