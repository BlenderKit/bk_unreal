"""Blendkit preferences dialog — Qt window hosted inside the Unreal editor.

Entry point: :func:`open_settings`.

Edits the process-wide :data:`bk_unreal.core.prefs.prefs` singleton and persists
it to the JSON store under the Blendkit global directory. Kept intentionally
small; mirrors the subset of bk_maya's preferences that the Unreal port acts on
(API key, SSL verification, global data dir, import resolution).
"""

from __future__ import annotations

import logging
from typing import Any

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ..core import auth, blender_runner
from ..core.prefs import RESOLUTIONS, prefs
from ..core.qt_host import get_qapp, parent_to_editor

log = logging.getLogger(__name__)

WINDOW_TITLE = "Blendkit Settings"

_current_dialog: SettingsDialog | None = None


class SettingsDialog(QDialog):
    """Preferences editor for the Blendkit Unreal plugin."""

    # Emitted from the login worker thread; queued to the GUI thread by Qt
    # because signal/slot connections across threads default to queued.
    _login_finished = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(460)
        self._build_ui()
        self._load_from_prefs()
        self._login_finished.connect(self._on_login_finished)
        auth.add_login_listener(self._refresh_account_status)

    def closeEvent(self, event: Any) -> None:
        auth.remove_login_listener(self._refresh_account_status)
        super().closeEvent(event)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        form = QFormLayout(self)

        self.account_status = QLabel("")
        form.addRow("Account", self.account_status)

        account_row = QWidget()
        account_layout = QHBoxLayout(account_row)
        account_layout.setContentsMargins(0, 0, 0, 0)
        self.login_button = QPushButton("Log In via Browser\u2026")
        self.login_button.clicked.connect(self._start_login)
        self.logout_button = QPushButton("Log Out")
        self.logout_button.clicked.connect(self._logout)
        account_layout.addWidget(self.login_button)
        account_layout.addWidget(self.logout_button)
        form.addRow("", account_row)

        self.api_key_field = QLineEdit()
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_field.setPlaceholderText("Paste API key here\u2026 (fallback if login doesn't work)")
        apply_key = QPushButton("Apply")
        apply_key.clicked.connect(self._apply_manual_key)
        api_key_row = QWidget()
        api_key_layout = QHBoxLayout(api_key_row)
        api_key_layout.setContentsMargins(0, 0, 0, 0)
        api_key_layout.addWidget(self.api_key_field, 1)
        api_key_layout.addWidget(apply_key)
        form.addRow("API key", api_key_row)

        self.ssl_check = QCheckBox("Verify SSL certificates")
        form.addRow("Security", self.ssl_check)

        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        self.global_dir_field = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dir)
        dir_layout.addWidget(self.global_dir_field, 1)
        dir_layout.addWidget(browse)
        form.addRow("Data directory", dir_row)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTIONS)
        form.addRow("Import resolution", self.resolution_combo)

        blender_row = QWidget()
        blender_layout = QHBoxLayout(blender_row)
        blender_layout.setContentsMargins(0, 0, 0, 0)
        self.blender_field = QLineEdit()
        self.blender_field.setPlaceholderText("Auto-detect (leave empty)")
        blender_browse = QPushButton("Browse…")
        blender_browse.clicked.connect(self._browse_blender)
        blender_detect = QPushButton("Detect")
        blender_detect.clicked.connect(self._detect_blender)
        blender_layout.addWidget(self.blender_field, 1)
        blender_layout.addWidget(blender_browse)
        blender_layout.addWidget(blender_detect)
        form.addRow("Blender executable", blender_row)

        self.blender_status = QLabel("")
        self.blender_status.setWordWrap(True)
        form.addRow("", self.blender_status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    # ── State sync ───────────────────────────────────────────────────────────

    def _load_from_prefs(self) -> None:
        self._refresh_account_status()
        self.ssl_check.setChecked(prefs.ssl_verification)
        self.global_dir_field.setText(prefs.global_dir)
        index = self.resolution_combo.findText(prefs.resolution)
        self.resolution_combo.setCurrentIndex(index if index >= 0 else self.resolution_combo.count() - 1)
        self.blender_field.setText(prefs.blender_exe)
        self._detect_blender()

    def _refresh_account_status(self) -> None:
        """Update the status label + button enabled-state from the auth module.

        Safe to call from the poller thread (only touches these widgets'
        thread-safe setters) - registered as an :func:`auth.add_login_listener`.
        """
        logged_in = auth.is_logged_in()
        self.account_status.setText("\u25cf Logged in" if logged_in else "\u25cb Not logged in")
        self.login_button.setEnabled(not logged_in)
        self.logout_button.setEnabled(logged_in)

    def _start_login(self) -> None:
        self.login_button.setEnabled(False)
        self.login_button.setText("Waiting for browser\u2026")
        auth.login_async(self._login_finished.emit)

    def _on_login_finished(self, ok: bool) -> None:
        self.login_button.setText("Log In via Browser\u2026")
        self._refresh_account_status()
        if not ok:
            log.warning("Blendkit login did not complete.")

    def _logout(self) -> None:
        auth.logout()
        self._refresh_account_status()

    def _apply_manual_key(self) -> None:
        key = self.api_key_field.text().strip()
        if not key:
            return
        auth.set_manual_api_key(key)
        self.api_key_field.clear()
        self._refresh_account_status()

    def _browse_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Blendkit data directory", self.global_dir_field.text())
        if chosen:
            self.global_dir_field.setText(chosen)

    def _browse_blender(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Select Blender executable", self.blender_field.text())
        if chosen:
            self.blender_field.setText(blender_runner.resolve_macos_app(chosen))
            self._detect_blender()

    def _detect_blender(self) -> None:
        """Resolve and validate the current Blender path; update the status label."""
        # Fall back to auto-detection when the field is empty.
        override = self.blender_field.text().strip()
        exe = blender_runner.resolve_macos_app(override) if override else blender_runner.find_blender_executable()
        if not exe:
            self.blender_status.setText(
                "No Blender found. Blendkit needs Blender "
                f"{blender_runner.MIN_BLENDER_MAJOR}.0 or newer for background processing."
            )
            return
        version = blender_runner.query_blender_version(exe)
        if version is None:
            self.blender_status.setText(f"Could not read the Blender version from:\n{exe}")
            return
        ver_str = ".".join(str(x) for x in version)
        if blender_runner.version_meets_min(version):
            self.blender_status.setText(f"Blender {ver_str} detected — OK.\n{exe}")
        else:
            self.blender_status.setText(
                f"Blender {ver_str} is too old — {blender_runner.MIN_BLENDER_MAJOR}.0 or newer is required."
            )

    def _save(self) -> None:
        prefs.ssl_verification = self.ssl_check.isChecked()
        prefs.global_dir = self.global_dir_field.text().strip()
        prefs.resolution = self.resolution_combo.currentText()
        prefs.blender_exe = self.blender_field.text().strip()
        prefs.save()
        log.info("Blendkit settings saved.")
        self.accept()


def open_settings() -> SettingsDialog | None:
    """Create (or raise) the settings dialog inside the Unreal editor."""
    global _current_dialog
    app = get_qapp()
    if app is None:
        log.error("Cannot open settings: Qt is unavailable.")
        return None

    if _current_dialog is not None:
        _current_dialog.show()
        _current_dialog.raise_()
        return _current_dialog

    dialog = SettingsDialog()
    parent_to_editor(dialog)
    dialog.finished.connect(_on_closed)
    dialog.show()
    dialog.raise_()
    _current_dialog = dialog
    return dialog


def _on_closed(_result: int) -> None:
    global _current_dialog
    _current_dialog = None
