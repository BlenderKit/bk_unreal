"""Locate and validate a Blender executable for background processing.

Unreal counterpart of ``bk_maya/core/blender_runner.py``, trimmed to the parts
needed to *configure* Blender: auto-detection, ``--version`` querying (cached in
prefs) and a minimum-version check. Blendkit requires **Blender 5.0 or newer**.

Kept engine-agnostic and Qt-free so the headless pytest suite can import it.
The QProcess-based background job runner will be added alongside the
download/import feature.

Auto-detection order (see :func:`find_blender_executable`):

1. ``prefs.blender_exe`` if set and existing.
2. ``BLENDER_PATH`` env var.
3. ``shutil.which("blender")``.
4. Common install dirs on Windows / macOS / Linux (latest version first).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .prefs import prefs

log = logging.getLogger(__name__)

MIN_BLENDER_MAJOR = 5
"""Minimum supported Blender major version (Blender 5.0+)."""

_VERSION_RE = re.compile(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)


def _candidate_paths() -> list[str]:
    """Return platform-specific candidate paths, newest-version first."""
    out: list[str] = []
    if sys.platform == "win32":
        for root in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ):
            base = Path(root) / "Blender Foundation"
            if base.is_dir():
                versions = sorted(
                    (p for p in base.iterdir() if p.is_dir() and p.name.lower().startswith("blender")),
                    key=lambda p: p.name,
                    reverse=True,
                )
                out.extend(str(v / "blender.exe") for v in versions)
    elif sys.platform == "darwin":
        out.extend(
            [
                "/Applications/Blender.app/Contents/MacOS/Blender",
                os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender"),
            ]
        )
    else:  # Linux / other
        out.extend(
            d if d.endswith("blender") else os.path.join(d, "blender")
            for d in (
                "/usr/bin",
                "/usr/local/bin",
                "/opt/blender/blender",
                os.path.expanduser("~/blender/blender"),
            )
        )
    return out


def resolve_macos_app(path: str) -> str:
    """Resolve a macOS ``Blender.app`` bundle to its inner executable.

    File pickers return the ``.app`` directory rather than the runnable binary
    inside it, so map any ``*.app`` path to ``<app>/Contents/MacOS/Blender``.
    Non-macOS paths and paths that already point at the inner binary are
    returned unchanged.
    """
    if sys.platform != "darwin" or not path:
        return path
    stripped = path.rstrip("/")
    if stripped.endswith(".app"):
        inner = os.path.join(stripped, "Contents", "MacOS", "Blender")
        if os.path.isfile(inner):
            return inner
    return path


def find_blender_executable() -> str:
    """Locate a usable Blender executable, or return ``""`` if none is found."""
    candidate = (prefs.blender_exe or "").strip()
    if candidate:
        candidate = resolve_macos_app(candidate)
        if os.path.isfile(candidate):
            return candidate

    env = os.environ.get("BLENDER_PATH", "").strip()
    if env and os.path.isfile(env):
        return env

    which = shutil.which("blender") or (shutil.which("blender.exe") if sys.platform == "win32" else None)
    if which:
        return which

    for p in _candidate_paths():
        if os.path.isfile(p):
            return p
    return ""


def _version_cache_sig(exe_path: str) -> str:
    """Return a cache key ``"<path>|<mtime>|<size>"``, or ``""`` if unavailable."""
    try:
        st = os.stat(exe_path)
    except OSError:
        return ""
    return f"{exe_path}|{int(st.st_mtime)}|{st.st_size}"


def _parse_version_str(value: str | None) -> tuple[int, int, int] | None:
    """Parse a cached ``"X.Y.Z"`` string back into a version triple."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _store_version_cache(sig: str, version: tuple[int, int, int]) -> None:
    """Persist a detected version, keeping only the most recent few entries."""
    value = ".".join(str(x) for x in version)
    if prefs.blender_version_cache.get(sig) == value:
        return
    prefs.blender_version_cache[sig] = value
    if len(prefs.blender_version_cache) > 8:
        for key in list(prefs.blender_version_cache)[:-8]:
            prefs.blender_version_cache.pop(key, None)
    try:
        prefs.save()
    except Exception:
        log.debug("Could not persist Blender version cache", exc_info=True)


def query_blender_version(exe_path: str, *, timeout: float = 5.0) -> tuple[int, int, int] | None:
    """Run ``<blender> --version`` and return the parsed ``(major, minor, patch)``.

    Results are cached in :data:`prefs.blender_version_cache`, keyed by the
    executable path plus its modification time and size, so a validated path is
    not re-spawned. Returns ``None`` on failure.
    """
    if not exe_path or not os.path.isfile(exe_path):
        return None

    sig = _version_cache_sig(exe_path)
    if sig:
        parsed = _parse_version_str(prefs.blender_version_cache.get(sig))
        if parsed is not None:
            return parsed

    try:
        proc = subprocess.run(
            [exe_path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        first_line = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        m = _VERSION_RE.search(first_line)
        if not m:
            return None
        version = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None

    if sig:
        _store_version_cache(sig, version)
    return version


def version_meets_min(version: tuple[int, int, int] | None) -> bool:
    """Return ``True`` when *version* is at least :data:`MIN_BLENDER_MAJOR`.0."""
    return bool(version) and version[0] >= MIN_BLENDER_MAJOR
