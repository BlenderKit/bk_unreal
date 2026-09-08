"""Placement transform helpers."""

from __future__ import annotations

import pytest
from bk_unreal.core import placement


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
    assert payload["mesh"] == pytest.approx([(-1.0, -3.0, 0.0), (1.0, 3.0, 4.0), (1.0, -3.0, 0.0)])
    assert [p for seg in payload["arrow"] for p in seg] == pytest.approx([(-1.0, 3.0, 0.0), (0.0, 4.0, 0.0)])
