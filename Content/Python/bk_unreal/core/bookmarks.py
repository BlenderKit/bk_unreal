"""Local bookmark store for the Blendkit Unreal plugin.

Persists a set of bookmarked asset base-ids to the prefs JSON. This is a local
stand-in until account-synced bookmarks arrive with the OAuth login flow (the
Maya plugin syncs bookmarks through the API once authenticated).
"""

from __future__ import annotations

from .prefs import prefs


def is_bookmarked(asset_id: str) -> bool:
    """Return ``True`` when *asset_id* is currently bookmarked."""
    return bool(asset_id) and asset_id in prefs.bookmarks


def toggle(asset_id: str) -> bool:
    """Add or remove *asset_id* from the bookmarks; return the new state."""
    if not asset_id:
        return False
    if asset_id in prefs.bookmarks:
        prefs.bookmarks.discard(asset_id)
        marked = False
    else:
        prefs.bookmarks.add(asset_id)
        marked = True
    prefs.save()
    return marked
