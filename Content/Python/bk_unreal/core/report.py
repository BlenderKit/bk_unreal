"""Environment info and prefilled GitHub issue URLs for bug reports.

Port of the Blender add-on's ``get_environment_info`` / ``get_report_bug_url``
(``blenderkit_addon/ui_panels.py``). Engine-agnostic: the Unreal engine version
is read lazily and falls back to ``unknown`` outside the editor.
"""

from __future__ import annotations

import os
import platform
from urllib.parse import urlencode

from .._version import get_channel, get_version
from . import global_vars
from .prefs import prefs

ISSUES_URL = "https://github.com/BlenderKit/bk_unreal/issues"
NEW_ISSUE_URL = f"{ISSUES_URL}/new"
BUG_TEMPLATE = "report-prefilled.yaml"
FEATURE_TEMPLATE = "feature-request.yaml"
REPORT_HELP_URL = "https://www.blendkit.com/report-bug/"


def get_unreal_version() -> str:
    """Return the running Unreal Engine version, or ``unknown`` when headless."""
    try:
        import unreal

        return str(unreal.SystemLibrary.get_engine_version())
    except Exception:
        return "unknown"


def get_os_info() -> str:
    """Return a human-readable OS name, release and CPU architecture."""
    system = platform.system()
    machine = platform.machine() or "unknown arch"
    if system == "Windows":
        release, build = platform.release(), platform.version()
        # platform.release() still reports "10" on Windows 11; build 22000+ is 11.
        try:
            if release == "10" and int(build.split(".")[-1]) >= 22000:
                release = "11"
        except ValueError:
            pass
        return f"Windows {release} ({build}) {machine}"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()} {machine}"
    if system == "Linux":
        try:
            distro = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except (OSError, AttributeError):
            distro = "Linux"
        return f"{distro} (kernel {platform.release()}) {machine}"
    return f"{platform.platform()} {machine}"


def get_blender_version() -> str:
    """Return the cached version of the configured Blender, without spawning it."""
    exe = prefs.blender_exe
    if not exe:
        return "not configured"
    try:
        from .blender_runner import _version_cache_sig

        sig = _version_cache_sig(exe)
    except Exception:
        sig = ""
    return prefs.blender_version_cache.get(sig, "unknown") if sig else "not found"


def get_environment_info() -> dict[str, str]:
    """Collect the versions and settings that help diagnose a report."""
    return {
        "blendkit_version": f"{get_version()} ({get_channel()})",
        "unreal_version": get_unreal_version(),
        "operating_system": get_os_info(),
        "python_version": platform.python_version(),
        "client_version": global_vars.CLIENT_VERSION,
        "blender_version": get_blender_version(),
        "ssl_verification": "enabled" if prefs.ssl_verification else "disabled",
    }


def get_environment_info_string(info: dict[str, str] | None = None) -> str:
    """Format environment info as a Markdown list for clipboard and the report window."""
    info = info or get_environment_info()
    return (
        f"- Blendkit for Unreal version: {info['blendkit_version']}\n"
        f"- Unreal Engine version: {info['unreal_version']}\n"
        f"- Operating system & architecture: {info['operating_system']}\n"
        f"- Python version: {info['python_version']}\n"
        f"- Blendkit client version: {info['client_version']}\n"
        f"- Blender version: {info['blender_version']}\n"
        f"- SSL verification: {info['ssl_verification']}\n"
        "- Using VPN, proxy, or firewall? "
    )


def get_report_bug_url(info: dict[str, str] | None = None) -> str:
    """Build a GitHub issue URL prefilled with environment info.

    Title and description are intentionally left empty so the user writes them.
    Query keys must match field ids in ``.github/ISSUE_TEMPLATE/report-prefilled.yaml``.

    Args:
        info: Precomputed environment info; collected when omitted.

    Returns:
        URL that opens the prefilled bug report form.
    """
    info = info or get_environment_info()
    return f"{NEW_ISSUE_URL}?{urlencode({'template': BUG_TEMPLATE, **info})}"


def get_feature_request_url(info: dict[str, str] | None = None) -> str:
    """Build a GitHub feature request URL with the plugin and engine version noted.

    Args:
        info: Precomputed environment info; collected when omitted.

    Returns:
        URL that opens the feature request form.
    """
    info = info or get_environment_info()
    context = f"Blendkit for Unreal {info['blendkit_version']}, Unreal Engine {info['unreal_version']}"
    return f"{NEW_ISSUE_URL}?{urlencode({'template': FEATURE_TEMPLATE, 'additional-info': context})}"


def get_client_log_dir() -> str:
    """Return the folder holding the Go client's ``default.log``."""
    return os.path.join(prefs.global_dir_resolved(), "client")


def get_unreal_log_dir() -> str:
    """Return the project's Unreal log folder (Python output lands there too), or ``""`` headless."""
    try:
        import unreal

        return os.path.abspath(unreal.Paths.project_log_dir())
    except Exception:
        return ""


__all__ = [
    "ISSUES_URL",
    "get_client_log_dir",
    "get_environment_info",
    "get_environment_info_string",
    "get_feature_request_url",
    "get_report_bug_url",
    "get_unreal_log_dir",
]
