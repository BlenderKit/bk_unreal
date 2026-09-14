"""Viewport transform helpers."""

from __future__ import annotations

import pytest
from bk_unreal.core import viewport_ue


def test_local_to_world_rotates_about_z_and_translates() -> None:
    out = viewport_ue._local_to_world((10.0, 0.0, 5.0), (1.0, 2.0, 3.0), 90.0)

    assert out == pytest.approx((1.0, 12.0, 8.0))
