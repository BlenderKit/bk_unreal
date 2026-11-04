"""Download and import pipelines for drag-to-place models and 360 HDRIs.

This module is the Unreal-side orchestration layer for the model import flow:

1. resolve the correct asset file for the user's selected resolution,
2. download it into the per-asset cache under the Blendkit global dir,
3. launch a headless Blender export recipe that produces a .usda stage,
4. import the resulting USD into Unreal at the drop position.

HDRIs download a single equirectangular .hdr/.exr image into ``hdrs`` and
create environment lighting directly, without Blender or USD.

The implementation intentionally stays engine-agnostic and Qt-free, so the unit
suite can cover the path-generation and stdout parsing logic without Unreal.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import blender_runner, prefs

log = logging.getLogger(__name__)

_RESOLUTION_MAP = {
    "512": "0_5K",
    "1024": "1K",
    "2048": "2K",
    "4096": "4K",
    "8192": "8K",
}


def resolution_token(resolution: str) -> str:
    """Return the Blendkit resolution token for a given string value."""
    value = str(resolution or "ORIGINAL").strip()
    if value == "ORIGINAL":
        return "ORIGINAL"
    if value in _RESOLUTION_MAP:
        return _RESOLUTION_MAP[value]
    return value


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (value or "asset").lower()).strip("_")
    return text or "asset"


def model_cache_dir(asset_data: dict[str, Any], resolution: str) -> str:
    """Return the per-asset model cache dir, mirroring bk_maya's layout."""
    global_dir = prefs.prefs.global_dir_resolved()
    slug = _slugify(str(asset_data.get("slug") or asset_data.get("name") or asset_data.get("assetBaseId") or "asset"))
    asset_id = str(asset_data.get("id") or asset_data.get("assetBaseId") or "0")
    token = resolution_token(resolution)
    return os.path.join(global_dir, "models", f"{slug}_{asset_id}", f"{slug}_{token}_{asset_id}")


def build_export_args(blend_path: str, output_dir: str, resolution: str) -> dict[str, Any]:
    """Return parameter dict for the bundled headless Blender export script."""
    blend_file = os.path.abspath(str(blend_path))
    out_dir = os.path.abspath(str(output_dir))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{Path(blend_file).stem}_unreal.usd")
    return {
        "blend_path": blend_file,
        "out_usd": out_path,
        "max_resolution": str(resolution or prefs.prefs.resolution),
        "export_mtlx": True,
        "normal_map_target": "unreal",
    }


def _stage_has_self_sublayer(usda_path: str) -> bool:
    if not str(usda_path).lower().endswith(".usda") or not os.path.isfile(usda_path):
        return False
    basename = os.path.basename(usda_path)
    try:
        with open(usda_path, encoding="utf-8") as fh:
            text = fh.read(4096)
    except OSError:
        return False
    return f"@./{basename}@" in text or f"@{basename}@" in text


def parse_stdout_lines(lines: list[str]) -> dict[str, Any]:
    """Parse a BK_* stdout stream from the export script into a structured dict."""
    parsed: dict[str, Any] = {
        "status": "",
        "progress": 0.0,
        "done_path": "",
        "artifact": "",
        "error": "",
    }
    for raw in lines:
        line = str(raw).strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0].upper() if parts else ""
        if tag == "BK_STATUS" and len(parts) >= 2:
            parsed["status"] = parts[1]
        elif tag == "BK_PROGRESS" and len(parts) >= 3:
            try:
                parsed["progress"] = float(parts[1])
            except ValueError:
                pass
        elif tag == "BK_DONE" and len(parts) >= 2:
            parsed["done_path"] = parts[1]
        elif tag == "BK_ARTIFACT" and len(parts) >= 2:
            parsed["artifact"] = parts[1]
        elif tag == "BK_ERROR" and len(parts) >= 2:
            parsed["error"] = " ".join(parts[1:])
    if not parsed["done_path"] and parsed["error"]:
        parsed["done_path"] = ""
    return parsed


# Mirrors bk_client's run_blender_script.go: recipe progress 0..1 maps onto task progress 12..99.
_RECIPE_TASK_PROGRESS_START = 12.0
_RECIPE_TASK_PROGRESS_END = 99.0
# Export task progress 0..100 occupies this slice of the whole download/import bar.
_EXPORT_FRACTION_START = 0.35
_EXPORT_FRACTION_SPAN = 0.6


