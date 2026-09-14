"""Unreal USD stage import helpers.

Unreal is imported lazily so this module remains usable by the headless test
suite. The USD Importer plugin must be enabled in the target project.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

log = logging.getLogger(__name__)

# Unreal's USD Stage import is expected to already convert the stage's
# authored up-axis/handedness (Blender exports Z-up right-handed) into
# Unreal's Z-up left-handed frame - the same reason ``placement._asset_bbox``
# only needs a Y mirror, not an extra rotation, to match. If a placed asset
# still comes in rotated wrong, adjust these constants (pitch/roll are the
# ones to try first if the model lies on its side or front-to-back - yaw is
# already the live placement spin, see ``rotation_z`` below) rather than
# guessing at the pivot/translation math - the two problems are independent.
_USD_BASIS_PITCH_DEG = 0.0  # rotation around Y (unreal.Rotator's "pitch")
# Confirmed in-editor: the imported USD stage faces 180 deg off from the
# (correctly-oriented) proxor/bbox preview on the same drag - Unreal's USD
# import evidently treats the stage's front axis oppositely from our own
# Blender-front convention. Correct with a fixed yaw offset, not a guess.
_USD_BASIS_YAW_DEG = 180.0  # extra rotation around Z, on top of the live placement yaw
_USD_BASIS_ROLL_DEG = 0.0  # rotation around X (unreal.Rotator's "roll")


def _pivot_corrected_location(
    location: tuple[float, float, float],
    pivot_offset: tuple[float, float, float],
    rotation_z_deg: float,
) -> tuple[float, float, float]:
    """Translate so the asset's bottom-center pivot (not its raw origin) lands on *location*.

    The exported USD keeps the model's original (Blender) origin, which is
    usually not bottom-center. ``pivot_offset`` is the same value computed by
    ``placement._recenter_bottom_center`` for this asset's bbox: how far the
    model's own origin sits from the bottom-center pivot the drag preview
    showed. Placing the actor's origin at ``location - R_z(pivot_offset)``
    makes the bottom-center point - not the raw origin - land on the drop
    point, matching the green preview box.
    """
    rad = math.radians(rotation_z_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    ox, oy, oz = pivot_offset
    rx = -ox * cos_r + oy * sin_r
    ry = -ox * sin_r - oy * cos_r
    return (location[0] + rx, location[1] + ry, location[2] - oz)


def import_stage(
    usda_path: str,
    *,
    location: tuple[float, float, float],
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
    rotation_z: float = 0.0,
    pivot_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> Any:
    """Spawn a ``UsdStageActor`` and place it at the drag drop transform."""
    if not os.path.isfile(usda_path):
        raise FileNotFoundError(f"USD stage does not exist: {usda_path}")

    try:
        import unreal  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Unreal Python is required to import a USD stage.") from exc

    if hasattr(unreal, "UsdStageActor"):
        actor_class = unreal.UsdStageActor
    else:
        raise RuntimeError("USD Importer plugin is not enabled: unreal.UsdStageActor is unavailable.")

    actor_yaw = float(rotation_z) + _USD_BASIS_YAW_DEG
    # Pivot offset must rotate by the SAME total yaw the actor actually ends
    # up with (including the 180 deg basis correction), or the bottom-center
    # pivot lands off the drop point for any non-square bbox.
    actor_location = _pivot_corrected_location(location, pivot_offset, actor_yaw)
    # unreal.Rotator's constructor is (roll, pitch, yaw), NOT (pitch, yaw,
    # roll) - always pass by keyword here to avoid silently rotating the
    # wrong axis (this exact positional mix-up previously sent the live
    # placement yaw into pitch instead).
    rotator = unreal.Rotator(
        roll=_USD_BASIS_ROLL_DEG,
        pitch=_USD_BASIS_PITCH_DEG,
        yaw=actor_yaw,
    )
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        actor_class,
        unreal.Vector(*actor_location),
        rotator,
    )
    if actor is None:
        raise RuntimeError("Unreal failed to spawn a UsdStageActor.")

    if hasattr(actor, "set_root_layer"):
        actor.set_root_layer(usda_path)
    else:
        actor.set_editor_property("root_layer", usda_path)
    actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
    log.info("Imported USD stage: %s", usda_path)
    return actor


__all__ = ["import_stage"]
