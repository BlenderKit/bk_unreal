"""Tests for the bundled Blender USD export recipe."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest


def _load_recipe() -> ModuleType:
    recipe_path = Path(__file__).resolve().parents[1] / "bk_client" / "client" / "tools" / "export_usd.py"
    spec = importlib.util.spec_from_file_location("bk_test_export_usd", recipe_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bpy", ModuleType("bpy"))
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("open_same_file", "expected_reload"), [(True, False), (False, True)])
def test_main_skips_reloading_blend_already_open(tmp_path, monkeypatch, open_same_file, expected_reload) -> None:
    recipe = _load_recipe()
    blend = tmp_path / "asset.blend"
    blend.write_bytes(b"BLENDER")
    opened = str(blend) if open_same_file else str(tmp_path / "other.blend")
    monkeypatch.setattr(recipe, "bpy", SimpleNamespace(data=SimpleNamespace(filepath=opened)))
    monkeypatch.setattr(recipe, "_parse_args", lambda: {"blend_path": str(blend), "out_usd": str(tmp_path / "a.usd")})
    calls: list[bool] = []

    def _fake_export(*_args, reload_blend: bool, **_kwargs) -> str:
        calls.append(reload_blend)
        return str(tmp_path / "a.usda")

    monkeypatch.setattr(recipe, "export_to_usd", _fake_export)

    assert recipe.main() == 0
    assert calls == [expected_reload]


@pytest.mark.parametrize(("export_mtlx", "mtlx_references"), [(False, True), (True, False), (False, False)])
def test_unreal_export_requires_materialx_sidecars(export_mtlx, mtlx_references) -> None:
    recipe = _load_recipe()
    with pytest.raises(ValueError, match="requires export_mtlx and mtlx_references"):
        recipe.export_to_usd(
            "source.blend",
            "target.usd",
            normal_map_target="unreal",
            export_mtlx=export_mtlx,
            mtlx_references=mtlx_references,
        )


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


@pytest.mark.parametrize("image_type", ["color3", "vector3"])
@pytest.mark.parametrize("upstream_flip", [False, True])
@pytest.mark.parametrize("encoded", [(0.5, 0.5, 1.0), (0.8, 0.9, 0.5), (0.2, 0.1, 0.5)])
def test_unreal_decoding_preserves_source_graph_and_flips_signed_y(
    tmp_path, image_type, upstream_flip, encoded
) -> None:
    recipe = _load_recipe()
    mtlx_path = tmp_path / "normal.mtlx"
    source = "flipped" if upstream_flip else "normal_texture"
    mtlx_path.write_text(
        f"""\
<materialx>
  <nodegraph name="NG_material">
    <image name="normal_texture" type="{image_type}" />
    <multiply name="flip_scale" type="vector3">
      <input name="in1" type="vector3" nodename="normal_texture" />
      <input name="in2" type="vector3" value="1,-1,1" />
    </multiply>
    <add name="flipped" type="vector3">
      <input name="in1" type="vector3" nodename="flip_scale" />
      <input name="in2" type="vector3" value="0,1,0" />
    </add>
    <normalmap name="normal_map" type="vector3">
      <input name="in" type="vector3" nodename="{source}" />
      <input name="scale" type="float" value="0.7" />
    </normalmap>
    <bump name="bump" type="vector3">
      <input name="normal" type="vector3" nodename="normal_map" />
      <input name="height" type="float" value="0.1" />
    </bump>
    <output name="normal" type="vector3" nodename="bump" />
  </nodegraph>
</materialx>
""",
        encoding="utf-8",
    )
    assert recipe._prepare_mtlx_normals_for_unreal(str(mtlx_path)) == 2
    content = mtlx_path.read_bytes()
    assert recipe._prepare_mtlx_normals_for_unreal(str(mtlx_path)) == 0
    assert mtlx_path.read_bytes() == content
    graph = recipe.ET.parse(mtlx_path).getroot().find("nodegraph")
    nodes = {node.get("name"): node for node in graph}

    def evaluate_input(input_node):
        if input_node.get("nodename"):
            return evaluate(input_node.get("nodename"))
        return tuple(float(value) for value in input_node.get("value").split(","))

    def evaluate(name):
        node = nodes[name]
        if node.tag == "image":
            assert node.get("type") == "vector3"
            assert node.get("colorspace") == "raw"
            return tuple(2 * value - 1 for value in encoded)
        if node.tag in ("convert", "dot"):
            return evaluate_input(node.find("input[@name='in']"))
        a = evaluate_input(node.find("input[@name='in1']"))
        b = evaluate_input(node.find("input[@name='in2']"))
        if node.tag == "multiply":
            return tuple(x * y for x, y in zip(a, b, strict=True))
        assert node.tag == "add"
        return tuple(x + y for x, y in zip(a, b, strict=True))

    normal = nodes["normal_map"]
    actual = evaluate_input(normal.find("input[@name='in']"))
    green = 1 - encoded[1] if upstream_flip else encoded[1]
    assert actual == pytest.approx((2 * encoded[0] - 1, 1 - 2 * green, 2 * encoded[2] - 1))
    assert normal.find("input[@name='scale']").get("value") == "0.7"
    assert nodes["bump"].tag == "normalize"
    assert graph.find("heighttonormal/input[@name='in']").get("value") == "0.1"
    assert any(node.find("input[@nodename='normal_map']") is not None for node in graph)


def test_normal_repair_does_not_replace_bump_or_discard_height(tmp_path) -> None:
    recipe = _load_recipe()
    path = tmp_path / "bump.mtlx"
    path.write_text(
        """\
