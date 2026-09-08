"""Unreal USD stage import helpers.

Unreal is imported lazily so this module remains usable by the headless test
suite. The USD Importer plugin must be enabled in the target project.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def import_stage(
    usda_path: str,
    *,
    location: tuple[float, float, float],
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
    rotation_z: float = 0.0,
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

    rotator = unreal.Rotator(0.0, float(rotation_z), 0.0)
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        actor_class,
        unreal.Vector(*location),
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
