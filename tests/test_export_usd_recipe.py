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
