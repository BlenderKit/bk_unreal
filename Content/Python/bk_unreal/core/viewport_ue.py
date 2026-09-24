"""Unreal-editor-specific backend for :mod:`bk_unreal.core.placement`.

All ``unreal`` (and ctypes/Win32) access is guarded/lazy so this module stays
import-safe outside the editor (headless tests, plain Python).

Mouse world-ray
----------------
Unreal's editor Python API has no public "mouse position in the active level
viewport" call (unlike Maya's ``M3dView``), because the level viewport is a
native Slate surface, not a Qt widget the asset-bar window can measure. Two
paths are supported, tried in this order:

1. **C++ fast path** - ``unreal.BlendkitViewportLibrary.get_mouse_world_ray()``,
   if the optional ``BlendkitViewport`` C++ module is compiled and loaded
   (see ``Source/BlendkitViewport``). It uses ``FEditorViewportClient`` /
   ``FSceneView::DeprojectFVector2D`` directly - the same code path Unreal's
   own gizmos use - so it is exact regardless of DPI, zoom, ortho/perspective,
   or which viewport has focus. This is the "more performant/more correct"
   method and is used whenever present.
2. **Python fallback** - approximates the ray from the OS-global cursor
   position (ctypes, Windows-only, mirrors bk_maya's low-level-hook approach
   in spirit) mapped into the foreground window's client area, combined with
   the active viewport camera transform and an assumed FOV. This degrades
   gracefully (logs once, returns ``None``) on non-Windows or when no
   viewport camera info is available.

Rotate / cancel input
----------------------
Mouse-wheel rotation is captured with a Win32 ``WH_MOUSE_LL`` low-level
hook (mirrors bk_maya's approach) since the level viewport is a native
Slate surface that no Qt event filter ever sees. The hook runs on a
dedicated thread with its own message loop (required - Windows silently
uninstalls a hook whose callback blocks the ~300 ms timeout) and only
accumulates wheel deltas into a module-level counter, drained each tick by
:func:`consume_wheel_delta`. On non-Windows platforms, or if the hook
fails to install, Q/E key hold is used as a continuous-rotation fallback.
Button-release/Escape are polled each tick via ``GetAsyncKeyState`` on Windows
and CoreGraphics ``CGEventSource{Button,Key}State`` on macOS (both read live HID
state; the Q/E rotation fallback uses the same path).
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_IS_MACOS = sys.platform == "darwin"
_DEFAULT_FOV_DEG = 90.0

_warned_no_ctypes = False
_warned_no_camera = False

# Debug-draw duration slightly longer than one tick so the preview doesn't
# flicker between ticks even if a frame is dropped. Kept as short as
# possible: anything longer causes visible motion-trail "ghosting" while
# rotating/moving (each stale, still-fading copy renders at its own past
# position/color) - most noticeable on points far from the rotation pivot
# (e.g. the forward arrow's tip) since they sweep a wider arc per tick than
# points near the pivot (e.g. the bbox's own corners).
_DRAW_DURATION = 0.05

# ``draw_debug_line``'s thickness is in *world* units (cm), unlike Maya's
# MUIDrawManager which is always screen-space pixels - a fixed cm value reads
# as a hairline (or invisible) except very close to the camera. These are
# target *pixel* widths; :func:`_units_per_pixel` converts them to world
# units for the object's current distance from the camera each frame.
_BOX_THICKNESS_PX = 1.5
_PROXOR_LINE_THICKNESS_PX = 2.0
_MIN_WORLD_THICKNESS = 0.25  # cm floor so thickness never collapses to 0
_LABEL_OFFSET_PX = 18.0

# Debug TEXT (Canvas-based) is a separate render path from debug LINES (the
# line-batcher) and needs a longer buffer to reliably show up each tick -
# using the same tiny `_DRAW_DURATION` left it invisible.
_TEXT_DRAW_DURATION = 0.5

_COLOR_HIT = (0, 220, 80)  # green - geometry hit
_COLOR_FLOOR = (0, 220, 80)  # green - floor fallback (was cyan; wire+proxor should read as one color)
_COLOR_MISS = (220, 50, 50)  # red - no surface


# ── unreal helpers ───────────────────────────────────────────────────────────


def _unreal() -> Any:
    try:
        import unreal

        return unreal
    except Exception:
        return None


def _editor_world(unreal_mod: Any) -> Any:
    try:
        subsystem = unreal_mod.get_editor_subsystem(unreal_mod.UnrealEditorSubsystem)
        return subsystem.get_editor_world()
    except Exception:
        pass
    try:
        return unreal_mod.EditorLevelLibrary.get_editor_world()
    except Exception:
        return None


def _active_camera_info(unreal_mod: Any) -> tuple[Any, Any] | None:
    """Return ``(location, rotation)`` of the active level-viewport camera."""
    global _warned_no_camera
    try:
        return unreal_mod.UnrealEditorSubsystem().get_level_viewport_camera_info()
    except Exception:
        pass
    try:
        return unreal_mod.EditorLevelLibrary.get_level_viewport_camera_info()
    except Exception:
        pass
    if not _warned_no_camera:
        log.debug("No active-viewport camera info API available.")
        _warned_no_camera = True
    return None


# ── tick registration ────────────────────────────────────────────────────────


def install_tick(callback: Callable[[float], None]) -> Any:
    """Register *callback* on Slate's post-tick (fires every editor frame)."""
    unreal_mod = _unreal()
    if unreal_mod is None:
        return None
    return unreal_mod.register_slate_post_tick_callback(callback)


