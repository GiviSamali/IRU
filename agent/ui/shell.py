from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

from core.config import AgentPaths, save_config
from core.logging_utils import tail_log
from core.runtime import AgentRuntime
from core.state import AgentState
from ui.setup import collect_setup


def launch_windows_shell(
    runtime: AgentRuntime,
    state: AgentState,
    config: dict,
    paths: AgentPaths,
    logger,
    startup_update_check: Callable[[], bool] | None = None,
) -> int:
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    icon = _build_icon(QtGui, QtWidgets, paths)
    app.setWindowIcon(icon)
    tray_available = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()

    status_window = None
    diagnostics_window = None
    tray = None
    runtime_started = False
    update_window_forced = False

    def refresh_views() -> None:
        nonlocal update_window_forced
        snapshot = state.snapshot()
        status_label = _status_label(snapshot.get("status", "disconnected"))
        update_state = str(snapshot.get("update_state", "") or "")
        update_progress = snapshot.get("update_progress", -1)
        update_detail = str(snapshot.get("update_detail", "") or "")
        update_summary = str(snapshot.get("last_update_check", "") or "-")
        show_update_section = update_state in {"checking_update", "update_available", "updating", "disabled"}
        update_active = update_state in {"checking_update", "updating"}

        try:
            progress_value = int(update_progress)
        except Exception:
            progress_value = -1

        if tray is not None:
            tray.setToolTip(f"IRU Agent - {status_label}")

        status_window.status_label.setText(f"Статус: {status_label}")
        status_window.device_label.setText(f"Устройство: {snapshot.get('device_id') or '-'}")
        status_window.server_label.setText(f"Сервер: {snapshot.get('server_url') or '-'}")
        status_window.version_label.setText(f"Версия агента: {snapshot.get('version') or '-'}")
        status_window.connected_label.setText(
            f"Последнее успешное подключение: {snapshot.get('last_connected_at') or '-'}"
        )
        status_window.disconnect_label.setText(
            f"Последняя причина дисконнекта: {snapshot.get('last_disconnect_reason') or '-'}"
        )
        status_window.update_box.setVisible(show_update_section)
        status_window.update_label.setText(f"Обновления: {update_summary}")
        status_window.update_detail_label.setText(update_detail or " ")
        if update_active:
            status_window.update_progress.setVisible(True)
            if progress_value < 0:
                status_window.update_progress.setRange(0, 0)
            else:
                status_window.update_progress.setRange(0, 100)
                status_window.update_progress.setValue(max(0, min(100, progress_value)))
        else:
            status_window.update_progress.setVisible(False)
            status_window.update_progress.setRange(0, 100)
            status_window.update_progress.setValue(0)

        if update_state == "updating" and tray_available and not update_window_forced:
            update_window_forced = True
            show_status_window()
        elif update_state != "updating":
            update_window_forced = False

        diagnostics_window.summary.setText(
            "\n".join(
                [
                    f"Статус: {status_label}",
                    f"Версия: {snapshot.get('version') or '-'}",
                    f"Устройство: {snapshot.get('device_id') or '-'}",
                    f"Server URL: {snapshot.get('server_url') or '-'}",
                    f"Конфиг: {snapshot.get('config_path') or '-'}",
                    f"Логи: {snapshot.get('log_path') or '-'}",
                    f"Последнее подключение: {snapshot.get('last_connected_at') or '-'}",
                    f"Последний дисконнект: {snapshot.get('last_disconnect_reason') or '-'}",
                    f"Проверка обновлений: {update_summary}",
                    f"Ход обновления: {update_detail or '-'}",
                ]
            )
        )
        diagnostics_window.log_tail.setPlainText(tail_log(paths.log_path))

    def show_status_window() -> None:
        refresh_views()
        status_window.showNormal()
        status_window.raise_()
        status_window.activateWindow()

    def show_diagnostics() -> None:
        refresh_views()
        diagnostics_window.showNormal()
        diagnostics_window.raise_()
        diagnostics_window.activateWindow()

    def open_logs_dir() -> None:
        try:
            os.startfile(str(paths.logs_dir))
        except Exception:
            logger.exception("Failed to open logs directory")

    def reconfigure() -> None:
        current = runtime.current_config()
        new_config = collect_setup(current)
        if not new_config:
            return
        merged = save_config(paths, {**current, **new_config}, logger=logger)
        runtime.update_config(merged)
        refresh_views()

    def shutdown() -> None:
        runtime.stop(wait=False)
        if tray is not None:
            tray.hide()
        app.quit()

    def start_runtime_if_needed() -> None:
        nonlocal runtime_started
        if runtime_started:
            return
        runtime.start()
        runtime_started = True

    class ShellSignals(QtCore.QObject):
        startup_update_done = QtCore.Signal(bool)

    signals = ShellSignals()

    def handle_startup_update_done(updater_launched: bool) -> None:
        if updater_launched:
            logger.info("[agent] updater launched from shell, shutting down UI process")
            shutdown()
            return
        start_runtime_if_needed()

    signals.startup_update_done.connect(handle_startup_update_done)

    class StatusWindow(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("IRU Agent")
            self.setWindowIcon(icon)
            self.setMinimumSize(480, 320)

            layout = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel("IRU Agent")
            title.setStyleSheet("font-size: 18px; font-weight: 600;")
            layout.addWidget(title)

            self.status_label = QtWidgets.QLabel()
            self.device_label = QtWidgets.QLabel()
            self.server_label = QtWidgets.QLabel()
            self.version_label = QtWidgets.QLabel()
            self.connected_label = QtWidgets.QLabel()
            self.disconnect_label = QtWidgets.QLabel()

            for widget in (
                self.status_label,
                self.device_label,
                self.server_label,
                self.version_label,
                self.connected_label,
                self.disconnect_label,
            ):
                widget.setWordWrap(True)
                layout.addWidget(widget)

            self.update_box = QtWidgets.QGroupBox("Обновление")
            update_layout = QtWidgets.QVBoxLayout(self.update_box)
            self.update_label = QtWidgets.QLabel()
            self.update_label.setWordWrap(True)
            self.update_detail_label = QtWidgets.QLabel()
            self.update_detail_label.setWordWrap(True)
            self.update_progress = QtWidgets.QProgressBar()
            self.update_progress.setMinimum(0)
            self.update_progress.setMaximum(100)
            self.update_progress.setValue(0)
            self.update_progress.setTextVisible(True)
            update_layout.addWidget(self.update_label)
            update_layout.addWidget(self.update_detail_label)
            update_layout.addWidget(self.update_progress)
            self.update_box.hide()
            layout.addWidget(self.update_box)

            row = QtWidgets.QHBoxLayout()
            reconnect_btn = QtWidgets.QPushButton("Переподключиться")
            reconnect_btn.clicked.connect(runtime.request_reconnect)
            reconfigure_btn = QtWidgets.QPushButton("Пере-настроить")
            reconfigure_btn.clicked.connect(reconfigure)
            diagnostics_btn = QtWidgets.QPushButton("Диагностика")
            diagnostics_btn.clicked.connect(show_diagnostics)
            row.addWidget(reconnect_btn)
            row.addWidget(reconfigure_btn)
            row.addWidget(diagnostics_btn)
            layout.addLayout(row)

        def closeEvent(self, event) -> None:
            if tray_available:
                event.ignore()
                self.hide()
                return
            shutdown()
            event.accept()

    class DiagnosticsWindow(QtWidgets.QDialog):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("IRU Agent - Диагностика")
            self.setWindowIcon(icon)
            self.resize(780, 560)

            layout = QtWidgets.QVBoxLayout(self)
            self.summary = QtWidgets.QLabel()
            self.summary.setWordWrap(True)
            layout.addWidget(self.summary)

            self.log_tail = QtWidgets.QPlainTextEdit()
            self.log_tail.setReadOnly(True)
            layout.addWidget(self.log_tail, stretch=1)

            row = QtWidgets.QHBoxLayout()
            open_logs_btn = QtWidgets.QPushButton("Открыть папку логов")
            open_logs_btn.clicked.connect(open_logs_dir)
            reconnect_btn = QtWidgets.QPushButton("Переподключиться")
            reconnect_btn.clicked.connect(runtime.request_reconnect)
            reconfigure_btn = QtWidgets.QPushButton("Пере-настроить")
            reconfigure_btn.clicked.connect(reconfigure)
            close_btn = QtWidgets.QPushButton("Закрыть")
            close_btn.clicked.connect(self.hide)
            for button in (open_logs_btn, reconnect_btn, reconfigure_btn, close_btn):
                row.addWidget(button)
            layout.addLayout(row)

        def closeEvent(self, event) -> None:
            event.ignore()
            self.hide()

    status_window = StatusWindow()
    diagnostics_window = DiagnosticsWindow()

    if tray_available:
        tray = QtWidgets.QSystemTrayIcon(icon, app)
        tray.setToolTip("IRU Agent")
        tray_menu = QtWidgets.QMenu()
        tray_menu.addAction("Открыть статус", show_status_window)
        tray_menu.addAction("Открыть диагностику", show_diagnostics)
        tray_menu.addSeparator()
        tray_menu.addAction("Переподключиться", runtime.request_reconnect)
        tray_menu.addAction("Пере-настроить", reconfigure)
        tray_menu.addAction("Открыть папку логов", open_logs_dir)
        tray_menu.addSeparator()
        tray_menu.addAction("Выход", shutdown)
        tray.setContextMenu(tray_menu)

        def on_tray_activated(reason) -> None:
            if reason in (
                QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
                QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
            ):
                show_status_window()

        tray.activated.connect(on_tray_activated)
        tray.show()
        logger.info("[agent] tray mode enabled")
    else:
        logger.warning("[agent] system tray is unavailable, keeping status window visible")

    timer = QtCore.QTimer()
    timer.setInterval(500)
    timer.timeout.connect(refresh_views)
    refresh_views()
    timer.start()

    def run_startup_update_check() -> None:
        updater_launched = False
        try:
            if startup_update_check is not None:
                updater_launched = bool(startup_update_check())
        except Exception:
            logger.exception("[agent] startup update check failed")
        signals.startup_update_done.emit(updater_launched)

    def begin_startup() -> None:
        if startup_update_check is None:
            start_runtime_if_needed()
            return
        threading.Thread(
            target=run_startup_update_check,
            name="IRUAgentUpdateCheck",
            daemon=True,
        ).start()

    if not tray_available:
        status_window.show()
    QtCore.QTimer.singleShot(0, begin_startup)

    exit_code = app.exec()
    runtime.stop(wait=False)
    return exit_code


def _build_icon(QtGui, QtWidgets, paths: AgentPaths):
    candidates: list[Path] = []
    candidates.append(paths.source_icon_path)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "IruIcon.ico")
        candidates.append(Path(sys.executable).parent / "icon.ico")
    for candidate in candidates:
        try:
            if candidate.exists():
                icon = QtGui.QIcon(str(candidate))
                if not icon.isNull():
                    return icon
        except Exception:
            continue
    return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)


def _status_label(status: str) -> str:
    mapping = {
        "connecting": "connecting",
        "connected": "connected",
        "disconnected": "disconnected",
        "checking_update": "checking_update",
        "update_available": "update_available",
        "updating": "updating",
        "config_error": "config_error",
        "disabled": "disabled",
    }
    return mapping.get(status, status)
