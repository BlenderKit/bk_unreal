"""Placement transform helpers."""

from __future__ import annotations

import math

import pytest
from bk_unreal.core import placement
from bk_unreal.core.placement_transform import rotate_vector, surface_basis


def test_asset_bbox_is_unrotated_fallback() -> None:
    asset = {"bbox_min": [-1.0, -3.0, 0.0], "bbox_max": [1.0, 3.0, 2.0]}

    bbox_min, bbox_max = placement._asset_bbox(asset)

    assert bbox_min == pytest.approx((-100.0, -300.0, 0.0))
    assert bbox_max == pytest.approx((100.0, 300.0, 200.0))


def test_prepare_proxor_payload_uses_own_bounds_and_arrow() -> None:
    payload = {
        "mesh": [(-1.0, -3.0, 2.0), (1.0, 3.0, 6.0), (1.0, -3.0, 2.0)],
        "lines": [[(-1.0, -3.0, 2.0), (1.0, 3.0, 6.0)]],
        "arrow": [[(-1.0, 3.0, 2.0), (0.0, 4.0, 2.0)]],
    }

    placement._prepare_proxor_payload(payload)

    assert payload["bbox_min"] == pytest.approx((-1.0, -3.0, 0.0))
    assert payload["bbox_max"] == pytest.approx((1.0, 3.0, 4.0))
    expected_mesh = [(-1.0, -3.0, 0.0), (1.0, 3.0, 4.0), (1.0, -3.0, 0.0)]
    for point, expected in zip(payload["mesh"], expected_mesh, strict=True):
        assert point == pytest.approx(expected)
    arrow_points = [point for segment in payload["arrow"] for point in segment]
    for point, expected in zip(arrow_points, [(-1.0, 3.0, 0.0), (0.0, 4.0, 0.0)], strict=True):
        assert point == pytest.approx(expected)


@pytest.mark.parametrize("normal", [(0, 0, 1), (0, 1, 1), (1, 0, 0), (0, -1, 0), (0, 0, -1), (0.000001, 0, -1)])
@pytest.mark.parametrize("spin", [0.0, 45.0, 180.0, 725.0])
def test_surface_basis_aligns_up_and_preserves_orthonormal_frame(normal, spin) -> None:
    basis = surface_basis(normal, spin)
    assert rotate_vector((0.0, 0.0, 1.0), basis) == pytest.approx(tuple(v / math.hypot(*normal) for v in normal))
    for i in range(3):
        for j in range(3):
            assert sum(basis[i][k] * basis[j][k] for k in range(3)) == pytest.approx(float(i == j), abs=1e-12)
    x, y, z = basis
    assert (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]) == pytest.approx(z)


@pytest.mark.parametrize("normal", [(0, 0, 0), (float("nan"), 0, 1), (float("inf"), 0, 1)])
def test_surface_basis_rejects_invalid_normals(normal) -> None:
    with pytest.raises(ValueError, match="Invalid placement surface normal"):
        surface_basis(normal, 0.0)
