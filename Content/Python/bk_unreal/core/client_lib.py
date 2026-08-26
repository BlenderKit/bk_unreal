"""Blendkit-Client process integration for Unreal.

Mirrors the architecture used by the Blender add-on / Maya plugin: the plugin
never talks to blendkit.com directly for search / thumbnail / download work.
Instead it spawns a local Go process (``blendkit-client``) and talks to it over
loopback HTTP.

The client:
  * fetches search results from the Blendkit API,
  * downloads all thumbnails (rate-limited),
  * writes them into a caller-supplied ``tempdir``,
  * reports task progress through a polling ``/report`` endpoint.

This module owns:
  * launching / probing the client process,
  * port discovery,
  * the ``/blender/asset_search`` POST,
  * the ``/report`` GET poll,
  * a task-id → callback registry used by ``ui.asset_bar`` to deliver search
    results and thumbnail paths back to the GUI thread.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from . import global_vars, search
from . import prefs as _prefs_mod

log = logging.getLogger(__name__)

# ── Versions / constants ─────────────────────────────────────────────────────

DEFAULT_CLIENT_VERSION = global_vars.CLIENT_VERSION

_client_version_cache: str | None = None

# Same ordering as the Blender add-on; these are also the redirect_uri ports
# whitelisted by the OAuth app, so we cannot pick arbitrary ones.
CLIENT_PORTS: tuple[str, ...] = (
    "62485",
    "65425",
    "55428",
    "49452",
    "35452",
    "25152",
    "5152",
    "1234",
)

ADDON_VERSION = "3.20.0"
"""Plugin version (X.Y.Z) reported to the Go client, kept in step with the
Blender add-on / Maya plugin so the client logs a consistent version string."""

ADDON_BUILD = "260517"
"""Date stamp (YYMMDD) used as the 4th version segment passed to the client."""

SOFTWARE_NAME = "Unreal"

OAUTH_CLIENT_ID = "IdFRwa3SGA8eMpzhRVFMg5Ts8sPK93xBjif93x0F"

POLL_CONNECT_TIMEOUT = 0.20
POLL_READ_TIMEOUT = 0.50
REQUEST_TIMEOUT = 5.0

# ── Module state ─────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_process: subprocess.Popen | None = None
_active_port: str = CLIENT_PORTS[0]
_app_id: int = os.getpid()
_port_index: int = 0
_use_inplace_client: bool = False

# task-id → callback registry, drained by the report poller.
_task_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
_callbacks_lock = threading.Lock()

# Global thumbnail sink: (asset_base_id, image_path). Thumbnails arrive as their
# own tasks, so they are not keyed by the requesting search's task id.
_thumbnail_callback: Callable[[str, str], None] | None = None

# asset_base_id → best downloaded thumbnail path seen so far. Lets a tile that
# was created after the client already reported (and dropped) its
# thumbnail_download task still pick the image up. "full" thumbs win over "small".
_thumbnail_paths: dict[str, str] = {}
_thumbnail_paths_lock = threading.Lock()

_poller_thread: threading.Thread | None = None
_poller_stop = threading.Event()


# ── Path helpers ─────────────────────────────────────────────────────────────


def _addon_root() -> str:
    """Return the plugin root (the folder that holds ``Blendkit.uplugin``).

    This file lives at ``<root>/Content/Python/bk_unreal/core/client_lib.py``,
    so the root is four directories up.
    """
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here)))))


def _binary_name() -> str:
    """Match ``decide_client_binary_name`` from the Blender add-on."""
    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "macos"

    arch = platform.machine().lower()
    if arch == "amd64":
        arch = "x86_64"
    elif arch == "aarch64":
        arch = "arm64"

    name = f"bk_client-{os_name}-{arch}"
    if os_name == "windows":
        name += ".exe"
    return name


def _client_binaries_root() -> str:
    """Directory that holds the ``vX.Y.Z/`` client-binary folders.

    Packaged plugin: ``<root>/client``. Source checkout: the ``bk_client``
    submodule at ``<root>/bk_client/client``.
    """
    root = _addon_root()
    packaged = os.path.join(root, "client")
    if os.path.isdir(packaged):
        return packaged
    return os.path.join(root, "bk_client", "client")


def _parse_version(name: str) -> tuple[int, ...] | None:
    if not name.startswith("v"):
        return None
    try:
        return tuple(int(part) for part in name[1:].split("."))
    except ValueError:
        return None


def _detect_client_version() -> str:
    """Return the exact bundled client version, e.g. ``v1.11.3``."""
    global _client_version_cache
    if _client_version_cache is not None:
        return _client_version_cache

    binaries_root = _client_binaries_root()

    try:
        with open(os.path.join(binaries_root, "RESOLVED_VERSION"), encoding="utf-8") as fh:
            resolved = fh.read().strip()
        if resolved:
            _client_version_cache = resolved if resolved.startswith("v") else f"v{resolved}"
            return _client_version_cache
    except OSError:
        pass

    binary = _binary_name()
    best: tuple[tuple[int, ...], str] | None = None
    try:
        for entry in os.listdir(binaries_root):
            parsed = _parse_version(entry)
            if parsed is None:
                continue
            if not os.path.isfile(os.path.join(binaries_root, entry, binary)):
                continue
            if best is None or parsed > best[0]:
                best = (parsed, entry)
    except OSError:
        best = None

    if best is not None:
        _client_version_cache = best[1]
    else:
        try:
            with open(os.path.join(binaries_root, "VERSION"), encoding="utf-8") as fh:
                _client_version_cache = f"v{fh.read().strip()}"
        except OSError:
            _client_version_cache = DEFAULT_CLIENT_VERSION
    return _client_version_cache


def _api_version() -> str:
    """Client HTTP API version prefix, e.g. ``v1.11`` (major.minor)."""
    parts = global_vars.CLIENT_VERSION.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:2])
    return global_vars.CLIENT_VERSION


def _inplace_binary_path() -> str:
    return os.path.join(_client_binaries_root(), _detect_client_version(), _binary_name())


def _installed_binary_dir() -> str:
    return os.path.join(_prefs_mod.prefs.global_dir_resolved(), "client", "bin", _detect_client_version())


def _installed_binary_path() -> str:
    return os.path.join(_installed_binary_dir(), _binary_name())


def _ensure_client_binary_installed() -> str:
    """Resolve the binary to launch, copying to the user's global dir if needed."""
    global _use_inplace_client

    src = _inplace_binary_path()
    dst = _installed_binary_path()

    if not _use_inplace_client and os.path.isfile(dst):
        return dst

    if not os.path.isfile(src):
        raise FileNotFoundError(f"Blendkit client binary not found at {src}. Run `python dev.py build` to build it.")

    if _use_inplace_client:
        return src

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if sys.platform != "win32":
            os.chmod(dst, 0o755)  # noqa: S103
        log.info("Installed Blendkit client to %s", dst)
        return dst
    except OSError as exc:
        log.warning("Could not install client to %s (%s); using in-plugin copy.", dst, exc)
        _use_inplace_client = True
        return src


