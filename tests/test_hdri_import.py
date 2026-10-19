"""360 HDRI environment import without requiring the Unreal editor."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bk_unreal.core import hdri_import


@pytest.fixture
def unreal_import(tmp_path, monkeypatch):
    image = tmp_path / "sky_version" / "sky_2K.exr"
    image.parent.mkdir()
    image.write_bytes(b"test-exr")

    class TextureCube:
        pass

    texture = TextureCube()
    actor = MagicMock()
    component = actor.get_component_by_class.return_value
    unreal = SimpleNamespace(
        BlendkitViewportLibrary=SimpleNamespace(import_hdri_cubemap=MagicMock(return_value=texture)),
        TextureCube=TextureCube,
        EditorAssetLibrary=SimpleNamespace(
            does_asset_exist=MagicMock(return_value=False),
            load_asset=MagicMock(return_value=None),
            save_loaded_asset=MagicMock(return_value=True),
        ),
        ScopedEditorTransaction=MagicMock(),
        EditorLevelLibrary=SimpleNamespace(
            spawn_actor_from_class=MagicMock(return_value=actor),
            destroy_actor=MagicMock(),
        ),
        SkyLight=object,
        SkyLightComponent=object,
        Vector=lambda *args: args,
        ComponentMobility=SimpleNamespace(MOVABLE="movable"),
        SkyLightSourceType=SimpleNamespace(SLS_SPECIFIED_CUBEMAP="specified"),
    )
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    return image, unreal, actor, component, texture


@pytest.mark.parametrize("extension", [".hdr", ".exr"])
def test_imports_360_image_and_configures_sky_light(unreal_import, extension) -> None:
    image, unreal, actor, component, texture = unreal_import
    image = image.rename(image.with_suffix(extension))

    assert hdri_import.import_hdri(str(image), rotation_z=42.0) is actor

    unreal.BlendkitViewportLibrary.import_hdri_cubemap.assert_called_once_with(
        str(image), "/Game/Blendkit/HDRIs/sky_version_sky_2K"
    )
    unreal.EditorAssetLibrary.save_loaded_asset.assert_called_once_with(texture)
    component.set_mobility.assert_called_once_with("movable")
    component.set_editor_property.assert_called_once_with("source_type", "specified")
    component.set_cubemap.assert_called_once_with(texture)
    component.set_source_cubemap_angle.assert_called_once_with(42.0)


def test_reuses_saved_environment_texture(unreal_import) -> None:
    image, unreal, _actor, _component, texture = unreal_import
    unreal.EditorAssetLibrary.does_asset_exist.return_value = True
    unreal.EditorAssetLibrary.load_asset.return_value = texture
    hdri_import.import_hdri(str(image))
    unreal.BlendkitViewportLibrary.import_hdri_cubemap.assert_not_called()


def test_missing_native_importer_reports_rebuild(unreal_import) -> None:
    image, unreal, _actor, _component, _texture = unreal_import
    unreal.BlendkitViewportLibrary = None
    with pytest.raises(RuntimeError, match="Rebuild"):
        hdri_import.import_hdri(str(image))
    unreal.EditorLevelLibrary.spawn_actor_from_class.assert_not_called()


@pytest.mark.parametrize("failure", ["decode", "save", "spawn", "component", "configure", "wrong_asset"])
def test_import_failure_is_not_reported_as_success(unreal_import, failure) -> None:
    image, unreal, actor, component, _texture = unreal_import
    if failure == "decode":
        unreal.BlendkitViewportLibrary.import_hdri_cubemap.return_value = None
    elif failure == "save":
        unreal.EditorAssetLibrary.save_loaded_asset.return_value = False
    elif failure == "spawn":
        unreal.EditorLevelLibrary.spawn_actor_from_class.return_value = None
    elif failure == "component":
        actor.get_component_by_class.return_value = None
    elif failure == "configure":
        component.set_cubemap.side_effect = RuntimeError("Invalid texture")
    else:
        unreal.EditorAssetLibrary.does_asset_exist.return_value = True
        unreal.EditorAssetLibrary.load_asset.return_value = object()

    with pytest.raises((RuntimeError, TypeError)):
        hdri_import.import_hdri(str(image))

    if failure in ("component", "configure"):
        unreal.EditorLevelLibrary.destroy_actor.assert_called_once_with(actor)
    else:
        unreal.EditorLevelLibrary.destroy_actor.assert_not_called()
