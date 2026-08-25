"""Blendkit REST API client.

Talks directly to the Blendkit API (``$BLENDKIT_SERVER/api/v1/``). All
network calls are synchronous; callers are expected to run them on a worker
thread so the Unreal UI thread is never blocked.

Most heavy lifting (search, thumbnails, downloads) goes through the Go
``blendkit-client`` instead — see :mod:`bk_unreal.core.client_lib`. This
module covers the direct-to-server endpoints (OAuth token exchange, profile).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..core import global_vars

log = logging.getLogger(__name__)


def base_url() -> str:
    return global_vars.SERVER


def api_v1() -> str:
    return f"{base_url()}/api/v1"


_DEFAULT_HEADERS = {
    "User-Agent": "Blendkit-Unreal/0.1",
    "Accept": "application/json",
}


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    api_key: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute an HTTP request and return the parsed JSON body.

    Raises ``urllib.error.HTTPError`` on 4xx/5xx responses.
    """
    all_headers = dict(_DEFAULT_HEADERS)
    if api_key:
        all_headers["Authorization"] = f"Bearer {api_key}"
    if headers:
        all_headers.update(headers)

    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    body: bytes | None = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        all_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, data=body, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        log.error("HTTP %s %s → %d: %s", method, url, exc.code, body_text)
        raise


# ── Auth ──────────────────────────────────────────────────────────────────────

CLIENT_ID = "IdFRwa3SGA8eMpzhRVFMg5Ts8sPK93xBjif93x0F"
"""OAuth client id baked into the Go client; reused here for URL building."""


def token_url() -> str:
    return f"{base_url()}/o/token/"


def revoke_url() -> str:
    return f"{base_url()}/o/revoke-token/"


def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    """Use a refresh token to obtain a new access token."""
    return _request(
        "POST",
        token_url(),
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
    )


def get_profile(api_key: str) -> dict[str, Any]:
    """Fetch the logged-in user's profile."""
    return _request("GET", f"{api_v1()}/me/", api_key=api_key)
