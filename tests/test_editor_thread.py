"""Tests for Qt-free editor-thread callback dispatch."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest
from bk_unreal.core import editor_thread


def test_dispatch_from_worker(monkeypatch):
    """Deliver worker callbacks only when the editor tick runs."""
    ticks = []
    removed = []
    monkeypatch.setitem(
        sys.modules,
        "unreal",
        SimpleNamespace(
            register_slate_post_tick_callback=lambda callback: ticks.append(callback) or 1,
            unregister_slate_post_tick_callback=removed.append,
        ),
    )
    editor_thread.install()
    editor_thread.install()
    calls = []
    worker = threading.Thread(target=lambda: editor_thread.run_on_editor_thread(lambda: calls.append(1)))
    worker.start()
    worker.join()
    try:
        assert len(ticks) == 1
        assert calls == []
        ticks[0](0.0)
        assert calls == [1]
        editor_thread.run_on_editor_thread(lambda: calls.append(2))
    finally:
        editor_thread.shutdown()
    assert removed == [1]
    ticks[0](0.0)
    assert calls == [1]


def test_dispatch_requires_install():
    """Reject scheduling before registration rather than touching Unreal on a worker."""
    with pytest.raises(RuntimeError, match="not installed"):
        editor_thread.run_on_editor_thread(lambda: None)


def test_failed_callback_does_not_drop_following_callbacks(monkeypatch):
    """Continue draining after one callback raises."""
    monkeypatch.setattr(editor_thread, "_tick_handle", 1)
    calls = []

    def fail():
        raise RuntimeError("failed callback")

    editor_thread.run_on_editor_thread(fail)
    editor_thread.run_on_editor_thread(lambda: calls.append(1))
    editor_thread._drain(0.0)
    assert calls == [1]
