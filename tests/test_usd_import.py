"""USD import helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from bk_unreal.core import usd_import
from bk_unreal.core.placement_transform import rotate_vector, surface_basis


def test_import_stage_matches_preview_yaw_and_identity_scale(tmp_path, monkeypatch) -> None:
    stage_path = tmp_path / "asset.usda"
    stage_path.write_text("#usda 1.0\n", encoding="utf-8")
    spawned = {}

    class Actor:
        def set_root_layer(self, path: str) -> None:
            spawned["root_layer"] = path

        def set_actor_scale3d(self, scale) -> None:
            spawned["scale"] = scale

    unreal = SimpleNamespace()
    unreal.UsdStageActor = object
    unreal.Vector = lambda x, y, z: (x, y, z)
    unreal.Rotator = lambda roll=0.0, pitch=0.0, yaw=0.0: (roll, pitch, yaw)

    def spawn_actor_from_class(actor_class, location, rotator):
        spawned["actor_class"] = actor_class
        spawned["location"] = location
        spawned["rotator"] = rotator
        return Actor()

    unreal.EditorLevelLibrary = SimpleNamespace(spawn_actor_from_class=spawn_actor_from_class)
    monkeypatch.setitem(sys.modules, "unreal", unreal)

    actor = usd_import.import_stage(
        str(stage_path),
        location=(10.0, 20.0, 30.0),
        normal=(0.0, 0.0, 1.0),
        rotation_z=45.0,
    )

    assert isinstance(actor, Actor)
    assert spawned["root_layer"] == str(stage_path)
    assert spawned["location"] == (10.0, 20.0, 30.0)
    assert spawned["rotator"] == (0.0, 0.0, 225.0)
    assert spawned["scale"] == (1.0, 1.0, 1.0)


@pytest.mark.parametrize("normal", [(1.0, 0.0, 0.0), (0.0, 1.0, 1.0), (0.0, 0.0, -1.0)])
def test_import_stage_aligns_surface_and_keeps_pivot_on_hit(tmp_path, monkeypatch, normal) -> None:
    stage_path = tmp_path / "asset.usda"
    stage_path.write_text("#usda 1.0\n", encoding="utf-8")
    spawned = {}

    class Actor:
        def set_root_layer(self, path: str) -> None:
            pass

        def set_actor_scale3d(self, scale) -> None:
            spawned["scale"] = scale

    def make_rot_from_xz(x_axis, z_axis):
        spawned["axes"] = (x_axis, z_axis)
        return "surface_rotation"

    def spawn_actor_from_class(actor_class, location, rotator):
        spawned["location"] = location
        spawned["rotator"] = rotator
        return Actor()

    unreal = SimpleNamespace(
        UsdStageActor=object,
        Vector=lambda *args: args,
        Rotator=lambda **kwargs: kwargs,
        MathLibrary=SimpleNamespace(make_rot_from_xz=make_rot_from_xz),
        EditorLevelLibrary=SimpleNamespace(spawn_actor_from_class=spawn_actor_from_class),
    )
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    pivot = (15.0, -20.0, -5.0)
    hit = (10.0, 20.0, 75.0)
    usd_import.import_stage(
        str(stage_path), location=hit, normal=normal, rotation_z=35.0, pivot_offset=pivot, scale=2.0
    )

    basis = surface_basis(normal, 215.0)
    assert spawned["axes"][0] == pytest.approx(basis[0])
    assert spawned["axes"][1] == pytest.approx(basis[2])
    assert spawned["rotator"] == "surface_rotation"
    offset = rotate_vector(pivot, basis)
    bottom_center = tuple(spawned["location"][i] + 2.0 * offset[i] for i in range(3))
    assert bottom_center == pytest.approx(hit)
