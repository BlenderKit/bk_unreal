"""Client integration path/version helpers (no client process required)."""

from __future__ import annotations

import os

from bk_unreal.core import client_lib, global_vars


def test_api_version_is_minor() -> None:
    # "/vX.Y" prefix is derived from global_vars.CLIENT_VERSION, whatever it
    # currently is - asserting against a literal would break on every bump.
    assert client_lib._api_version() == global_vars.CLIENT_VERSION


def test_binary_name_platform_suffix() -> None:
    name = client_lib._binary_name()
    assert name.startswith("bk_client-")
    if os.name == "nt":
        assert name.endswith(".exe")


def test_addon_root_points_at_plugin() -> None:
    root = client_lib._addon_root()
    # The plugin root holds the .uplugin descriptor.
    assert os.path.isfile(os.path.join(root, "Blendkit.uplugin"))


def test_client_ports_are_strings() -> None:
    assert client_lib.CLIENT_PORTS
    assert all(isinstance(p, str) and p.isdigit() for p in client_lib.CLIENT_PORTS)


def test_run_blender_script_nests_recipe_params(monkeypatch) -> None:
    captured = {}

    def fake_request(method, url, body=None, **kwargs):
        captured.update(body or {})
        return {"task_id": "task-1"}

    monkeypatch.setattr(client_lib, "ensure_running", lambda: "62485")
    monkeypatch.setattr(client_lib, "_http_request", fake_request)

    result = client_lib.run_blender_script(
        script_id="export_usd",
        blender_exe_path="C:/Blender/blender.exe",
        blend_path="C:/cache/model.blend",
        output_path="C:/cache/model.usd",
        out_usd="C:/cache/model.usd",
        max_resolution="ORIGINAL",
    )

    assert result["task_id"] == "task-1"
    assert captured["blend_path"] == "C:/cache/model.blend"
    assert captured["output_path"] == "C:/cache/model.usd"
    assert captured["params"] == {
        "blend_path": "C:/cache/model.blend",
        "out_usd": "C:/cache/model.usd",
        "max_resolution": "ORIGINAL",
    }