def export_task_progress(task: dict[str, Any], previous: float) -> tuple[float, str]:
    """Map a ``run_blender_script`` task report to an overall (fraction, message).

    Older clients report only "Launching Blender" (10%), so ``BK_PROGRESS``
    recipe lines in ``message_detailed`` are parsed as well. Never decreases.

    Args:
        task: the client's task report.
        previous: the overall fraction already shown.

    Returns:
        The new overall fraction and the task's status message ("" if none).
    """
    task_progress = 0.0
    with contextlib.suppress(TypeError, ValueError):
        task_progress = float(task.get("progress") or 0.0)
    message = str(task.get("message") or "")
    detail = str(task.get("message_detailed") or "").strip()
    if detail.startswith("[blender] "):
        detail = detail[len("[blender] ") :]
    if detail.startswith("BK_PROGRESS "):
        parts = detail.split(maxsplit=2)
        with contextlib.suppress(IndexError, ValueError):
            recipe = max(0.0, min(1.0, float(parts[1])))
            span = _RECIPE_TASK_PROGRESS_END - _RECIPE_TASK_PROGRESS_START
            task_progress = max(task_progress, _RECIPE_TASK_PROGRESS_START + recipe * span)
            if len(parts) > 2:
                message = parts[2]
    fraction = _EXPORT_FRACTION_START + max(0.0, min(1.0, task_progress / 100.0)) * _EXPORT_FRACTION_SPAN
    return max(previous, fraction), message


def export_phase_label(message: str, elapsed: float) -> str:
    """Append seconds spent in the current phase so long single steps still visibly advance."""
    seconds = int(elapsed)
    return f"{message} ({seconds}s)" if seconds >= 2 else message


_FAILURE_REASON_LIMIT = 400
# Seconds the worker waits for the editor tick to start the queued import (not the import itself).
EDITOR_PICKUP_TIMEOUT = 120.0


def failure_reason(text: str) -> str:
    """Pick the most useful one-line reason out of a pipeline failure message.

    Prefers the recipe's last ``BK_ERROR`` line, then the last Python exception
    line, then the first line of ``text``. ``[blender]`` prefixes are dropped.

    Args:
        text: the raw exception text (may include the whole Blender stdout).

    Returns:
        A short human-readable reason.
    """
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("[blender]"):
            line = line[len("[blender]") :].strip()
        if line:
            lines.append(line)
    reason = ""
    for line in reversed(lines):
        if line.startswith("BK_ERROR "):
            reason = line[len("BK_ERROR ") :].strip()
            break
    if not reason:
        for line in reversed(lines):
            head = line.split(":", 1)[0]
            if ":" in line and head.isidentifier() and head.endswith(("Error", "Exception")):
                reason = line
                break
    if not reason and lines:
        reason = lines[0]
    reason = reason or "Unknown error"
    if len(reason) > _FAILURE_REASON_LIMIT:
        reason = reason[: _FAILURE_REASON_LIMIT - 3].rstrip() + "..."
    return reason


def show_failure_dialog(asset_name: str, reason: str) -> None:
    """Show a modal editor dialog explaining why an asset import failed (editor thread only)."""
    try:
        import unreal
    except ImportError:
        return
    message = f"Blendkit could not import '{asset_name}'.\n\nReason: {reason}\n\nSee the Output Log for full details."
    try:
        unreal.EditorDialog.show_message("Blendkit import failed", message, unreal.AppMsgType.OK)
    except Exception as exc:
        log.warning("Could not show import failure dialog: %s", exc)


