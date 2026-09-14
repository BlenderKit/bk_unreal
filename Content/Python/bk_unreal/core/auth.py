"""OAuth2 PKCE authentication for the Blendkit Unreal plugin.

Port of ``bk_maya/core/auth.py``. Mirrors the Blendkit add-on flow: the local
``blendkit-client`` Go process owns the OAuth callback on
``http://localhost:{port}/consumer/exchange/`` and delivers tokens back
through ``/report`` as a ``login`` task. This module generates the PKCE pair,
hands the verifier to the client, opens the browser, and waits for the login
task on a callback.

Tokens are persisted in the OS credential vault via
:mod:`bk_unreal.core.secret_store`, so they survive editor restarts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import secrets
import string
import threading
import time
import webbrowser
from collections.abc import Callable
from typing import Any

from ..api import client as api
from . import client_lib, secret_store

log = logging.getLogger(__name__)

# How early before expiry to start considering a token stale (seconds).
_REFRESH_RESERVE = 3 * 24 * 3600  # 3 days, mirrors the Blender add-on.

# Name under which the token bundle is stored in the OS credential vault.
_SECRET_NAME = "tokens"  # noqa: S105  # nosec B105 - a vault key name, not a credential

# -------------------------------------------------------------------------
# Token storage
# -------------------------------------------------------------------------

_tokens: dict[str, Any] = {}
_tokens_lock = threading.Lock()


def _load_tokens() -> dict[str, Any]:
    global _tokens
    with _tokens_lock:
        if _tokens:
            return dict(_tokens)
        raw = secret_store.get_secret(_SECRET_NAME)
        if raw:
            try:
                loaded = json.loads(raw)
                _tokens = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                _tokens = {}
        return dict(_tokens)


def _save_tokens(tokens: dict[str, Any]) -> None:
    global _tokens
    with _tokens_lock:
        _tokens = dict(tokens)
        secret_store.set_secret(_SECRET_NAME, json.dumps(_tokens))


def _clear_tokens() -> None:
    global _tokens
    with _tokens_lock:
        _tokens = {}
        secret_store.delete_secret(_SECRET_NAME)


# -------------------------------------------------------------------------
# PKCE
# -------------------------------------------------------------------------


def _generate_pkce_pair() -> tuple[str, str]:
    rand = random.SystemRandom()
    verifier = "".join(rand.choices(string.ascii_letters + string.digits, k=128))
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# -------------------------------------------------------------------------
# Login task plumbing
# -------------------------------------------------------------------------

_login_event = threading.Event()
_login_error: str = ""

# Refresh suppression so concurrent get_api_key() calls only fire one /refresh.
_refresh_inflight = False
_refresh_lock = threading.Lock()

# Cached numeric id of the logged-in user (for a future "My assets only" filter).
_user_id: int | None = None
_user_id_lock = threading.Lock()

# Listeners notified (on the poller thread) once the profile id is cached.
_profile_listeners: list[Callable[[], None]] = []

# Listeners notified (on the poller thread) after a *fresh* login completes
# (a transition from logged-out to logged-in, not a token refresh).
_login_listeners: list[Callable[[], None]] = []


def _invalidate_user_id() -> None:
    global _user_id
    with _user_id_lock:
        _user_id = None


def _on_login_task(result: dict[str, Any], status: str, message: str) -> None:
    """Callback registered with the client for every ``login`` task."""
    global _login_error, _refresh_inflight
    if status == "finished" and result.get("access_token"):
        was_logged_in = is_logged_in()
        tokens = {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", ""),
            "expires_in": result.get("expires_in", 3600),
            "expires_at": time.time() + int(result.get("expires_in", 3600)),
        }
        _save_tokens(tokens)
        _invalidate_user_id()
        fetch_profile()
        if not was_logged_in:
            _notify_login_listeners()
        log.info("Blendkit tokens received from client.")
    else:
        _login_error = message or "Login failed"
        log.error("Login task failed: %s", _login_error)
        # A rejected refresh token can never recover; drop it so search and the
        # rest of the plugin fall back to anonymous instead of sending a dead key.
        if "refresh token" in _login_error.lower() and ("400" in _login_error or "401" in _login_error):
            log.warning("Refresh token rejected by the server; clearing stored credentials.")
            _clear_tokens()
            _invalidate_user_id()

    with _refresh_lock:
        _refresh_inflight = False
    _login_event.set()


# Register the login callback once on import so refreshes happening before
# any explicit login() call are still persisted.
client_lib.set_login_callback(_on_login_task)


def _on_profile_task(result: dict[str, Any], status: str, message: str) -> None:
    """Callback for the client's ``profiles/get_user_profile`` task."""
    global _user_id
    if status != "finished":
        log.warning("Could not load user profile: %s", message or "unknown error")
        return

    user = result.get("user") if isinstance(result, dict) else None
    uid = user.get("id") if isinstance(user, dict) else None
    if uid is None:
        log.warning("User profile response had no user id.")
        return

    with _user_id_lock:
        _user_id = int(uid)
    log.debug("Cached user id %s.", _user_id)

    for cb in _profile_listeners:
        try:
            cb()
        except Exception:
            log.exception("Profile listener raised")


client_lib.set_profile_callback(_on_profile_task)


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------


def is_logged_in() -> bool:
    return bool(_load_tokens().get("access_token"))


def add_login_listener(cb: Callable[[], None]) -> None:
    """Register *cb* to run (on the poller thread) after a fresh login
    completes, so open UI (e.g. the settings dialog) can refresh itself.
    """
    if cb not in _login_listeners:
        _login_listeners.append(cb)