<materialx><nodegraph name="NG">
  <image name="texture" type="color3" nodedef="ND_image_color3" />
  <bump name="bump" type="vector3" nodedef="ND_bump_vector3">
    <input name="normal" type="vector3" nodename="texture" />
    <input name="height" type="float" value="0.1" />
  </bump>
</nodegraph></materialx>
""",
        encoding="utf-8",
    )
    assert recipe._fix_mtlx_normal_maps(str(path)) == 1
    assert recipe._fix_mtlx_normal_maps(str(path)) == 0
    graph = recipe.ET.parse(path).getroot().find("nodegraph")
    bump = graph.find("bump")
    assert bump.find("input[@name='height']").get("value") == "0.1"
    normal = graph.find("normalmap")
    assert bump.find("input[@name='normal']").get("nodename") == normal.get("name")
    assert normal.find("input[@name='in']").get("nodename") == "texture"


def test_unreal_material_name_matches_usd_prim_without_nodegraph_collision(tmp_path) -> None:
    recipe = _load_recipe()
    path = tmp_path / "material.mtlx"
    path.write_text(
        """\
<materialx>
  <nodegraph name="Rock_19"><output name="color" type="color3" value="0.5,0.5,0.5" /></nodegraph>
  <standard_surface name="surface" type="surfaceshader">
    <input name="base_color" type="color3" nodegraph="Rock_19" output="color" />
  </standard_surface>
  <surfacematerial name="Rock_20" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="surface" />
  </surfacematerial>
</materialx>
""",
        encoding="utf-8",
    )
    recipe._prepare_mtlx_normals_for_unreal(str(path), material_name="Rock-19")
    root = recipe.ET.parse(path).getroot()
    assert root.find("surfacematerial").get("name") == "Rock_19"
    graph_name = root.find("nodegraph").get("name")
    assert graph_name != "Rock_19"
    assert root.find("standard_surface/input").get("nodegraph") == graph_name
    content = path.read_bytes()
    recipe._prepare_mtlx_normals_for_unreal(str(path), material_name="Rock-19")
    assert path.read_bytes() == content


@pytest.mark.parametrize("source_type", ["color3", "vector3", "color4", "vector4"])
def test_scalar_connections_use_valid_typed_nodes(tmp_path, source_type) -> None:
    recipe = _load_recipe()
    path = tmp_path / "scalar.mtlx"
    path.write_text(
        f"""\
<materialx><nodegraph name="NG">
  <image name="height" type="{source_type}" />
  <bump name="bump" type="vector3"><input name="height" type="float" nodename="height" /></bump>