def _log_path() -> str:
    log_dir = os.path.join(_prefs_mod.prefs.global_dir_resolved(), "client")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "default.log")


# ── URL / HTTP helpers ───────────────────────────────────────────────────────


def get_base_url(port: str | None = None) -> str:
    return f"http://127.0.0.1:{port or _active_port}/{_api_version()}"


def get_app_id() -> int:
    return _app_id


def _http_request(
    method: str,
    url: str,
    body: dict | None = None,
    *,
    connect_timeout: float = REQUEST_TIMEOUT,
    read_timeout: float = REQUEST_TIMEOUT,
) -> Any:
    """Minimal JSON-in / JSON-out HTTP call. Returns parsed JSON or None."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=max(connect_timeout, read_timeout)) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def _minimal_report_data() -> dict[str, Any]:
    return {
        "app_id": _app_id,
        "api_key": _prefs_mod.prefs.api_key,
        "addon_version": ADDON_VERSION,
        "platform_version": platform.platform(),
    }


def _prefs_block() -> dict[str, Any]:
    """The ``PREFS`` block the Go client embeds in every search/download task."""
    return {
        "api_key": _prefs_mod.prefs.api_key,
        "api_key_refresh": "",
        "api_key_timeout": 0,
        "scene_id": "",
        "app_id": _app_id,
        "unpack_files": False,
        "create_asset_library": False,
        "resolution": _prefs_mod.prefs.resolution,
        "project_subdir": "",
        "global_dir": "",
        "binary_path": "",
        "addon_dir": "",
        "addon_module_name": "bk_unreal",
    }


# ── Process launch ───────────────────────────────────────────────────────────


def _ping(port: str) -> bool:
    try:
        _http_request(
            "GET",
            f"http://127.0.0.1:{port}/{_api_version()}/report",
            body=_minimal_report_data(),
            connect_timeout=POLL_CONNECT_TIMEOUT,
            read_timeout=POLL_READ_TIMEOUT,
        )
        return True
    except Exception:
        return False


def _find_running_client() -> str | None:
    for port in CLIENT_PORTS:
        if _ping(port):
            return port
    return None


def _client_process_alive() -> bool:
    proc = _process
    return proc is not None and proc.poll() is None


def _spawn(port: str) -> subprocess.Popen:
    binary = _ensure_client_binary_installed()

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    p = _prefs_mod.prefs
    ssl_context = "DISABLED" if not getattr(p, "ssl_verification", True) else ""

    args = [
        binary,
        "--port",
        port,
        "--server",
        global_vars.SERVER,
        "--software",
        SOFTWARE_NAME,
        "--version",
        f"{ADDON_VERSION}.{ADDON_BUILD}",
        "--pid",
        str(os.getpid()),
    ]
    if ssl_context:
        args += ["--ssl_context", ssl_context]

    log_file = open(_log_path(), "ab")  # noqa: SIM115
    log.info("Spawning Blendkit client on port %s: %s", port, binary)
    return subprocess.Popen(
        args,
        stdout=log_file,
        stderr=log_file,
        creationflags=creation_flags,
        cwd=os.path.dirname(binary),
    )


def ensure_running() -> str | None:
    """Ensure a client is reachable; return the active port or ``None``.

    First reuse any running client, then reuse our own live subprocess, then
    spawn a new one, rotating through :data:`CLIENT_PORTS` on repeated failures.
    """
    global _process, _active_port, _port_index

    with _state_lock:
        running = _find_running_client()
        if running is not None:
            _active_port = running
            return running

        if _client_process_alive():
            return _active_port

        for _ in range(len(CLIENT_PORTS)):
            port = CLIENT_PORTS[_port_index % len(CLIENT_PORTS)]
            _port_index += 1
            try:
                _process = _spawn(port)
            except FileNotFoundError as exc:
                log.error("%s", exc)
                return None
            except OSError as exc:
                log.warning("Spawn on port %s failed: %s", port, exc)
                continue

            # Give it a moment to bind, then confirm.
            for _attempt in range(20):
                if _ping(port):
                    _active_port = port
                    _start_poller()
                    return port
                if not _client_process_alive():
                    break
                time.sleep(0.1)
            log.warning("Client did not answer on port %s; trying next.", port)

        return None


# ── Report poller ────────────────────────────────────────────────────────────


def register_task_callback(task_id: str, callback: Callable[[dict[str, Any]], None]) -> None:
    with _callbacks_lock:
        _task_callbacks[task_id] = callback


def register_thumbnail_callback(callback: Callable[[str, str], None] | None) -> None:
    """Register a global ``(asset_base_id, image_path)`` thumbnail sink.

    Thumbnails arrive as their own ``thumbnail_download`` tasks (with a task id
    distinct from the search that requested them), so they are routed here
    rather than through the per-task callback registry.
    """
    global _thumbnail_callback
    _thumbnail_callback = callback


def get_thumbnail_path(asset_base_id: str) -> str:
    """Return the best downloaded thumbnail path seen for *asset_base_id*, or ''.

    Covers the race where the client reported (and then dropped) a finished
    ``thumbnail_download`` task before the asset's tile existed to receive it.
    """
    with _thumbnail_paths_lock:
        return _thumbnail_paths.get(asset_base_id, "")


def clear_thumbnail_cache() -> None:
    """Forget cached thumbnail paths (call when starting a fresh search)."""
    with _thumbnail_paths_lock:
        _thumbnail_paths.clear()


def _drain_report(report: list[dict[str, Any]]) -> None:
    with _callbacks_lock:
        callbacks = dict(_task_callbacks)
    for task in report:
        task_type = task.get("task_type", "")

        if task_type == "thumbnail_download":
            if task.get("status") != "finished":
                continue
            data = task.get("data") or {}
            base_id = str(data.get("assetBaseId") or "")
            path = str(data.get("image_path") or "")
            if not base_id or not path:
                continue
            is_full = data.get("thumbnail_type") == "full"
            with _thumbnail_paths_lock:
                # "full" thumbs (the larger Middle image) win; otherwise keep
                # the first path so we never blank an already-shown image.
                if is_full or base_id not in _thumbnail_paths:
                    _thumbnail_paths[base_id] = path
            cb = _thumbnail_callback
            if cb is not None:
                try:
                    cb(base_id, path)
                except Exception as exc:
                    log.error("Thumbnail callback failed for %s: %s", base_id, exc)
            continue

        task_id = task.get("task_id", "")
        cb_task = callbacks.get(task_id)
        if cb_task is None:
            continue
        try:
            cb_task(task)
        except Exception as exc:
            log.error("Task callback for %s failed: %s", task_id, exc)
        if task.get("status") in ("finished", "error"):
            with _callbacks_lock:
                _task_callbacks.pop(task_id, None)


def _poll_loop() -> None:
    while not _poller_stop.is_set():
        try:
            report = _http_request(
                "GET",
                f"{get_base_url()}/report",
                body=_minimal_report_data(),
                connect_timeout=POLL_CONNECT_TIMEOUT,
                read_timeout=REQUEST_TIMEOUT,
            )
            if isinstance(report, list):
                _drain_report(report)
        except Exception:
            pass
        _poller_stop.wait(0.5)


def _start_poller() -> None:
    global _poller_thread
    if _poller_thread is not None and _poller_thread.is_alive():
        return
    _poller_stop.clear()
    _poller_thread = threading.Thread(target=_poll_loop, name="bk_unreal-report-poll", daemon=True)
    _poller_thread.start()


# ── Search ───────────────────────────────────────────────────────────────────


def asset_search(query: dict[str, Any], tempdir: str, callback: Callable[[dict[str, Any]], None]) -> str | None:
    """POST a search to the client and route results to *callback*.

    Returns the client-assigned task id, or ``None`` if no client is available.
    """
    port = ensure_running()
    if port is None:
        log.error("No Blendkit client available for search.")
        return None

    asset_type = str(query.get("asset_type", "model"))
    page_size = int(query.get("page_size", 24))
    urlquery = search.build_search_url(query, next_url=str(query.get("next", "") or ""))

    body = {
        "PREFS": _prefs_block(),
        "addon_version": ADDON_VERSION,
        "platform_version": platform.platform(),
        "api_key": _prefs_mod.prefs.api_key,
        "app_id": _app_id,
        "asset_type": asset_type,
        "blender_version": "0.0.0",  # client just echoes this back
        "get_next": False,
        "next": "",
        "page_size": page_size,
        "scene_uuid": str(uuid.uuid4()),
        "tempdir": tempdir,
        "urlquery": urlquery,
        "is_validator": False,
        "history_id": "",
    }
    log.info("asset_search: type=%s query=%r → port %s", asset_type, query.get("query", ""), port)
    try:
        resp = _http_request("POST", f"{get_base_url()}/blender/asset_search", body=body)
    except Exception as exc:
        log.error("asset_search POST failed: %s", exc)
        return None

    if not isinstance(resp, dict) or "task_id" not in resp:
        log.error("Unexpected /asset_search response: %r", resp)
        return None

    task_id = str(resp["task_id"])
    log.info("asset_search accepted, task_id=%s", task_id)
    register_task_callback(task_id, callback)
    _start_poller()
    return task_id


# ── Shutdown ─────────────────────────────────────────────────────────────────


def shutdown() -> None:
    """Stop the poller and terminate a client we spawned."""
    _poller_stop.set()
    with _state_lock:
        if _client_process_alive() and _process is not None:
            try:
                _process.terminate()
            except OSError:
                pass


atexit.register(shutdown)
