"""Tests for the bundled Blender USD export recipe."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_recipe() -> ModuleType:
    recipe_path = Path(__file__).resolve().parents[1] / "bk_client" / "client" / "tools" / "export_usd.py"
    spec = importlib.util.spec_from_file_location("bk_test_export_usd", recipe_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bpy", ModuleType("bpy"))
    spec.loader.exec_module(module)
    return module


def test_mtlx_reference_layer_sanitizes_materialx_target_paths(tmp_path) -> None:
    recipe = _load_recipe()
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    mtlx_path = materials_dir / "0000_round_lantern_001.mtlx"
    mtlx_path.write_text(
        '<materialx><surfacematerial name="0000_round_lantern_2" type="material" /></materialx>',
        encoding="utf-8",
    )
    layer_path = tmp_path / "asset.usda"

    wired = recipe.write_mtlx_reference_layer(
        str(layer_path),
        "./asset.usd",
        {
            "materials": [
                {
                    "name": "000_round_lantern.001",
                    "mtlx_file": "materials/0000_round_lantern_001.mtlx",
                    "success": True,
                }
            ]
        },
    )

    assert wired == 1
    text = layer_path.read_text(encoding="utf-8")
    assert "</MaterialX/Materials/_000_round_lantern_2>" in text
    assert "</MaterialX/Materials/0000_round_lantern_2>" not in text


def test_directx_normal_map_conversion_flips_only_green_and_is_idempotent(tmp_path) -> None:
    recipe = _load_recipe()
    mtlx_path = tmp_path / "normal.mtlx"
    mtlx_path.write_text(
        """\
<materialx>
  <nodegraph name="NG_material">
    <image name="normal_texture" type="vector3" />
    <normalmap name="normal_map" type="vector3">
      <input name="in" type="vector3" nodename="normal_texture" />
    </normalmap>
    <standard_surface name="surface" type="surfaceshader">
      <input name="normal" type="vector3" nodename="normal_map" />
    </standard_surface>
  </nodegraph>
</materialx>
""",
        encoding="utf-8",
    )

    assert recipe._convert_mtlx_normal_maps_to_directx(str(mtlx_path)) == 1
    assert recipe._convert_mtlx_normal_maps_to_directx(str(mtlx_path)) == 0

    root = recipe.ET.parse(mtlx_path).getroot()
    graph = root.find("nodegraph")
    assert graph is not None
    nodes = {node.get("name"): node for node in graph}
    normal_map = nodes["normal_map"]
    combine = nodes["bk_dx_normal_combine_0"]
    subtract = nodes["bk_dx_normal_subtract_0"]
    separate = nodes["bk_dx_normal_separate_0"]

    assert normal_map.find("input[@name='in']").get("nodename") == "bk_dx_normal_combine_0"
    assert separate.find("input[@name='in']").get("nodename") == "normal_texture"
    assert subtract.find("input[@name='in1']").get("value") == "1"
    assert subtract.find("input[@name='in2']").attrib == {
        "name": "in2",
        "type": "float",
        "nodename": "bk_dx_normal_separate_0",
        "output": "outy",
    }
    assert combine.find("input[@name='in1']").get("output") == "outx"
    assert combine.find("input[@name='in2']").get("nodename") == "bk_dx_normal_subtract_0"
    assert combine.find("input[@name='in3']").get("output") == "outz"
