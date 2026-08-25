"""Blendkit for Unreal Engine — Python package.

A port of the Blendkit Blender add-on / Maya plugin to Unreal Engine's editor
Python API. Talks to the shared Go ``blendkit-client`` for search, auth,
thumbnails and downloads, and renders a Qt asset bar hosted inside the Unreal
editor via a Slate post-tick pump.
"""

from __future__ import annotations

from ._version import __version__

__all__ = ["__version__"]
