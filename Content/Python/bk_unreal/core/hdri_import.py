"""Import 360 equirectangular HDR/EXR images as Unreal environment lighting."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)


def import_hdri(image_path: str, *, rotation_z: float = 0.0) -> Any:
    """Save an environment texture and create a Sky Light on the editor thread.

    The original download remains a 360 image. Unreal represents long-lat
    environment textures internally as TextureCube assets, including EXRs.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"HDRI image does not exist: {image_path}")
    if os.path.splitext(image_path)[1].lower() not in (".hdr", ".exr"):
        raise ValueError("HDRI import requires a .hdr or .exr image.")

    import unreal  # type: ignore[import-not-found]

    library = getattr(unreal, "BlendkitViewportLibrary", None)
    if library is None or not hasattr(library, "import_hdri_cubemap"):
        raise RuntimeError("Rebuild the BlendkitViewport native module to enable 360 HDRI imports.")

    directory = os.path.basename(os.path.dirname(image_path))
    name = os.path.splitext(os.path.basename(image_path))[0]
    package_name = "/Game/Blendkit/HDRIs/" + re.sub(r"[^a-zA-Z0-9_]", "_", f"{directory}_{name}")
    texture = (
        unreal.EditorAssetLibrary.load_asset(package_name)
        if unreal.EditorAssetLibrary.does_asset_exist(package_name)
        else None
    )
    if texture is None:
        texture = library.import_hdri_cubemap(image_path, package_name)
    if texture is None:
        raise RuntimeError(f"Unreal failed to import the 360 HDRI image: {image_path}. See the Output Log.")
    if not isinstance(texture, unreal.TextureCube):
        raise TypeError(f"The HDRI destination is not an environment texture: {package_name}")
    if not unreal.EditorAssetLibrary.save_loaded_asset(texture):
        raise RuntimeError(f"Unreal could not save the HDRI texture: {package_name}")

    with unreal.ScopedEditorTransaction("Import Blendkit HDRI"):
        actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0.0, 0.0, 0.0))
        if actor is None:
            raise RuntimeError("Unreal failed to create an HDRI Sky Light.")
        component = actor.get_component_by_class(unreal.SkyLightComponent)
        if component is None:
            unreal.EditorLevelLibrary.destroy_actor(actor)
            raise RuntimeError("The imported HDRI Sky Light has no SkyLightComponent.")
        try:
            actor.set_actor_label(f"Blendkit HDRI - {name}")
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            component.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
            component.set_cubemap(texture)
            component.set_source_cubemap_angle(float(rotation_z))
        except Exception:
            unreal.EditorLevelLibrary.destroy_actor(actor)
            raise
    log.info("Imported 360 HDRI environment: %s", image_path)
    return actor
