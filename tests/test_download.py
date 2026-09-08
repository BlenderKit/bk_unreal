"""Download + export pipeline helpers."""

from __future__ import annotations

import os

from bk_unreal.core import download, prefs


def test_resolution_token_matches_blendkit() -> None:
    assert download.resolution_token("512") == "0_5K"
    assert download.resolution_token("1024") == "1K"
    assert download.resolution_token("2048") == "2K"
    assert download.resolution_token("4096") == "4K"
    assert download.resolution_token("8192") == "8K"
    assert download.resolution_token("ORIGINAL") == "ORIGINAL"


def test_model_cache_path_layout() -> None:
    asset = {
        "assetBaseId": "abc123",
        "slug": "chair",
        "id": "99",
        "name": "Chair",
    }
    base = prefs.prefs.global_dir_resolved()
    path = download.model_cache_dir(asset, "2048")
    assert path.startswith(os.path.join(base, "models"))
    leaf = os.path.basename(path)
    assert leaf.startswith("chair_")
    assert "99" in leaf
    assert leaf.endswith("_99") or leaf.startswith("chair_2K_99")


def test_build_export_args_uses_usd_output() -> None:
    blend_path = os.path.abspath(os.path.join("tmp", "chair_99.blend"))
    out_dir = os.path.abspath(os.path.join("tmp", "out"))
    args = download.build_export_args(blend_path, out_dir, "1024")
    assert os.path.abspath(args["blend_path"]) == blend_path
    assert args["out_usd"].endswith(".usd")
    assert os.path.basename(args["out_usd"]) == "chair_99.usd"
    assert args["max_resolution"] == "1024"
    assert args["export_mtlx"] is True


def test_stage_has_self_sublayer_detects_broken_wrapper(tmp_path) -> None:
    wrapper = tmp_path / "chair.usda"
    wrapper.write_text("subLayers = [\n    @./chair.usda@\n]\n", encoding="utf-8")

    assert download._stage_has_self_sublayer(str(wrapper)) is True


def test_stage_has_self_sublayer_accepts_geometry_crate(tmp_path) -> None:
    wrapper = tmp_path / "chair.usda"
    wrapper.write_text("subLayers = [\n    @./chair.usd@\n]\n", encoding="utf-8")

    assert download._stage_has_self_sublayer(str(wrapper)) is False


def test_parse_stdout_prefers_done_over_error() -> None:
    lines = [
        "BK_STATUS export",
        "BK_PROGRESS 0.500 model",
        "BK_DONE C:/blendkit-out/chair.usda",
        "BK_ERROR this should be ignored",
    ]
    parsed = download.parse_stdout_lines(lines)
    assert parsed["done_path"] == "C:/blendkit-out/chair.usda"
    assert parsed["status"] == "export"
    assert parsed["progress"] == 0.5


def test_parse_stdout_still_captures_error() -> None:
    parsed = download.parse_stdout_lines(["BK_ERROR export failed"])
    assert parsed["error"] == "export failed"
