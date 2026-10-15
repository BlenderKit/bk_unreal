"""Open the native "Report a Problem" window with prefilled environment info."""

from __future__ import annotations

import logging
import webbrowser

from ..core import report

log = logging.getLogger(__name__)


def _open_url(url: str) -> None:
    try:
        import unreal

        unreal.SystemLibrary.launch_url(url)
    except Exception:
        webbrowser.open(url)


def open_report_window() -> None:
    """Show the native report window, or open the prefilled bug form if the native module is missing."""
    info = report.get_environment_info()
    bug_url = report.get_report_bug_url(info)
    try:
        import unreal

        bridge = unreal.BlendkitBridge.get()
        bridge.open_report_window(
            unreal.BlendkitReportInfo(
                environment_text=report.get_environment_info_string(info),
                bug_report_url=bug_url,
                feature_request_url=report.get_feature_request_url(info),
                issues_url=report.ISSUES_URL,
                unreal_log_dir=report.get_unreal_log_dir(),
                client_log_dir=report.get_client_log_dir(),
            )
        )
    except Exception as exc:
        log.warning("Native report window unavailable (%s); opening the bug report in the browser.", exc)
        log.info("Environment info:\n%s", report.get_environment_info_string(info))
        _open_url(bug_url)
