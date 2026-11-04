"""Optional real-Blender checks: set BLENDKIT_TEST_BLENDER to blender.exe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_blender_normal_export(tmp_path) -> None:
    import pytest

    blender = os.environ.get("BLENDKIT_TEST_BLENDER")
    if not blender:
        pytest.skip("Set BLENDKIT_TEST_BLENDER to run real Blender/USD export checks")
    result = subprocess.run(
        [
            blender,
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(Path(__file__).resolve()),
            "--",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NORMAL_EXPORT_OK" in result.stdout


def _check_export(directory: Path) -> None:
    import bpy
    from pxr import Gf, Sdf, Usd, UsdShade

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bk_client" / "client" / "tools"))
    import export_usd

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    material = bpy.data.materials.new("NormalTest.019")
    material.use_nodes = True
    bpy.context.object.data.materials.append(material)
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    normal_image = bpy.data.images.new("normal_source", width=2, height=2)
    normal_image["blendkit_normal_convention"] = "opengl"
    normal_image.colorspace_settings.name = "Non-Color"
    normal_image.pixels[:] = [0.8, 0.9, 0.5, 1.0] * 4
    normal_image.filepath_raw = str(directory / "normal_source.png")
    normal_image.file_format = "PNG"
    normal_image.save()
    normal_image.pack()
    image = nodes.new("ShaderNodeTexImage")
    image.image = normal_image
    normal = nodes.new("ShaderNodeNormalMap")
    bump = nodes.new("ShaderNodeBump")
    height = nodes.new("ShaderNodeTexNoise")
    links.new(image.outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], bump.inputs["Normal"])
    links.new(height.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], nodes.get("Principled BSDF").inputs["Normal"])
    blend_path = directory / "source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    for target in ("preserve", "unreal"):
        output = export_usd.export_to_usd(str(blend_path), str(directory / f"{target}.usd"), normal_map_target=target)
        stage = Usd.Stage.Open(output)
        if target == "unreal":
            material_prims = [prim for prim in stage.Traverse() if prim.GetTypeName() == "Material"]
            assert any(prim.GetName() == "NormalTest_019_Material" for prim in material_prims)
        normal_shaders = []
        bump_shaders = []
        for prim in stage.Traverse():
            shader = UsdShade.Shader(prim)
            if not shader:
                continue
            shader_id = str(shader.GetIdAttr().Get() or "")
            if shader_id.startswith("ND_normalmap"):
                normal_shaders.append(shader)
                assert prim.GetCustomDataByKey("blendkit:normal:inputConvention") == "opengl"
                expected = "directx" if target == "unreal" else "opengl"
                assert prim.GetCustomDataByKey("blendkit:normal:outputConvention") == expected
            if shader_id.startswith("ND_bump"):
                bump_shaders.append(shader)
                source = shader.GetInput("normal").GetConnectedSource()
                assert source
                assert str(UsdShade.Shader(source[0].GetPrim()).GetIdAttr().Get()).startswith("ND_normalmap")
                assert shader.GetInput("height").GetConnectedSource()
        assert len(normal_shaders) == 1
        assert len(bump_shaders) == (0 if target == "unreal" else 1)
        if target == "unreal":
            source = normal_shaders[0].GetInput("in").GetConnectedSource()
            decoder = UsdShade.Shader(source[0].GetPrim())
            assert decoder.GetIdAttr().Get() == "ND_add_vector3"
            assert tuple(decoder.GetInput("in2").Get()) == (-1, 1, -1)
            image_prims = [
                prim
                for prim in stage.Traverse()
                if prim.GetCustomDataByKey("blendkit:normal:sourceConvention") == "opengl"
            ]
            assert image_prims, (
                [(im.name, im.filepath, im.get("blendkit_normal_convention")) for im in bpy.data.images],
                [
                    (str(prim.GetPath()), str(prim.GetAttribute("inputs:file").Get()), prim.GetCustomData())
                    for prim in stage.Traverse()
                    if "image" in str(prim.GetAttribute("info:id").Get())
                ],
            )
        stage = None

    native_path = str(directory / "native.usda")
    stage = Usd.Stage.CreateNew(native_path)
    for name, sy, by in (("GL", 2, -1), ("DX", -2, 1), ("Default", None, None)):
        texture = UsdShade.Shader.Define(stage, f"/{name}/texture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("normal_source.png"))
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        if sy is not None:
            texture.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, sy, 2, 1))
            texture.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, by, -1, 0))
        surface = UsdShade.Shader.Define(stage, f"/{name}/surface")
        surface.CreateIdAttr("UsdPreviewSurface")
        surface.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(texture.ConnectableAPI(), "rgb")
    stage.GetRootLayer().Save()
    assert export_usd._author_usd_normal_metadata(native_path, "preserve") == 3
    for name, sy, by, convention in (("GL", 2, -1, "opengl"), ("DX", -2, 1, "directx"), ("Default", 2, -1, "opengl")):
        texture = UsdShade.Shader(stage.GetPrimAtPath(f"/{name}/texture"))
        assert tuple(texture.GetInput("scale").Get()) == (2, sy, 2, 1)
        assert tuple(texture.GetInput("bias").Get()) == (-1, by, -1, 0)
        assert texture.GetInput("sourceColorSpace").Get() == "raw"
        assert texture.GetPrim().GetCustomDataByKey("blendkit:normal:inputConvention") == convention
    sys.stdout.write("NORMAL_EXPORT_OK\n")


if __name__ == "__main__":
    _check_export(Path(sys.argv[-1]))
