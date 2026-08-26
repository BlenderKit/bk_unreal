"""Search query building for Blendkit.

Turns UI state (asset type, search text, page) into the ``urlquery`` dict the Go
client expects, mirroring the Blender add-on's query construction.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from . import global_vars

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


def build_search_url(query: dict[str, Any], next_url: str = "") -> str:
    """Return the full ``$SERVER/api/v1/search/?...`` URL for the Go client.

    The client GETs this string verbatim as the ``urlquery`` field of an
    ``/blender/asset_search`` request, so it must be an absolute URL. When
    *next_url* is given (cursor pagination) it is returned unchanged.
    """
    if next_url:
        return next_url

    asset_type = str(query.get("asset_type", "model"))
    search_text = str(query.get("query", "") or "").strip()
    page_size = int(query.get("page_size", PAGE_SIZE))
    order = str(query.get("order", "") or "")

    if order:
        effective_order = order
    elif not search_text:
        effective_order = "-last_blend_upload,-last_zip_file_upload"
    else:
        effective_order = "_score"

    q_tokens: list[str] = []
    if search_text:
        q_tokens.append(urllib.parse.quote_plus(search_text))
    q_tokens.append(f"asset_type:{asset_type}")
    q_tokens.append("sexualizedContent:")
    q_tokens.append("verification_status:validated")
    q_tokens.append(f"order:{effective_order}")

    query_str = "+".join(q_tokens)
    if not search_text:  # server expects a leading separator when query is empty
        query_str = "+" + query_str

    other = {
        "dict_parameters": 1,
        "page_size": page_size,
        "addon_version": "3.20.0",
        "addon_type": "unreal",
    }
    return f"{global_vars.SERVER}/api/v1/search/?query={query_str}&{urllib.parse.urlencode(other)}"
