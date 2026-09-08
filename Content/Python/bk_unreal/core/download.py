"""Download and Blender export pipeline for drag-to-place models.

This module is the Unreal-side orchestration layer for the model import flow:

1. resolve the correct asset file for the user's selected resolution,
2. download it into the per-asset cache under the Blendkit global dir,
3. launch a headless Blender export recipe that produces a .usda stage,
4. import the resulting USD into Unreal at the drop position.

The implementation intentionally stays engine-agnostic and Qt-free, so the unit
suite can cover the path-generation and stdout parsing logic without Unreal.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
    out_path = os.path.join(out_dir, f"{Path(blend_file).stem}.usd")
    return {
        "blend_path": blend_file,
        "out_usd": out_path,
        "max_resolution": str(resolution or prefs.prefs.resolution),
        "export_mtlx": True,
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


class DownloadController:
    """Controller that handles a single model start-to-import job."""

    def __init__(
        self,
        *,
        asset_data: dict[str, Any],
        drop_location: tuple[float, float, float],
        drop_normal: tuple[float, float, float],
        drop_rotation_z: float,
        progress_callback: Callable[[float, str], None] | None = None,
        finished_callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.asset_data = asset_data
        self.drop_location = drop_location
        self.drop_normal = drop_normal
        self.drop_rotation_z = drop_rotation_z
        self.asset_id = str(asset_data.get("assetBaseId") or asset_data.get("id") or "")
        self.model_dir = model_cache_dir(asset_data, prefs.prefs.resolution)
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
        """Begin the model download/export/import sequence asynchronously."""
        if self.started:
            return
        self.started = True
        self._thread = threading.Thread(target=self._run, name="bk-download-controller", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Worker entry point for the async pipeline."""
        try:
            self._notify_progress(0.0, "Starting")
            self._download_model()
            usd_path = self._export_model()
            from . import usd_import

            self._notify_progress(0.98, "Importing")
            self._import_on_editor_thread(usd_path, usd_import)
            self._notify_progress(1.0, "Imported")
            self._notify_finished(True, "Imported")
        except Exception as exc:  # pragma: no cover - guard for editor runtime
            self.error = str(exc)
            self._notify_finished(False, "Failed")
            log.exception("Model import pipeline failed: %s", exc)
        self.finished = True

    def _import_on_editor_thread(self, usd_path: str, usd_import: Any) -> None:
        """Queue Unreal actor creation onto the editor thread and await it."""
        from . import qt_host

        completed = threading.Event()
        failure: list[BaseException] = []

        def _import() -> None:
            try:
                usd_import.import_stage(
                    usd_path,
                    location=self.drop_location,
                    normal=self.drop_normal,
                    rotation_z=self.drop_rotation_z,
                )
            except BaseException as exc:
                failure.append(exc)
            finally:
                completed.set()

        qt_host.run_on_editor_thread(_import)
        if not completed.wait(60.0):
            raise TimeoutError("Timed out waiting for the Unreal USD stage import.")
        if failure:
            raise failure[0]

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

        def _on_task(task: dict[str, Any]) -> None:
            task_state.update(task)
            progress = task.get("progress")
            if progress is not None:
                self._notify_progress(
                    0.35 + max(0.0, min(1.0, float(progress) / 100.0)) * 0.6, task.get("message") or "Exporting"
                )
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
            asset_data=self.asset_data,
        )
        task_id = resp.get("task_id")
        if not task_id:
            raise RuntimeError(f"Client rejected the Blender export request: {resp!r}")
        client_lib.register_task_callback(str(task_id), _on_task)
        log.info("Blender export task accepted: %s", task_id)
        self._notify_progress(0.4, "Exporting")

        deadline = time.monotonic() + 900.0
        while not completed.wait(0.5):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Blender export task timed out: {task_id}")
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
