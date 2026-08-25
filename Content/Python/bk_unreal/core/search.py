"""Search query building for Blendkit.

Turns UI state (asset type, search text, page) into the ``urlquery`` dict the Go
client expects, mirroring the Blender add-on's query construction.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PAGE_SIZE = 24

ASSET_TYPES: tuple[tuple[str, str], ...] = (
    ("model", "Models"),
    ("material", "Materials"),
    ("scene", "Scenes"),
    ("hdr", "HDRIs"),
    ("printable", "Printables"),
)


def build_query(
    asset_type: str = "model",
    search_text: str = "",
    page: int = 1,
    page_size: int = PAGE_SIZE,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the search ``urlquery`` dict for the client's ``asset_search``."""
    query: dict[str, Any] = {
        "asset_type": asset_type,
        "page_size": page_size,
        "page": page,
    }
    if search_text.strip():
        query["query"] = search_text.strip()
    if extra:
        query.update(extra)
    return query