</nodegraph></materialx>
""",
        encoding="utf-8",
    )
    assert recipe._fix_mtlx_scalar_connections(str(path)) == 1
    assert recipe._fix_mtlx_scalar_connections(str(path)) == 0
    graph = recipe.ET.parse(path).getroot().find("nodegraph")
    scalar = graph.find("dotproduct")
    vector_type = f"vector{source_type[-1]}"
    assert scalar.get("nodedef") == f"ND_dotproduct_{vector_type}"
    assert scalar.find("input[@name='in1']").get("type") == vector_type
    assert graph.find("bump/input").get("nodename") == scalar.get("name")


def test_mtlx_names_become_valid_usd_identifiers(tmp_path, monkeypatch) -> None:
    recipe = _load_recipe()
    monkeypatch.setattr(recipe, "log", lambda *_args: None)
    path = tmp_path / "06_Material.mtlx"
    path.write_text(
        '<materialx version="1.39">'
        '<nodegraph name="06_Material">'
        '<image name="1_img" type="color3"><input name="file" type="filename" value="a.png" /></image>'
        '<output name="base_color" type="color3" nodename="1_img" />'
        "</nodegraph>"
        '<standard_surface name="2_surface" type="surfaceshader">'
        '<input name="base_color" type="color3" nodegraph="06_Material" output="base_color" />'
        "</standard_surface>"
        '<surfacematerial name="_6_Material" type="material">'
        '<input name="surfaceshader" type="surfaceshader" nodename="2_surface" />'
        "</surfacematerial>"
        "</materialx>",
        encoding="utf-8",
    )

    assert recipe._sanitize_mtlx_names(str(path)) == 3

    text = path.read_text(encoding="utf-8")
    assert '<nodegraph name="_6_Material_">' in text
    assert 'nodegraph="_6_Material_" output="base_color"' in text
    assert '<image name="__img"' in text and 'nodename="__img"' in text
    assert '<standard_surface name="__surface"' in text and 'nodename="__surface"' in text
    assert '<surfacematerial name="_6_Material"' in text


class _FakeMtlxLibrary:
    """Minimal stand-in for MaterialX's stdlib document (MaterialX isn't installed in the test venv)."""

    _OUTPUTS: ClassVar[dict] = {"ND_separate3_color3": {"outr": "float", "outg": "float", "outb": "float"}}
    _CONVERTS: ClassVar[set] = {"ND_convert_color4_color3", "ND_convert_color3_vector3", "ND_convert_float_color3"}

    def getNodeDef(self, name):
        if name in self._CONVERTS:
            return SimpleNamespace(getActiveOutput=lambda _name: None)
        outputs = self._OUTPUTS.get(name)
        if outputs is None:
            return None
        return SimpleNamespace(
            getActiveOutput=lambda port: SimpleNamespace(getType=lambda: outputs[port]) if port in outputs else None
        )


def test_mtlx_type_mismatches_get_convert_nodes(tmp_path, monkeypatch) -> None:
    recipe = _load_recipe()
    monkeypatch.setattr(recipe, "log", lambda *_args: None)
    path = tmp_path / "m.mtlx"
    path.write_text(
        '<materialx version="1.39"><nodegraph name="NG">'
        '<ramp name="ramp" type="color4" nodedef="ND_ramp" />'
        '<image name="nrm" type="color3" nodedef="ND_image_color3" />'
        '<separate3 name="sep" type="multioutput" nodedef="ND_separate3_color3" />'
        '<mix name="mix" type="color3"><input name="fg" type="color3" nodename="ramp" />'
        '<input name="bg" type="color3" nodename="ramp" /><input name="mix" type="float" nodename="sep" output="outr" />'
        "</mix>"
        '<separate3 name="vec" type="multioutput"><input name="in" type="vector3" nodename="nrm" /></separate3>'
        '<combine3 name="c" type="color3"><input name="in1" type="color3" nodename="sep" output="outg" /></combine3>'
        '<output name="base_color" type="color3" nodename="ramp" />'
        "</nodegraph></materialx>",
        encoding="utf-8",
    )

    assert recipe._repair_mtlx_types(str(path), _FakeMtlxLibrary()) == 5

    root = recipe.ET.parse(path).getroot()
    graph = root.find("nodegraph")
    converts = {node.get("name"): node for node in graph.findall("convert")}
    assert {node.get("nodedef") for node in converts.values()} == {
        "ND_convert_color4_color3",
        "ND_convert_color3_vector3",
        "ND_convert_float_color3",
    }
    # One shared convert per (source, output, type); the float input of a multioutput keeps its output.
    assert len(converts) == 3
    mix = graph.find("mix")
    assert mix.find("input[@name='fg']").get("nodename") == mix.find("input[@name='bg']").get("nodename")
    assert mix.find("input[@name='mix']").get("output") == "outr"
    float_convert = next(n for n in converts.values() if n.get("nodedef") == "ND_convert_float_color3")
    assert float_convert.find("input").attrib == {"name": "in", "type": "float", "nodename": "sep", "output": "outg"}
    assert graph.find("combine3/input").get("output") is None
    assert converts[graph.find("output").get("nodename")].get("nodedef") == "ND_convert_color4_color3"


def test_mtlx_scalar_fix_covers_nodegraph_outputs(tmp_path, monkeypatch) -> None:
    recipe = _load_recipe()
    monkeypatch.setattr(recipe, "log", lambda *_args: None)
    path = tmp_path / "m.mtlx"
    path.write_text(
        '<materialx version="1.39"><nodegraph name="NG">'
        '<power name="gamma" type="color3" nodedef="ND_power_color3FA" />'
        '<output name="specular_roughness" type="float" nodename="gamma" />'
        "</nodegraph></materialx>",
        encoding="utf-8",
    )

    assert recipe._fix_mtlx_scalar_connections(str(path)) == 1

    graph = recipe.ET.parse(path).getroot().find("nodegraph")
    scalar = graph.find(f"dotproduct[@name='{graph.find('output').get('nodename')}']")
    assert scalar is not None and scalar.get("type") == "float"


def test_main_removes_partial_outputs_on_failure(tmp_path, monkeypatch) -> None:
    recipe = _load_recipe()
    blend = tmp_path / "asset.blend"
    blend.write_bytes(b"BLENDER")
    out_usd = tmp_path / "a_unreal.usd"
    monkeypatch.setattr(recipe, "bpy", SimpleNamespace(data=SimpleNamespace(filepath="")))
    monkeypatch.setattr(recipe, "_parse_args", lambda: {"blend_path": str(blend), "out_usd": str(out_usd)})

    def _failing_export(*_args, **_kwargs) -> str:
        out_usd.write_bytes(b"crate")
        out_usd.with_suffix(".usda").write_text("#usda 1.0", encoding="utf-8")
        raise RuntimeError("usdMtlx failed")

    monkeypatch.setattr(recipe, "export_to_usd", _failing_export)

    assert recipe.main() == 1
    assert not out_usd.exists()
    assert not out_usd.with_suffix(".usda").exists()
