"""Headless tests for native Slate settings actions."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from bk_unreal.core.prefs import Prefs
from bk_unreal.ui_native import settings


class Delegate:
    """Minimal reflected delegate stand-in."""

    def __init__(self):
        self.callbacks = []

    def add_callable(self, callback):
        self.callbacks.append(callback)

    def remove_callable(self, callback):
        self.callbacks.remove(callback)


@pytest.fixture
def native_settings(monkeypatch, tmp_path):
    """Provide an isolated bridge, preferences and editor callback queue."""
    snapshots = []
    queue = []
    bridge = SimpleNamespace(
        set_settings=snapshots.append,
        open_settings_tab=lambda: None,
        **{
            name: Delegate()
            for name in (
                "on_settings_requested",
                "on_settings_saved",
                "on_login_requested",
                "on_logout_requested",
                "on_manual_key_requested",
                "on_blender_detect_requested",
            )
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "unreal",
        SimpleNamespace(BlendkitBridge=SimpleNamespace(get=lambda: bridge), BlendkitSettings=SimpleNamespace),
    )
    preferences = Prefs()
    preferences._loaded = True
    preferences.global_dir = str(tmp_path)
    monkeypatch.setattr(settings, "prefs", preferences)
    monkeypatch.setattr(settings, "_installed", False)
    monkeypatch.setattr(settings, "_state", {})
    monkeypatch.setattr(settings.editor_thread, "run_on_editor_thread", queue.append)
    monkeypatch.setattr(settings.auth, "is_logged_in", lambda: False)
    monkeypatch.setattr(settings.auth, "add_login_listener", lambda callback: None)
    monkeypatch.setattr(settings.auth, "remove_login_listener", lambda callback: None)
    detect_blender = settings._on_detect_blender
    monkeypatch.setattr(settings, "_on_detect_blender", lambda path: None)
    settings.install()
    yield SimpleNamespace(
        bridge=bridge, snapshots=snapshots, queue=queue, prefs=preferences, detect_blender=detect_blender
    )
    settings.shutdown()


def test_native_settings_lifecycle(native_settings):
    """Bind each native event once and remove bindings during shutdown."""
    settings.install()
    assert len(native_settings.bridge.on_login_requested.callbacks) == 1
    assert native_settings.snapshots[-1].account_status == "Not logged in"
    settings.shutdown()
    assert native_settings.bridge.on_login_requested.callbacks == []


def test_native_settings_save(native_settings):
    """Persist the same preferences without a Qt dialog."""
    settings._on_settings_saved(
        SimpleNamespace(
            global_dir=native_settings.prefs.global_dir,
            blender_executable="",
            resolution="2048",
            verify_ssl=False,
        )
    )
    assert native_settings.prefs.resolution == "2048"
    assert native_settings.prefs.ssl_verification is False
    assert native_settings.snapshots[-1].message == "Settings saved."


def test_native_settings_rejects_invalid_resolution(native_settings):
    """Reject malformed bridge input without changing preferences."""
    settings._on_settings_saved(SimpleNamespace(resolution="bad"))
    assert native_settings.prefs.resolution == "ORIGINAL"
    assert native_settings.snapshots[-1].message == "Invalid import resolution."


def test_login_result_is_marshaled(native_settings, monkeypatch):
    """Update account state on the editor tick, not the OAuth worker."""
    pending = []
    monkeypatch.setattr(settings.auth, "login_async", pending.append)
    settings._on_login()
    settings._on_login()
    assert len(pending) == 1
    assert native_settings.snapshots[-1].login_pending
    pending[0](False)
    assert native_settings.snapshots[-1].login_pending
    native_settings.queue.pop()()
    assert not native_settings.snapshots[-1].login_pending
    assert "failed" in native_settings.snapshots[-1].message


def test_late_login_result_is_ignored(native_settings, monkeypatch):
    """Discard queued account updates when the plugin is unregistered."""
    pending = []
    monkeypatch.setattr(settings.auth, "login_async", pending.append)
    settings._on_login()
    pending[0](True)
    settings.shutdown()
    snapshots = len(native_settings.snapshots)
    native_settings.queue.pop()()
    assert len(native_settings.snapshots) == snapshots


def test_manual_key_and_logout(native_settings, monkeypatch):
    """Forward account commands without exposing credentials in bridge state."""
    applied = []
    logged_out = []
    monkeypatch.setattr(settings.auth, "set_manual_api_key", applied.append)
    monkeypatch.setattr(settings.auth, "logout", lambda: logged_out.append(True))
    settings._on_manual_key("  test-key  ")
    assert applied == ["test-key"]
    assert "test-key" not in repr(native_settings.snapshots[-1])
    settings._on_logout()
    assert logged_out == [True]


@pytest.mark.parametrize(
    ("executable", "version", "expected"),
    [
        ("", None, "No Blender found"),
        ("blender", None, "Could not read Blender version"),
        ("blender", (4, 5, 0), "too old"),
        ("blender", (5, 0, 0), "detected"),
    ],
)
def test_blender_detection_runs_off_editor_thread(native_settings, monkeypatch, executable, version, expected):
    """Defer Blender probing and deliver only its result on the editor tick."""
    workers = []
    monkeypatch.setattr(
        settings,
        "threading",
        SimpleNamespace(Thread=lambda target, **kwargs: SimpleNamespace(start=lambda: workers.append(target))),
    )
    monkeypatch.setattr(settings.blender_runner, "find_blender_executable", lambda: executable)
    monkeypatch.setattr(settings.blender_runner, "query_blender_version", lambda path: version)
    native_settings.detect_blender("")
    assert native_settings.snapshots[-1].blender_pending
    workers.pop()()
    assert native_settings.snapshots[-1].blender_pending
    native_settings.queue.pop()()
    assert not native_settings.snapshots[-1].blender_pending
    assert expected in native_settings.snapshots[-1].blender_status


def test_login_result_from_previous_registration_is_ignored(native_settings, monkeypatch):
    """Do not let an old OAuth worker change a newly registered settings tab."""
    pending = []
    monkeypatch.setattr(settings.auth, "login_async", pending.append)
    settings._on_login()
    settings.shutdown()
    settings.install()
    pending[0](True)
    assert native_settings.queue == []


def test_login_worker_reports_exceptions(monkeypatch):
    """Restore login controls when client startup fails in the OAuth worker."""

    def fail(timeout):
        raise RuntimeError("Client unavailable")

    monkeypatch.setattr(settings.auth, "login", fail)
    monkeypatch.setattr(
        settings.auth,
        "threading",
        SimpleNamespace(Thread=lambda target, **kwargs: SimpleNamespace(start=target)),
    )
    results = []
    settings.auth.login_async(results.append)
    assert results == [False]


def test_make_item_extracts_metadata(monkeypatch):
    """Ensure _make_item populates author, license, price, rating, and description."""
    from bk_unreal.ui_native import asset_bar_native

    created_items = []

    class MockItem:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            created_items.append(self)

    monkeypatch.setitem(sys.modules, "unreal", SimpleNamespace(BlendkitAssetItem=MockItem))

    asset = {
        "assetBaseId": "asset-123",
        "name": "Cozy Armchair",
        "assetType": "model",
        "description": "A very comfortable armchair.",
        "author": {"fullName": "Jane Doe", "id": 42},
        "isFree": True,
        "license": "CC-BY",
        "ratingsAverage": {"quality": 4.8},
        "ratingsCount": {"quality": 10},
        "downloadCount": 1500,
        "tags": ["chair", "furniture", "living room"],
    }

    item = asset_bar_native._make_item(asset)
    assert item.asset_id == "asset-123"
    assert item.name == "Cozy Armchair"
    assert item.author_name == "Jane Doe"
    assert item.author_id == "42"
    assert item.price == "FREE"
    assert item.license == "CC-BY"
    assert "4.8" in item.rating
    assert item.download_count == "1500"
    assert "furniture" in item.tags
    assert item.description == "A very comfortable armchair."