def uninstall_tick(handle: Any) -> None:
    if handle is None:
        return
    unreal_mod = _unreal()
    if unreal_mod is None:
        return
    try:
        unreal_mod.unregister_slate_post_tick_callback(handle)
    except Exception as exc:
        log.debug("uninstall_tick failed: %s", exc)


# ── input polling (ctypes: Win32 GetAsyncKeyState / macOS CoreGraphics) ─────

_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02
_VK_ESCAPE = 0x1B
_VK_Q = 0x51
_VK_E = 0x45

_prev_lmb = False
_prev_rmb = False

# macOS CoreGraphics HID polling, the counterpart to Win32 GetAsyncKeyState.
# CGEventSourceButtonState / CGEventSourceKeyState read live hardware state from
# any thread with no run loop; reading button/key state needs no Accessibility
# grant (only event *taps* do). Win32 virtual-keys are mapped to macOS codes.
_CG_STATE_COMBINED = 0  # kCGEventSourceStateCombinedSessionState
_MAC_MOUSE_BUTTON = {_VK_LBUTTON: 0, _VK_RBUTTON: 1}  # kCGMouseButtonLeft/Right
_MAC_KEYCODE = {_VK_ESCAPE: 53, _VK_Q: 12, _VK_E: 14}  # macOS virtual keycodes
_cg: Any = None


def _macos_cg() -> Any:
    """Bind (once) the CoreGraphics HID-state functions used for input polling."""
    global _cg
    if _cg is None:
        import ctypes

        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGEventSourceButtonState.restype = ctypes.c_bool
        cg.CGEventSourceButtonState.argtypes = [ctypes.c_int, ctypes.c_uint32]
        cg.CGEventSourceKeyState.restype = ctypes.c_bool
        cg.CGEventSourceKeyState.argtypes = [ctypes.c_int, ctypes.c_uint16]
        _cg = cg
    return _cg


def _macos_key_down(vk: int) -> bool:
    cg = _macos_cg()
    if vk in _MAC_MOUSE_BUTTON:
        return bool(cg.CGEventSourceButtonState(_CG_STATE_COMBINED, _MAC_MOUSE_BUTTON[vk]))
    keycode = _MAC_KEYCODE.get(vk)
    if keycode is None:
        return False
    return bool(cg.CGEventSourceKeyState(_CG_STATE_COMBINED, keycode))


def _key_down(vk: int) -> bool:
    global _warned_no_ctypes
    try:
        if _IS_MACOS:
            return _macos_key_down(vk)
        if _IS_WINDOWS:
            import ctypes

            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        return False
    except Exception:
        if not _warned_no_ctypes:
            log.debug("HID key-state API unavailable; drag input polling disabled.")
            _warned_no_ctypes = True
        return False


def consume_mouse_buttons() -> dict[str, bool]:
    """Return button-release/cancel edges since the last call (polled)."""
    global _prev_lmb, _prev_rmb
    lmb = _key_down(_VK_LBUTTON)
    rmb = _key_down(_VK_RBUTTON)
    lmb_up = _prev_lmb and not lmb
    rmb_up = _prev_rmb and not rmb
    _prev_lmb, _prev_rmb = lmb, rmb
    return {"lmb_up": lmb_up, "rmb_up": rmb_up, "esc": _key_down(_VK_ESCAPE)}


