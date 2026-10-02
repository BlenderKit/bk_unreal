"""Native (Slate) asset-bar UI package.

An experiment running in parallel with :mod:`bk_unreal.ui` (the Qt asset bar).
The presentation lives in the C++ ``BlendkitViewport`` module (``SBlendkitAssetBar``
+ ``UBlendkitBridge``); this package is only the Python glue that wires the
native widgets to the same engine-agnostic ``core`` code the Qt UI uses.
"""

from __future__ import annotations
