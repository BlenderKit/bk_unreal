"""Download + export pipeline helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from bk_unreal.core import client_lib, download, editor_thread, prefs


def _hdri_controller():
    return download.DownloadController(
        asset_data={"assetBaseId": "sky-base", "id": "sky-version", "slug": "sunny-sky", "assetType": "hdr"},
        drop_location=(0.0, 0.0, 0.0),
        drop_normal=(0.0, 0.0, 1.0),
        drop_rotation_z=30.0,
    )


@pytest.mark.parametrize("extension", [".hdr", ".exr"])
def test_hdri_download_keeps_image_format_and_resolution_cache(tmp_path, monkeypatch, extension) -> None:
    monkeypatch.setattr(prefs.prefs, "global_dir_resolved", lambda: str(tmp_path))
    monkeypatch.setattr(prefs.prefs, "resolution", "2048")
    calls = []
    monkeypatch.setattr(
        client_lib,
        "get_download_url",
        lambda *args: {"can_download": True, "download_url": f"https://example.test/sky{extension}?signature=123"},
    )

    def fake_download(url, path):
        calls.append(path)
        Path(path).write_bytes(b"image-data")
        return True

    monkeypatch.setattr(client_lib, "blocking_file_download", fake_download)
    controller = _hdri_controller()
    image_path = controller._download_hdri()
    assert Path(image_path).is_relative_to(tmp_path / "hdrs")
    assert Path(image_path).suffix == extension
    assert "2K" in Path(image_path).stem
    assert controller._download_hdri() == image_path
    assert len(calls) == 1

    monkeypatch.setattr(prefs.prefs, "resolution", "4096")
    assert _hdri_controller()._download_hdri() != image_path
    assert len(calls) == 2


@pytest.mark.parametrize(
    "result",
    [
        {"can_download": False, "download_url": "https://example.test/sky.exr"},
        {"can_download": True, "download_url": ""},
        {"can_download": True, "download_url": "https://example.test/sky.blend"},
    ],
)
def test_hdri_rejects_denied_missing_or_non_image_downloads(tmp_path, monkeypatch, result) -> None:
    monkeypatch.setattr(prefs.prefs, "global_dir_resolved", lambda: str(tmp_path))
    monkeypatch.setattr(client_lib, "get_download_url", lambda *args: result)
    monkeypatch.setattr(client_lib, "blocking_file_download", lambda *args: pytest.fail("Must not download"))
    with pytest.raises(RuntimeError):
        _hdri_controller()._download_hdri()


def test_hdri_pipeline_skips_model_export_and_dispatches_import(tmp_path, monkeypatch) -> None:
    from bk_unreal.core import hdri_import

    monkeypatch.setattr(prefs.prefs, "global_dir_resolved", lambda: str(tmp_path))
    controller = _hdri_controller()
    image_path = str(tmp_path / "sky.exr")
    monkeypatch.setattr(controller, "_download_hdri", lambda: image_path)
    monkeypatch.setattr(controller, "_download_model", lambda: pytest.fail("HDRIs are not models"))
    monkeypatch.setattr(controller, "_export_model", lambda: pytest.fail("HDRIs must not launch Blender"))
    calls = []

    def dispatch(callback):
        calls.append("editor-thread")
        callback()

    monkeypatch.setattr(editor_thread, "run_on_editor_thread", dispatch)
    monkeypatch.setattr(hdri_import, "import_hdri", lambda path, **kwargs: calls.append((path, kwargs)))
    controller._run()
    assert controller.finished
    assert controller.error is None
    assert calls == ["editor-thread", (image_path, {"rotation_z": 30.0})]


@pytest.mark.parametrize("failure", ["client", "empty", "interrupted"])
def test_failed_hdri_download_is_not_cached(tmp_path, monkeypatch, failure) -> None:
    monkeypatch.setattr(prefs.prefs, "global_dir_resolved", lambda: str(tmp_path))
    monkeypatch.setattr(
        client_lib,
        "get_download_url",
        lambda *args: {"can_download": True, "download_url": "https://example.test/sky.exr"},
    )

    def fake_download(url, path):
        if failure == "interrupted":
            Path(path).write_bytes(b"partial")
            raise RuntimeError("Download interrupted")
        if failure == "empty":
            Path(path).touch()
        return failure != "client"

    monkeypatch.setattr(client_lib, "blocking_file_download", fake_download)
    with pytest.raises(RuntimeError):
        _hdri_controller()._download_hdri()
    assert not list((tmp_path / "hdrs").rglob("*.exr"))
    assert not list((tmp_path / "hdrs").rglob("*.part"))


def test_hdri_import_failure_reaches_finished_callback(tmp_path, monkeypatch) -> None:
    from bk_unreal.core import hdri_import

    monkeypatch.setattr(prefs.prefs, "global_dir_resolved", lambda: str(tmp_path))
    controller = _hdri_controller()
    monkeypatch.setattr(controller, "_download_hdri", lambda: str(tmp_path / "sky.exr"))
    monkeypatch.setattr(editor_thread, "run_on_editor_thread", lambda callback: callback())

    def fail_import(*args, **kwargs):
        raise RuntimeError("HDRI decode failed")

    monkeypatch.setattr(hdri_import, "import_hdri", fail_import)
    finished = []
    controller._finished_callback = lambda success, status: finished.append((success, status))
    controller._run()
    assert controller.finished
    assert controller.error == "HDRI decode failed"
    assert finished == [(False, "Failed")]


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