# ── mouse wheel (WH_MOUSE_LL hook, mirrors bk_maya) ─────────────────────────
#
# WHY a low-level hook instead of a Qt wheelEvent: the level viewport is a
# native Slate surface, not a Qt widget, so no Qt event filter ever sees
# wheel events delivered to it. A system-wide ``WH_MOUSE_LL`` hook observes
# the raw Win32 message before it's routed to any window, exactly like
# bk_maya's placement drag. It must run on its own thread with its own
# ``GetMessage`` loop: Windows silently removes a WH_MOUSE_LL hook whose
# callback doesn't return within ~300 ms, and the editor's main thread is
# frequently busy for longer than that while drawing/ticking.

_WM_MOUSEWHEEL_LL = 0x020A
_wheel_accum = 0  # raw signed delta, multiples of 120 per notch
_wheel_capture_active = False  # True while a drag is in progress
_hook_handle = None
_hook_proc_ref = None  # keep the CFUNCTYPE closure alive
_hook_installed = False


class _MSLLHOOKSTRUCT:
    """Lazily-built ctypes structure (avoids importing ctypes at module load)."""

    _cls = None

    @classmethod
    def get(cls):
        if cls._cls is None:
            import ctypes
            from ctypes import wintypes

            class _Impl(ctypes.Structure):
                _fields_ = [
                    ("pt", wintypes.POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            cls._cls = _Impl
        return cls._cls


def _ll_mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
    """Runs on the dedicated hook thread; must return quickly (see above)."""
    global _wheel_accum
    import ctypes

    try:
        if n_code == 0 and w_param == _WM_MOUSEWHEEL_LL and _wheel_capture_active:
            info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT.get()))[0]
            raw = (info.mouseData >> 16) & 0xFFFF
            if raw >= 0x8000:
                raw -= 0x10000
            _wheel_accum += int(raw)
            return 1  # swallow: stop the editor viewport from also dolly-zooming
    except Exception:
        pass  # never let an exception escape a Win32 hook callback
    try:
        return ctypes.windll.user32.CallNextHookEx(0, n_code, w_param, l_param)
    except Exception:
        return 0


def _install_mouse_wheel_hook() -> None:
    global _hook_handle, _hook_proc_ref, _hook_installed
    if _hook_installed or not _IS_WINDOWS:
        return

    import threading

    def _hook_thread_main() -> None:
        global _hook_handle, _hook_proc_ref, _hook_installed
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hookproc_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            _hook_proc_ref = hookproc_t(_ll_mouse_proc)
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc_t, wintypes.HINSTANCE, wintypes.DWORD]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

            hmod = kernel32.GetModuleHandleW(None)
            _hook_handle = user32.SetWindowsHookExW(14, _hook_proc_ref, hmod, 0)  # WH_MOUSE_LL
            if not _hook_handle:
                log.warning("SetWindowsHookExW failed, GetLastError=%d", ctypes.get_last_error())
                _hook_proc_ref = None
                return
            _hook_installed = True
            log.info("Mouse-wheel hook installed (hhook=%s).", _hook_handle)

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("Mouse-wheel hook thread crashed:")

    threading.Thread(target=_hook_thread_main, name="bk_unreal.viewport_ue.WH_MOUSE_LL", daemon=True).start()


def set_wheel_capture_active(active: bool) -> None:
    """Enable/disable wheel-swallowing (call at drag start/end).

    While active, wheel notches are consumed for rotation and never reach
    the editor viewport, so scrolling to rotate the placement preview no
    longer also dolly-zooms the camera.
    """
    global _wheel_capture_active
    _wheel_capture_active = active
    if active:
        _install_mouse_wheel_hook()


def reset_wheel_accum() -> None:
    """Discard any wheel delta accumulated before capture was enabled."""
    global _wheel_accum
    _wheel_accum = 0


_warned_no_realtime_override = False


def set_placement_realtime_active(active: bool) -> None:
    """Force the level viewport to Realtime for the duration of a drag (call at start/end).

    Debug lines/meshes expire against world time, which keeps advancing even
    while a viewport isn't set to Realtime (it then only redraws when
    something invalidates it) - a viewport that goes a beat without
    redrawing lets the short-lived preview lines expire before it ever
    renders them, while the persistent debug-text overlay (no expiry) still
    shows on whatever infrequent redraw does happen. That mismatch is what
    caused "text visible, bbox/proxor not". No-op if the C++ module isn't
    built (falls back to whatever behaviour the viewport already had).
    """
    global _warned_no_realtime_override
    unreal_mod = _unreal()
    if unreal_mod is None:
        return
    lib = getattr(unreal_mod, "BlendkitViewportLibrary", None)
    if lib is None or not hasattr(lib, "set_placement_realtime_override"):
        if not _warned_no_realtime_override:
            log.debug("BlendkitViewportLibrary.set_placement_realtime_override unavailable (rebuild the plugin?).")
            _warned_no_realtime_override = True
        return
    try:
        lib.set_placement_realtime_override(active)
    except Exception as exc:
        if not _warned_no_realtime_override:
            log.debug("set_placement_realtime_override failed: %s", exc)
            _warned_no_realtime_override = True


