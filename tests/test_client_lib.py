"""Client integration path/version helpers (no client process required)."""

from __future__ import annotations

import os

from bk_unreal.core import client_lib


def test_api_version_is_minor() -> None:
    # From CLIENT_VERSION "v1.11" we expect the "/vX.Y" API prefix.
    assert client_lib._api_version() == "v1.11"


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
