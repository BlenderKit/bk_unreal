"""Version + release-channel sanity checks."""

from __future__ import annotations

import runpy
import zipfile
from pathlib import Path

from bk_unreal import _version


def test_base_version_shape() -> None:
    parts = _version.BASE_VERSION.split(".")
    assert len(parts) == 2
    assert all(p.isdigit() for p in parts)


def test_get_version_nonempty() -> None:
    assert _version.get_version()


def test_channel_is_known() -> None:
    assert _version.get_channel() in (
        _version.CHANNEL_STABLE,
        _version.CHANNEL_ALPHA,
        _version.CHANNEL_DEV,
    )


def test_version_info_keys() -> None:
    info = _version.get_version_info()
    for key in ("version", "channel", "base_version"):
        assert key in info


def test_zip_excludes_retired_qt_packages(monkeypatch, tmp_path) -> None:
    """Keep stale vendored Qt binaries out of native-only release packages."""
    tasks = runpy.run_path(str(Path(__file__).resolve().parents[1] / "dev.py"))
    build_zip = tasks["build_zip"]
    root = tmp_path / "plugin"
    lib = root / "Content" / "Python" / "bk_unreal" / "lib"
    for package in ("PySide6", "shiboken6", "qtpy", "requests"):
        directory = lib / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setitem(build_zip.__globals__, "REPO_ROOT", str(root))
    monkeypatch.setitem(build_zip.__globals__, "OUT_DIR", str(tmp_path / "out"))
    with zipfile.ZipFile(build_zip("test")) as archive:
        names = archive.namelist()
    assert names == ["Blendkit/Content/Python/bk_unreal/lib/requests/__init__.py"]
    assert tasks["VENDOR_PACKAGES"] == ["packaging", "requests"]