def consume_wheel_delta() -> float:
    """Return rotate-step notches accumulated since the last call.

    Uses the WH_MOUSE_LL hook (Windows) when available; falls back to
    continuous Q/E key hold on other platforms or if the hook failed.
    """
    global _wheel_accum
    _install_mouse_wheel_hook()
    notches = _wheel_accum / 120.0
    _wheel_accum = 0
    if notches:
        return notches
    delta = 0.0
    if _key_down(_VK_E):
        delta += 1.0
    if _key_down(_VK_Q):
        delta -= 1.0
    return delta


# ── mouse world ray ──────────────────────────────────────────────────────────


def _cpp_mouse_world_ray(unreal_mod: Any) -> tuple[Any, Any] | None:
    lib = getattr(unreal_mod, "BlendkitViewportLibrary", None)
    if lib is None:
        return None
    try:
        valid, start, end = lib.get_mouse_world_ray()
        if not valid:
            return None
        return start, end
    except Exception as exc:
        log.debug("BlendkitViewportLibrary.get_mouse_world_ray failed: %s", exc)
        return None


def _cursor_in_foreground_window() -> tuple[float, float, float, float] | None:
    """Return ``(local_x, local_y, width, height)`` in the foreground window."""
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        local_x = pt.x - origin.x
        local_y = pt.y - origin.y
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return float(local_x), float(local_y), float(width), float(height)
    except Exception as exc:
        log.debug("Foreground-window cursor lookup failed: %s", exc)
        return None


def _python_mouse_world_ray(unreal_mod: Any) -> tuple[Any, Any] | None:
    """Best-effort ray from OS cursor + active camera (see module docstring)."""
    cam = _active_camera_info(unreal_mod)
    cursor = _cursor_in_foreground_window()
    if cam is None or cursor is None:
        return None
    loc, rot = cam
    local_x, local_y, width, height = cursor

    fov = math.radians(_DEFAULT_FOV_DEG)
    aspect = width / height
    # NDC in [-1, 1], y flipped (screen-down -> world-up).
    ndc_x = (local_x / width) * 2.0 - 1.0
    ndc_y = 1.0 - (local_y / height) * 2.0
    tan_half = math.tan(fov / 2.0)
    view_x = ndc_x * tan_half * aspect
    view_y = ndc_y * tan_half

    forward = rot.get_forward_vector()
    right = rot.get_right_vector()
    up = rot.get_up_vector()
    dir_x = forward.x + view_x * right.x + view_y * up.x
    dir_y = forward.y + view_x * right.y + view_y * up.y
    dir_z = forward.z + view_x * right.z + view_y * up.z
    length = math.sqrt(dir_x**2 + dir_y**2 + dir_z**2) or 1.0
    dir_x, dir_y, dir_z = dir_x / length, dir_y / length, dir_z / length

    far = 100_000.0  # cm
    start = unreal_mod.Vector(loc.x, loc.y, loc.z)
    end = unreal_mod.Vector(loc.x + dir_x * far, loc.y + dir_y * far, loc.z + dir_z * far)
    return start, end


def get_mouse_world_ray() -> tuple[Any, Any] | None:
    """Return ``(ray_start, ray_end)`` in world space, or ``None``."""
    unreal_mod = _unreal()
    if unreal_mod is None:
        return None
    ray = _cpp_mouse_world_ray(unreal_mod)
    if ray is not None:
        return ray
    return _python_mouse_world_ray(unreal_mod)


# ── raycast ──────────────────────────────────────────────────────────────────


_warned_line_trace = False


