"""Headless tests for bug report environment info and prefilled issue URLs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from bk_unreal.core import report
from bk_unreal.core.prefs import Prefs
from bk_unreal.ui_native import report as report_ui

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"


@pytest.fixture(autouse=True)
def isolated_prefs(monkeypatch, tmp_path):
    """Use throw-away preferences so tests never touch the user's data dir."""
    preferences = Prefs()
    preferences._loaded = True
    preferences.global_dir = str(tmp_path)
    monkeypatch.setattr(report, "prefs", preferences)
    return preferences


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_environment_info_headless_has_all_fields():
    info = report.get_environment_info()
    assert info["unreal_version"] == "unknown"
    assert info["blender_version"] == "not configured"
    assert info["ssl_verification"] == "enabled"
    assert all(info.values())


def test_environment_string_lists_versions():
    text = report.get_environment_info_string()
    assert "Blendkit for Unreal version:" in text
    assert "Unreal Engine version: unknown" in text
    assert text.rstrip().endswith("firewall?")


def test_unreal_version_read_lazily(monkeypatch):
    fake = SimpleNamespace(SystemLibrary=SimpleNamespace(get_engine_version=lambda: "5.6.1-123+++UE5"))
    monkeypatch.setitem(sys.modules, "unreal", fake)
    assert report.get_environment_info()["unreal_version"] == "5.6.1-123+++UE5"


def test_bug_url_targets_prefilled_template_and_encodes_values():
    info = dict(report.get_environment_info(), operating_system="Windows 11 (10.0.22631) AMD64 & more")
    url = report.get_report_bug_url(info)
    assert url.startswith("https://github.com/BlenderKit/bk_unreal/issues/new?")
    query = _query(url)
    assert query["template"] == "report-prefilled.yaml"
    assert query["operating_system"] == info["operating_system"]
    assert "title" not in query and "description" not in query


def test_bug_url_keys_match_template_field_ids():
    template = (TEMPLATE_DIR / "report-prefilled.yaml").read_text(encoding="utf-8")
    ids = set(re.findall(r"^\s+id:\s*(\S+)", template, re.MULTILINE))
    keys = set(_query(report.get_report_bug_url())) - {"template"}
    assert keys <= ids


def test_feature_url_uses_feature_template():
    query = _query(report.get_feature_request_url())
    assert query["template"] == "feature-request.yaml"
    assert (TEMPLATE_DIR / query["template"]).is_file()
    template = (TEMPLATE_DIR / "feature-request.yaml").read_text(encoding="utf-8")
    assert "id: additional-info" in template
    assert "Blendkit for Unreal" in query["additional-info"]


def test_blender_version_from_cache(isolated_prefs, tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"x")
    from bk_unreal.core import blender_runner

    isolated_prefs.blender_exe = str(exe)
    isolated_prefs.blender_version_cache[blender_runner._version_cache_sig(str(exe))] = "5.0.1"
    assert report.get_blender_version() == "5.0.1"
    isolated_prefs.blender_exe = str(tmp_path / "missing.exe")
    assert report.get_blender_version() == "not found"


def test_open_report_window_uses_bridge(monkeypatch):
    opened = []
    fake = SimpleNamespace(
        BlendkitBridge=SimpleNamespace(get=lambda: SimpleNamespace(open_report_window=opened.append)),
        BlendkitReportInfo=lambda **kw: kw,
        SystemLibrary=SimpleNamespace(get_engine_version=lambda: "5.6.0"),
        Paths=SimpleNamespace(project_log_dir=lambda: "Saved/Logs"),
    )
    monkeypatch.setitem(sys.modules, "unreal", fake)
    report_ui.open_report_window()
    assert len(opened) == 1
    info = opened[0]
    assert "Unreal Engine version: 5.6.0" in info["environment_text"]
    assert info["bug_report_url"].startswith(report.NEW_ISSUE_URL)
    assert info["unreal_log_dir"]
    assert info["client_log_dir"]


def test_open_report_window_falls_back_to_browser(monkeypatch):
    launched = []
    fake = SimpleNamespace(SystemLibrary=SimpleNamespace(launch_url=launched.append))
    monkeypatch.setitem(sys.modules, "unreal", fake)
    report_ui.open_report_window()
    assert len(launched) == 1
    assert _query(launched[0])["template"] == "report-prefilled.yaml"