def remove_login_listener(cb: Callable[[], None]) -> None:
    """Unregister a callback previously added with :func:`add_login_listener`."""
    try:
        _login_listeners.remove(cb)
    except ValueError:
        pass


def _notify_login_listeners() -> None:
    for cb in tuple(_login_listeners):
        try:
            cb()
        except Exception:
            log.exception("Login listener raised")


def fetch_profile() -> None:
    """Trigger an async profile fetch via the client (no-op if not logged in)."""
    api_key = get_api_key()
    if not api_key:
        return

    def _worker() -> None:
        try:
            client_lib.ensure_running()
            client_lib.get_user_profile(api_key)
        except Exception as exc:
            log.warning("Could not request user profile: %s", exc)

    threading.Thread(target=_worker, name="bk_unreal-profile-fetch", daemon=True).start()


def get_user_id() -> int | None:
    """Return the cached logged-in user's numeric id, or ``None``.

    Non-blocking: triggers an async fetch and returns ``None`` if not cached yet.
    """
    with _user_id_lock:
        if _user_id is not None:
            return _user_id
    fetch_profile()
    return None


def get_api_key() -> str:
    """Return the cached access token. Triggers a background refresh if the
    token is within ``_REFRESH_RESERVE`` seconds of expiry, but never blocks.
    """
    tokens = _load_tokens()
    access = tokens.get("access_token", "")
    refresh = tokens.get("refresh_token", "")
    if not access:
        return ""

    expires_at = float(tokens.get("expires_at", 0))
    if refresh and time.time() + _REFRESH_RESERVE >= expires_at:
        _request_refresh(refresh, access)

    return access


def _request_refresh(refresh: str, old_api_key: str) -> None:
    """Fire-and-forget refresh via the client (deduped)."""
    global _refresh_inflight
    with _refresh_lock:
        if _refresh_inflight:
            return
        _refresh_inflight = True

    def _worker() -> None:
        global _refresh_inflight
        try:
            client_lib.ensure_running()
            client_lib.refresh_token(refresh, old_api_key)
            log.info("Token refresh requested via client.")
        except Exception as exc:
            with _refresh_lock:
                _refresh_inflight = False
            log.warning("Token refresh request failed: %s", exc)

    # Never on the caller's thread: get_api_key() runs on the Qt/editor thread.
    threading.Thread(target=_worker, name="bk_unreal-token-refresh", daemon=True).start()


def login(timeout: float = 180.0) -> bool:
    """Open the browser OAuth flow. Blocks until the client reports a login
    task or *timeout* elapses. Returns True on success.
    """
    global _login_error

    port = client_lib.ensure_running()
    if not port:
        log.error("Client not running; cannot start login.")
        return False

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    try:
        client_lib.send_oauth_verification_data(verifier, state)
    except Exception as exc:
        log.error("Failed to send PKCE verifier to client: %s", exc)
        return False

    redirect_uri = f"http://localhost:{port}/consumer/exchange/"
    auth_url = (
        f"{api.base_url()}/o/authorize"
        f"?client_id={client_lib.OAUTH_CLIENT_ID}"
        f"&response_type=code"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )

    _login_event.clear()
    _login_error = ""
    log.info("Opening browser for Blendkit login (callback on port %s)...", port)
    webbrowser.open_new_tab(auth_url)

    if not _login_event.wait(timeout=timeout):
        log.error("Login timed out after %s seconds.", timeout)
        return False

    if _login_error:
        return False
    return is_logged_in()


def login_async(on_done: Callable[[bool], None], timeout: float = 180.0) -> None:
    """Non-blocking variant of :func:`login` for UI callers on the GUI thread.

    Runs :func:`login` on a daemon thread and invokes *on_done(success)*; the
    caller is responsible for marshalling *on_done* back to the GUI thread if
    it touches Qt widgets (e.g. via ``QTimer.singleShot(0, ...)``).
    """

    def _worker() -> None:
        ok = login(timeout=timeout)
        try:
            on_done(ok)
        except Exception:
            log.exception("login_async on_done callback raised")

    threading.Thread(target=_worker, name="bk_unreal-oauth-login", daemon=True).start()


def logout() -> None:
    """Clear local tokens and revoke them on the server in the background."""
    tokens = _load_tokens()
    refresh = tokens.get("refresh_token", "")
    access = tokens.get("access_token", "")
    # Clear locally first so the UI updates immediately and the revoke worker
    # can't be blocked by, or block, anything on the caller's thread.
    _clear_tokens()
    _invalidate_user_id()
    if refresh:

        def _worker() -> None:
            try:
                client_lib.ensure_running()
                client_lib.oauth2_logout(refresh, access)
            except Exception as exc:
                log.warning("Client-side logout failed: %s", exc)

        threading.Thread(target=_worker, name="bk_unreal-oauth-logout", daemon=True).start()
    try:
        from . import bookmarks

        bookmarks.clear()
    except Exception:
        log.debug("Could not clear bookmarks on logout", exc_info=True)
    log.info("Logged out.")


def set_manual_api_key(value: str) -> None:
    """Store a manually-pasted API key as a long-lived access token.

    Fallback path for the settings dialog's paste field when the OAuth browser
    flow isn't usable (mirrors bk_maya's manual-entry behaviour). No refresh
    token is available for a pasted key, so it's given a generous 30-day
    expiry and simply needs re-pasting after that.
    """
    value = value.strip()
    if not value:
        return
    _save_tokens(
        {"access_token": value, "refresh_token": "", "expires_at": time.time() + 30 * 24 * 3600}  # nosec B105
    )
    _invalidate_user_id()
    fetch_profile()
    _notify_login_listeners()