def _line_trace(unreal_mod: Any, world: Any, ray_start: Any, ray_end: Any) -> Any:
    """Return a hit ``HitResult``, or ``None`` on miss/failure.

    Uses an *object-type* query (``WorldStatic`` + ``WorldDynamic``) rather
    than a single trace channel: a channel-based trace only blocks on
    whichever collision *response* a given actor happens to have configured
    for that specific channel (often "Visibility"), which many placed/
    imported meshes leave at "Ignore", making the drag raycast silently miss
    everything and fall through to the floor plane. Object-type queries
    instead match on the actor's *object type*, which defaults to
    ``WorldStatic``/``WorldDynamic`` for nearly all placed geometry and is
    what bk_maya's "hit any visible mesh" raycast effectively mirrors.
    ``trace_complex=True`` gets the exact per-triangle hit/normal instead of
    a simplified collision hull, matching bk_maya's ``closestIntersection``.

    ``unreal.SystemLibrary.line_trace_single_for_objects`` is documented as
    returning ``(bool, HitResult)``, but some engine builds bind it
    differently (a bare ``HitResult`` with ``b_blocking_hit``, or ``None``
    outright on failure) - handle all three shapes defensively so a binding
    mismatch degrades to the floor-plane fallback instead of spamming a
    debug log every tick.
    """
    global _warned_line_trace
    try:
        object_type_query = unreal_mod.ObjectTypeQuery
        object_types = [
            getattr(object_type_query, "ECC_WORLD_STATIC", object_type_query.OBJECT_TYPE_QUERY1),
            getattr(object_type_query, "ECC_WORLD_DYNAMIC", object_type_query.OBJECT_TYPE_QUERY2),
        ]
        result = unreal_mod.SystemLibrary.line_trace_single_for_objects(
            world,
            ray_start,
            ray_end,
            object_types,
            True,
            [],
            unreal_mod.DrawDebugTrace.NONE,
            True,
        )
    except Exception as exc:
        if not _warned_line_trace:
            log.warning("line_trace_single_for_objects raised (falling back to floor plane): %s", exc)
            _warned_line_trace = True
        return None

    if isinstance(result, tuple) and len(result) == 2:
        success, hit = result
        return hit if success else None
    if hasattr(result, "b_blocking_hit"):
        return result if result.b_blocking_hit else None
    if hasattr(result, "location") and (hasattr(result, "impact_normal") or hasattr(result, "normal")):
        return result
    if result is not None and not _warned_line_trace:
        log.warning("line_trace_single returned an unexpected type: %r", type(result))
        _warned_line_trace = True
    return None


def raycast(ray_start: Any, ray_end: Any) -> tuple[bool, tuple[float, float, float], tuple[float, float, float], bool]:
    """Return ``(has_hit, location, normal, hit_floor)`` for the given ray."""
    unreal_mod = _unreal()
    if unreal_mod is None:
        return False, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), False

    world = _editor_world(unreal_mod)
    if world is not None:
        hit = _line_trace(unreal_mod, world, ray_start, ray_end)
        if hit is not None:
            loc = hit.location
            nrm = getattr(hit, "impact_normal", None) or hit.normal
            normal = (nrm.x, nrm.y, nrm.z)
            # Flip a back-face normal toward the camera so a placed asset
            # never ends up aligned upside-down/inverted (mirrors bk_maya).
            dx, dy, dz = ray_end.x - ray_start.x, ray_end.y - ray_start.y, ray_end.z - ray_start.z
            if normal[0] * dx + normal[1] * dy + normal[2] * dz > 0.0:
                normal = (-normal[0], -normal[1], -normal[2])
            return True, (loc.x, loc.y, loc.z), normal, False

    # Floor plane fallback (Z=0, Unreal up-axis).
    ox, oy, oz = ray_start.x, ray_start.y, ray_start.z
    dx, dy, dz = ray_end.x - ox, ray_end.y - oy, ray_end.z - oz
    if abs(dz) > 1e-9:
        t = -oz / dz
        if t > 0:
            return True, (ox + t * dx, oy + t * dy, 0.0), (0.0, 0.0, 1.0), True
    return False, (ox + dx, oy + dy, oz + dz), (0.0, 0.0, 1.0), False


# ── preview (debug-draw, no persistent actor needed) ────────────────────────


def spawn_preview_actor() -> Any:
    """No-op placeholder (debug-draw needs no actor); kept for API symmetry."""
    return None


def destroy_preview_actor(actor: Any) -> None:
    """No-op placeholder; see :func:`spawn_preview_actor`. Also clears any lingering debug text."""
    unreal_mod = _unreal()
    if unreal_mod is None:
        return
    _set_debug_text(unreal_mod, False, (0.0, 0.0, 0.0), "", unreal_mod.LinearColor(1.0, 1.0, 1.0, 1.0))