class DownloadController:
    """Controller that handles a single asset start-to-import job."""

    def __init__(
        self,
        *,
        asset_data: dict[str, Any],
        drop_location: tuple[float, float, float],
        drop_normal: tuple[float, float, float],
        drop_rotation_z: float,
        drop_pivot_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
        progress_callback: Callable[[float, str], None] | None = None,
        finished_callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.asset_data = asset_data
        self.drop_location = drop_location
        self.drop_normal = drop_normal
        self.drop_rotation_z = drop_rotation_z
        self.drop_pivot_offset = drop_pivot_offset
        self.asset_id = str(asset_data.get("assetBaseId") or asset_data.get("id") or "")
        self.is_hdri = str(asset_data.get("assetType") or "").lower() in ("hdr", "hdri")
        self.resolution = str(prefs.prefs.resolution)
        self.model_dir = "" if self.is_hdri else model_cache_dir(asset_data, self.resolution)
        self.started = False
        self.finished = False
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._progress_callback = progress_callback
        self._finished_callback = finished_callback

    def _notify_progress(self, progress: float, status: str) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(progress, status)
        except Exception as exc:
            log.debug("Download progress callback failed: %s", exc)

    def _notify_finished(self, success: bool, status: str) -> None:
        if self._finished_callback is None:
            return
        try:
            self._finished_callback(success, status)
        except Exception as exc:
            log.debug("Download finished callback failed: %s", exc)

    def start(self) -> None:
        """Begin the asset download/import sequence asynchronously."""
        if self.started:
            return
        self.started = True
        self._thread = threading.Thread(target=self._run, name="bk-download-controller", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Worker entry point for the async pipeline."""
        try:
            self._notify_progress(0.0, "Starting")
            if self.is_hdri:
                from . import hdri_import as importer

                import_path = self._download_hdri()
            else:
                from . import usd_import as importer

                self._download_model()
                import_path = self._export_model()
            self._notify_progress(0.98, "Importing")
            self._import_on_editor_thread(import_path, importer)
            self._notify_progress(1.0, "Imported")
            self._notify_finished(True, "Imported")
        except Exception as exc:  # pragma: no cover - guard for editor runtime
            self.error = str(exc)
            reason = failure_reason(self.error)
            self._notify_finished(False, "Failed")
            log.exception("Asset import pipeline failed: %s", exc)
            log.error("Asset import failed: %s", reason)
            self._report_failure(reason)
        self.finished = True

    def _report_failure(self, reason: str) -> None:
        from . import editor_thread

        asset_name = str(self.asset_data.get("name") or self.asset_data.get("displayName") or self.asset_id)
        try:
            editor_thread.run_on_editor_thread(lambda: show_failure_dialog(asset_name, reason))
        except Exception as exc:
            log.debug("Could not queue import failure dialog: %s", exc)

    def _import_on_editor_thread(self, usd_path: str, usd_import: Any) -> None:
        """Queue Unreal actor creation onto the editor thread and await it."""
        from . import editor_thread

        started = threading.Event()
        completed = threading.Event()
        failure: list[BaseException] = []

        def _import() -> None:
            started.set()
            try:
                if self.is_hdri:
                    usd_import.import_hdri(usd_path, rotation_z=self.drop_rotation_z)
                else:
                    usd_import.import_stage(
                        usd_path,
                        location=self.drop_location,
                        normal=self.drop_normal,
                        rotation_z=self.drop_rotation_z,
                        pivot_offset=self.drop_pivot_offset,
                    )
            except BaseException as exc:
                failure.append(exc)
            finally:
                completed.set()

        editor_thread.run_on_editor_thread(_import)
        # Only bound the wait for the editor to pick the job up. Once running, the import blocks the
        # editor thread itself (Nanite builds of multi-million-triangle meshes take minutes), so a
        # deadline there would only abandon an import that is about to succeed.
        if not started.wait(EDITOR_PICKUP_TIMEOUT):
            raise TimeoutError("Timed out waiting for the Unreal editor to start the asset import.")
        completed.wait()
        if failure:
            raise failure[0]

    def _download_hdri(self) -> str:
        """Download the selected 360 image via the shared Go client, preserving its format."""
        from . import client_lib

        if not self.asset_id:
            raise RuntimeError("HDRI is missing an asset id.")
        slug = _slugify(str(self.asset_data.get("slug") or self.asset_data.get("name") or self.asset_id))
        asset_id = _slugify(str(self.asset_data.get("id") or self.asset_id))
        directory = os.path.join(prefs.prefs.global_dir_resolved(), "hdrs", f"{slug}_{asset_id}")
        stem = f"{slug}_{resolution_token(self.resolution)}"
        for extension in (".hdr", ".exr"):
            cached = os.path.join(directory, stem + extension)
            if os.path.isfile(cached) and os.path.getsize(cached) > 0:
                self._notify_progress(0.95, "Using cached HDRI")
                return cached

        result = client_lib.get_download_url(self.asset_data, self.resolution)
        url = str(result.get("download_url") or "")
        if result.get("can_download") is False or not url:
            raise RuntimeError(f"No permitted HDRI download URL for asset {self.asset_id}.")
        extension = ""
        for source in (str(result.get("filename") or ""), url):
            candidate = os.path.splitext(unquote(urlparse(source).path))[1].lower()
            if candidate in (".hdr", ".hdri", ".exr"):
                extension = ".hdr" if candidate == ".hdri" else candidate
                break
        if not extension:
            raise RuntimeError("Expected a 360 HDRI image (.hdr or .exr) from the Blendkit client.")

        os.makedirs(directory, exist_ok=True)
        destination = os.path.join(directory, stem + extension)
        partial = destination + ".part"
        self._notify_progress(0.05, "Downloading HDRI")
        try:
            if not client_lib.blocking_file_download(url, partial):
                raise RuntimeError("The Blendkit client failed to download the HDRI.")
            if not os.path.isfile(partial) or os.path.getsize(partial) == 0:
                raise RuntimeError("The Blendkit client produced an empty HDRI download.")
            os.replace(partial, destination)
        finally:
            if os.path.isfile(partial):
                os.remove(partial)
        self._notify_progress(0.95, "Downloaded HDRI")
        return destination

    def _download_model(self) -> None:
        """Resolve a model URL, download it into the cache, and ensure it is a .blend."""
        if not self.asset_id:
            raise RuntimeError("Asset is missing an asset id; cannot resolve model cache path.")

        os.makedirs(self.model_dir, exist_ok=True)
        file_path = os.path.join(self.model_dir, f"{self.asset_id}.blend")
        if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
            log.info("Using cached model: %s", file_path)
            self._notify_progress(0.35, "Using cached blend")
            return

        from . import client_lib

        resolution = str(prefs.prefs.resolution)
        result = client_lib.get_download_url(self.asset_data, resolution)
        url = result.get("download_url") or ""
        if not url:
            raise RuntimeError(f"No download URL returned for asset {self.asset_id}: {result!r}")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        self._notify_progress(0.05, "Downloading")
        client_lib.blocking_file_download(url, file_path)
        if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
            raise RuntimeError(f"Downloaded file did not land at {file_path}")
        self._notify_progress(0.35, "Downloaded")

    def _export_model(self) -> str:
        """Export the local .blend to a .usda file with Blender in headless mode."""
        from . import client_lib

        blend_path = os.path.join(self.model_dir, f"{self.asset_id}.blend")
        if not os.path.isfile(blend_path):
            raise RuntimeError(f"Expected blend file missing: {blend_path}")

        blender_exe = blender_runner.find_blender_executable()
        if not blender_exe:
            raise RuntimeError("No Blender executable found for background export.")

        args = build_export_args(blend_path, self.model_dir, prefs.prefs.resolution)
        out_path = str(args["out_usd"])
        out_usda = os.path.splitext(out_path)[0] + ".usda"
        if _stage_has_self_sublayer(out_usda):
            log.warning("Removing broken cached USD wrapper with self-sublayer: %s", out_usda)
            os.remove(out_usda)
        completed = threading.Event()
        task_state: dict[str, Any] = {}
        export_state = {"fraction": 0.4, "message": "Exporting"}

        def _on_task(task: dict[str, Any]) -> None:
            task_state.update(task)
            fraction, message = export_task_progress(task, export_state["fraction"])
            export_state["fraction"] = fraction
            export_state["message"] = message or export_state["message"]
            if task.get("status") in ("finished", "error"):
                completed.set()

        resp = client_lib.run_blender_script(
            script_id="export_usd",
            blender_exe_path=blender_exe,
            blend_path=blend_path,
            output_path=out_path,
            out_usd=out_path,
            max_resolution=str(args["max_resolution"]),
            export_mtlx=True,
            normal_map_target=str(args["normal_map_target"]),
            asset_data=self.asset_data,
        )
        task_id = resp.get("task_id")
        if not task_id:
            raise RuntimeError(f"Client rejected the Blender export request: {resp!r}")
        client_lib.register_task_callback(str(task_id), _on_task)
        log.info("Blender export task accepted: %s", task_id)
        self._notify_progress(export_state["fraction"], export_state["message"])

        deadline = time.monotonic() + 900.0
        phase_message = ""
        phase_started = time.monotonic()
        while not completed.wait(0.5):
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Blender export task timed out: {task_id}")
            message = export_state["message"]
            if message != phase_message:
                phase_message, phase_started = message, now
            self._notify_progress(export_state["fraction"], export_phase_label(message, now - phase_started))
        if task_state.get("status") == "error":
            message = task_state.get("message") or "Blender export failed"
            detail = task_state.get("message_detailed") or ""
            if detail and detail not in message:
                message = f"{message}: {detail}"
            raise RuntimeError(message)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Blender export did not produce a .usd file at {out_path}")
        primary_path = out_usda if os.path.isfile(out_usda) and not _stage_has_self_sublayer(out_usda) else out_path
        log.info("Export artifact ready: %s", primary_path)
        return primary_path


__all__ = [
    "DownloadController",
    "build_export_args",
    "model_cache_dir",
    "parse_stdout_lines",
    "resolution_token",
]
