"""Search query construction."""

from __future__ import annotations

from bk_unreal.core import search


def test_build_query_defaults() -> None:
    query = search.build_query()
    assert query["asset_type"] == "model"
    assert query["page"] == 1
    assert query["page_size"] == search.PAGE_SIZE
    assert "query" not in query  # empty search text omitted


def test_build_query_with_text() -> None:
    query = search.build_query(asset_type="material", search_text="  wood  ", page=3)
    assert query["asset_type"] == "material"
    assert query["query"] == "wood"
    assert query["page"] == 3


def test_asset_types_nonempty() -> None:
    assert search.ASSET_TYPES
    assert all(len(pair) == 2 for pair in search.ASSET_TYPES)
