"""Version + release-channel sanity checks."""

from __future__ import annotations

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