def _bbox_corners(bbox_min: tuple, bbox_max: tuple) -> list[tuple[float, float, float]]:
    xs = (bbox_min[0], bbox_max[0])
    ys = (bbox_min[1], bbox_max[1])
    zs = (bbox_min[2], bbox_max[2])
    return [(x, y, z) for x in xs for y in ys for z in zs]


_BOX_EDGES = (
    (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
    (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
)  # fmt: skip

_Transform = tuple[tuple[float, float, float], float]


def _local_to_world(pt: tuple, loc: tuple, rot_z_deg: float) -> tuple[float, float, float]:
    x, y, z = pt
    rad = math.radians(rot_z_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
    return (loc[0] + x, loc[1] + y, loc[2] + z)


def _transform_point(pt: tuple, transform: _Transform) -> tuple[float, float, float]:
    loc, rotation_z = transform
    return _local_to_world(pt, loc, rotation_z)


_warned_no_units_per_pixel = False


def _cpp_units_per_pixel(unreal_mod: Any, world_location: tuple) -> float | None:
    global _warned_no_units_per_pixel
    lib = getattr(unreal_mod, "BlendkitViewportLibrary", None)
    if lib is None or not hasattr(lib, "get_world_units_per_pixel"):
        if not _warned_no_units_per_pixel:
            log.debug("BlendkitViewportLibrary.get_world_units_per_pixel unavailable (rebuild the plugin?).")
            _warned_no_units_per_pixel = True
        return None
    try:
        valid, units_per_pixel = lib.get_world_units_per_pixel(unreal_mod.Vector(*world_location))
        return float(units_per_pixel) if valid else None
    except Exception as exc:
        if not _warned_no_units_per_pixel:
            log.debug("BlendkitViewportLibrary.get_world_units_per_pixel failed: %s", exc)
            _warned_no_units_per_pixel = True
        return None


def _python_units_per_pixel(unreal_mod: Any, world_location: tuple) -> float | None:
    """Best-effort cm-per-pixel from the active camera + assumed FOV."""
    cam = _active_camera_info(unreal_mod)
    cursor = _cursor_in_foreground_window()
    if cam is None or cursor is None:
        return None
    loc, _rot = cam
    _, _, _width, height = cursor
    dx, dy, dz = world_location[0] - loc.x, world_location[1] - loc.y, world_location[2] - loc.z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    fov = math.radians(_DEFAULT_FOV_DEG)
    return 2.0 * distance * math.tan(fov / 2.0) / max(height, 1.0)


def _units_per_pixel(unreal_mod: Any, world_location: tuple) -> float:
    """Return world (cm) units covered by one screen pixel at *world_location*."""
    upp = _cpp_units_per_pixel(unreal_mod, world_location)
    if upp is None:
        upp = _python_units_per_pixel(unreal_mod, world_location)
    return upp if upp else 1.0


def update_preview_actor(_actor: Any, state: Any) -> None:
    """Draw the green/cyan/red bbox + proxor wireframe/hologram for one frame."""
    unreal_mod = _unreal()
    if unreal_mod is None:
        return

    color = unreal_mod.LinearColor(*(c / 255.0 for c in _COLOR_MISS), 1.0)
    if state.has_hit:
        rgb = _COLOR_FLOOR if state.hit_floor else _COLOR_HIT
        color = unreal_mod.LinearColor(*(c / 255.0 for c in rgb), 1.0)

    upp = _units_per_pixel(unreal_mod, state.location)
    box_thickness = max(_MIN_WORLD_THICKNESS, _BOX_THICKNESS_PX * upp)
    proxor_thickness = max(_MIN_WORLD_THICKNESS, _PROXOR_LINE_THICKNESS_PX * upp)
    rotation_z = float(state.rotation_z)
    transform = (state.location, rotation_z)

    corners = [_transform_point(p, transform) for p in _bbox_corners(state.bbox_min, state.bbox_max)]
    for a, b in _BOX_EDGES:
        _draw_line(unreal_mod, corners[a], corners[b], color, box_thickness)

    if getattr(state, "downloading", False):
        _draw_proxor_lines_with_reveal(unreal_mod, state, color, proxor_thickness, transform)
    else:
        for seg in state.proxor_lines:
            a = _transform_point(seg[0], transform)
            b = _transform_point(seg[1], transform)
            _draw_line(unreal_mod, a, b, color, proxor_thickness)

    for seg in state.arrow_lines:
        a = _transform_point(seg[0], transform)
        b = _transform_point(seg[1], transform)
        _draw_line(unreal_mod, a, b, color, box_thickness)

    if state.proxor_mesh:
        _draw_proxor_mesh(unreal_mod, state, color, proxor_thickness, transform)

    _draw_asset_label(unreal_mod, state, color, transform)


def _download_reveal_z(state: Any) -> float:
    progress = max(0.0, min(1.0, float(getattr(state, "download_progress", 0.0))))
    return float(state.bbox_min[2]) + (float(state.bbox_max[2]) - float(state.bbox_min[2])) * progress


def _clip_segment_z(a: tuple, b: tuple, z_limit: float) -> tuple[tuple, tuple] | None:
    az = float(a[2])
    bz = float(b[2])
    if az <= z_limit and bz <= z_limit:
        return a, b
    if az > z_limit and bz > z_limit:
        return None
    denom = bz - az
    if abs(denom) < 1e-9:
        return None
    t = (z_limit - az) / denom
    mid = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, z_limit)
    return (a, mid) if az <= z_limit else (mid, b)


def _draw_proxor_lines_with_reveal(
    unreal_mod: Any,
    state: Any,
    color: Any,
    thickness: float,
    transform: _Transform,
) -> None:
    reveal_z = _download_reveal_z(state)
    muted = unreal_mod.LinearColor(color.r * 0.35, color.g * 0.35, color.b * 0.35, 0.35)
    for seg in state.proxor_lines:
        a_local, b_local = seg[0], seg[1]
        a = _transform_point(a_local, transform)
        b = _transform_point(b_local, transform)
        _draw_line(unreal_mod, a, b, muted, max(thickness * 0.65, _MIN_WORLD_THICKNESS))
        clipped = _clip_segment_z(a_local, b_local, reveal_z)
        if clipped is None:
            continue
        ca = _transform_point(clipped[0], transform)
        cb = _transform_point(clipped[1], transform)
        _draw_line(unreal_mod, ca, cb, color, thickness)


def _draw_asset_label(
    unreal_mod: Any,
    state: Any,
    color: Any,
    transform: _Transform,
) -> None:
    """Asset name (+ status while downloading) above the bbox - visible for the whole drag."""
    corners = [_transform_point(p, transform) for p in _bbox_corners(state.bbox_min, state.bbox_max)]
    top_z = max(p[2] for p in corners)
    center_x = sum(p[0] for p in corners) / len(corners)
    center_y = sum(p[1] for p in corners) / len(corners)
    upp = _units_per_pixel(unreal_mod, (center_x, center_y, top_z))
    label_loc = (center_x, center_y, top_z + _LABEL_OFFSET_PX * upp)
    asset_name = str(state.asset_data.get("name") or state.asset_data.get("displayName") or "Asset")
    if getattr(state, "downloading", False):
        status = str(getattr(state, "download_status", "") or "Processing")
        progress = max(0.0, min(1.0, float(getattr(state, "download_progress", 0.0))))
        text = f"{asset_name}\n{status} {round(progress * 100)}%"
    else:
        text = asset_name
    _set_debug_text(unreal_mod, True, label_loc, text, color)


_warned_no_debug_text = False


def _set_debug_text(unreal_mod: Any, enabled: bool, location: tuple, text: str, color: Any) -> None:
    """Show/hide one screen-space text label, preferring the editor-viewport-aware C++ hook.

    ``unreal.SystemLibrary.draw_debug_string`` requires a PlayerController/HUD
    and is therefore invisible in the plain (non-PIE) level editor viewport -
    this tries ``BlendkitViewportLibrary.draw_debug_text_world`` (built into
    the ``BlendkitViewport`` C++ module, hooks ``UDebugDrawService`` instead)
    first, and only falls back to ``draw_debug_string`` (best-effort, may not
    render outside Play) if that module isn't built.
    """
    global _warned_no_debug_text
    lib = getattr(unreal_mod, "BlendkitViewportLibrary", None)
    if lib is not None and hasattr(lib, "draw_debug_text_world"):
        try:
            lib.draw_debug_text_world(enabled, unreal_mod.Vector(*location), text, color)
            return
        except Exception as exc:
            if not _warned_no_debug_text:
                log.debug("BlendkitViewportLibrary.draw_debug_text_world failed: %s", exc)
                _warned_no_debug_text = True
    elif not _warned_no_debug_text:
        log.debug("BlendkitViewportLibrary.draw_debug_text_world unavailable (rebuild the plugin?).")
        _warned_no_debug_text = True
    if not enabled:
        return  # no fallback "clear" - draw_debug_string just expires on its own
    world = _editor_world(unreal_mod)
    if world is None:
        return
    try:
        unreal_mod.SystemLibrary.draw_debug_string(
            world,
            unreal_mod.Vector(*location),
            text,
            None,
            color,
            _TEXT_DRAW_DURATION,
        )
    except Exception as exc:
        log.debug("draw_debug_string failed: %s", exc)


_warned_no_mesh_draw = False


def _draw_proxor_mesh(
    unreal_mod: Any,
    state: Any,
    color: Any,
    fallback_thickness: float,
    transform: _Transform,
) -> None:
    """Draw the proxor hologram fill as an immediate-mode triangle mesh.

    Uses the same green/cyan/red hit-state *color* as the bbox wireframe (a
    fixed hologram tint made the proxor read as a separate, unrelated
    overlay). Vertices are already transformed to world space (matching the
    bbox/line drawing above). Tries, in order: ``UKismetSystemLibrary``'s
    debug-mesh draw (not exposed to Python on every engine build), then the
    plugin's own ``BlendkitViewportLibrary.draw_debug_triangle_mesh`` C++
    helper, then falls back to drawing each triangle's 3 edges as lines so
    the hologram is at least visible (as a wireframe) before the C++ module
    is rebuilt.
    """
    global _warned_no_mesh_draw
    world = _editor_world(unreal_mod)
    if world is None:
        return
    mesh = list(state.proxor_mesh)
    if getattr(state, "downloading", False):
        reveal_z = _download_reveal_z(state)
        shown: list = []
        for i in range(0, len(mesh) - 2, 3):
            tri = mesh[i : i + 3]
            if sum(float(p[2]) for p in tri) / 3.0 <= reveal_z:
                shown.extend(tri)
        mesh = shown
    verts = [unreal_mod.Vector(*_transform_point(p, transform)) for p in mesh]
    n_tris = len(verts) // 3
    if n_tris <= 0:
        return
    verts = verts[: n_tris * 3]
    hologram_color = unreal_mod.LinearColor(color.r, color.g, color.b, 0.35)

    kismet = unreal_mod.SystemLibrary
    if hasattr(kismet, "draw_debug_mesh"):
        try:
            kismet.draw_debug_mesh(
                world,
                verts,
                list(range(len(verts))),
                hologram_color,
                unreal_mod.Vector(0.0, 0.0, 0.0),
                unreal_mod.Rotator(0.0, 0.0, 0.0),
                unreal_mod.Vector(1.0, 1.0, 1.0),
                _DRAW_DURATION,
            )
            return
        except Exception as exc:
            log.debug("draw_debug_mesh failed: %s", exc)

    lib = getattr(unreal_mod, "BlendkitViewportLibrary", None)
    if lib is not None and hasattr(lib, "draw_debug_triangle_mesh"):
        try:
            lib.draw_debug_triangle_mesh(world, verts, hologram_color, _DRAW_DURATION)
            return
        except Exception as exc:
            log.debug("BlendkitViewportLibrary.draw_debug_triangle_mesh failed: %s", exc)

    if not _warned_no_mesh_draw:
        log.debug("No filled debug-mesh draw available; drawing proxor hologram as wireframe (rebuild the plugin?).")
        _warned_no_mesh_draw = True
    for i in range(0, len(verts), 3):
        a, b, c = verts[i], verts[i + 1], verts[i + 2]
        _draw_line(unreal_mod, (a.x, a.y, a.z), (b.x, b.y, b.z), color, fallback_thickness)
        _draw_line(unreal_mod, (b.x, b.y, b.z), (c.x, c.y, c.z), color, fallback_thickness)
        _draw_line(unreal_mod, (c.x, c.y, c.z), (a.x, a.y, a.z), color, fallback_thickness)


def _draw_line(unreal_mod: Any, a: tuple, b: tuple, color: Any, thickness: float) -> None:
    world = _editor_world(unreal_mod)
    if world is None:
        return
    try:
        unreal_mod.SystemLibrary.draw_debug_line(
            world,
            unreal_mod.Vector(*a),
            unreal_mod.Vector(*b),
            color,
            _DRAW_DURATION,
            thickness,
        )
    except Exception as exc:
        log.debug("draw_debug_line failed: %s", exc)
