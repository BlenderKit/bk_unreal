"""Viewport transform helpers."""

from __future__ import annotations

import pytest
from bk_unreal.core import viewport_ue


def test_proxor_basis_maps_vertical_source_length_to_x_positive() -> None:
    out = viewport_ue._local_to_world(
        (0.0, 0.0, 10.0),
        (0.0, 0.0, 0.0),
        0.0,
        -90.0,
        -90.0,
    )

    assert out == pytest.approx((10.0, 0.0, 0.0))
