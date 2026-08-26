"""Minimal user-preferences shim for the Blendkit Unreal plugin.

Mirrors the subset of the Blender add-on / Maya plugin ``prefs`` object that the
client integration relies on (global data dir, API key, SSL verification). Later
this can be backed by an Unreal ``USavedConfig`` / project settings surface; for
now it reads environment variables and a simple JSON file under the Blendkit
global directory so behaviour matches the other ports.
"""

from __future__ import annotations

import json
import os
from typing import Any


def _default_global_dir() -> str:
    """Return the default Blendkit global data directory (per-user)."""
    override = os.environ.get("BLENDKIT_GLOBAL_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), "blenderkit_data")


RESOLUTIONS: tuple[str, ...] = ("512", "1024", "2048", "4096", "8192", "ORIGINAL")


class Prefs:
    """Lazily-loaded preferences with the fields client_lib expects."""

    def __init__(self) -> None:
        self._global_dir = _default_global_dir()
        self.ssl_verification: bool = True
        self._api_key: str = os.environ.get("API_KEY", "")
        self.resolution: str = "ORIGINAL"
        self.blender_exe: str = ""
        self.blender_version_cache: dict[str, str] = {}
        self.bookmarks: set[str] = set()
        self._loaded = False

    # ── Global dir ───────────────────────────────────────────

    def global_dir_resolved(self) -> str:
        """Absolute, guaranteed-to-exist global data directory."""
        self._load()
        os.makedirs(self._global_dir, exist_ok=True)
        return self._global_dir

    @property
    def global_dir(self) -> str:
        self._load()
        return self._global_dir

    @global_dir.setter
    def global_dir(self, value: str) -> None:
        self._global_dir = os.path.abspath(value) if value else _default_global_dir()

    # ── API key ───────────────────────────────────────────────────────────────

    def _prefs_file(self) -> str:
        return os.path.join(self._global_dir, "bk_unreal_prefs.json")

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._prefs_file(), encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
        except (OSError, ValueError):
            return
        self._api_key = self._api_key or data.get("api_key", "")
        self.ssl_verification = bool(data.get("ssl_verification", self.ssl_verification))
        self.resolution = str(data.get("resolution", self.resolution))
        self.blender_exe = str(data.get("blender_exe", self.blender_exe))
        cache = data.get("blender_version_cache")
        if isinstance(cache, dict):
            self.blender_version_cache = {str(k): str(v) for k, v in cache.items()}
        marks = data.get("bookmarks")
        if isinstance(marks, list):
            self.bookmarks = {str(m) for m in marks}
        stored_dir = data.get("global_dir", "")
        if stored_dir:
            self._global_dir = os.path.abspath(stored_dir)

    @property
    def api_key(self) -> str:
        self._load()
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._api_key = value or ""

    def save(self) -> None:
        """Persist the mutable prefs to the global dir (best-effort)."""
        try:
            os.makedirs(self._global_dir, exist_ok=True)
            with open(self._prefs_file(), "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "api_key": self._api_key,
                        "ssl_verification": self.ssl_verification,
                        "resolution": self.resolution,
                        "blender_exe": self.blender_exe,
                        "blender_version_cache": self.blender_version_cache,
                        "bookmarks": sorted(self.bookmarks),
                        "global_dir": self._global_dir,
                    },
                    fh,
                )
        except OSError:
            pass


prefs = Prefs()
"""Process-wide singleton, imported as ``from ..core.prefs import prefs``."""
