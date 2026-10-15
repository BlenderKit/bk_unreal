"""Viewport transforms and explicit native surface-hit outputs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bk_unreal.core import viewport_ue
from bk_unreal.core.placement_transform import surface_basis


def _trace_api(*, blocking: bool = True) -> SimpleNamespace:
    point = SimpleNamespace(x=10.0, y=20.0, z=75.0)
    normal = SimpleNamespace(x=0.0, y=0.0, z=1.0)
    return SimpleNamespace(
        BlendkitViewportLibrary=SimpleNamespace(trace_placement_ray=Mock(return_value=(blocking, point, normal)))
    )


def test_local_to_world_rotates_about_z_and_translates() -> None:
    out = viewport_ue._local_to_world((10.0, 0.0, 5.0), (1.0, 2.0, 3.0), 90.0)

    assert out == pytest.approx((1.0, 12.0, 8.0))


def test_preview_points_align_to_wall_and_spin_in_surface_plane() -> None:
    transform = ((10.0, 20.0, 75.0), surface_basis((1.0, 0.0, 0.0), 90.0))

    assert viewport_ue._transform_point((0.0, 0.0, 0.0), transform) == pytest.approx((10.0, 20.0, 75.0))
    assert viewport_ue._transform_point((0.0, 0.0, 5.0), transform) == pytest.approx((15.0, 20.0, 75.0))
    assert viewport_ue._transform_point((5.0, 0.0, 0.0), transform) == pytest.approx((10.0, 25.0, 75.0))


@pytest.mark.parametrize("blocking", [False, True])
def test_line_trace_reads_native_out_parameters(blocking: bool) -> None:
    unreal = _trace_api(blocking=blocking)

    hit = viewport_ue._line_trace(unreal, "world", "start", "end")

    if blocking:
        assert hit is not None
        assert hit.location.z == 75.0
        assert hit.impact_normal.z == 1.0
    else:
        assert hit is None
    unreal.BlendkitViewportLibrary.trace_placement_ray.assert_called_once_with("start", "end")


@pytest.mark.parametrize("failure", [AttributeError("old binary"), TypeError("opaque HitResult")])
def test_native_trace_failure_logs_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    unreal = _trace_api()
    unreal.BlendkitViewportLibrary.trace_placement_ray.side_effect = failure
    monkeypatch.setattr(viewport_ue, "_warned_line_trace", False)

    assert viewport_ue._line_trace(unreal, "world", "start", "end") is None
    assert viewport_ue._line_trace(unreal, "world", "start", "end") is None
    assert caplog.text.count("rebuild BlendkitViewport") == 1


@pytest.mark.parametrize("blocking", [False, True])
def test_raycast_preserves_elevated_surface_or_falls_back_to_floor(
    monkeypatch: pytest.MonkeyPatch, blocking: bool
) -> None:
    unreal = _trace_api(blocking=blocking)
    monkeypatch.setattr(viewport_ue, "_unreal", lambda: unreal)
    monkeypatch.setattr(viewport_ue, "_editor_world", lambda _unreal: object())
    ray_start = SimpleNamespace(x=0.0, y=0.0, z=100.0)
    ray_end = SimpleNamespace(x=0.0, y=0.0, z=-100.0)

    result = viewport_ue.raycast(ray_start, ray_end)

    if blocking:
        assert result == (True, (10.0, 20.0, 75.0), (0.0, 0.0, 1.0), False)
    else:
        assert result == (True, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), True)
