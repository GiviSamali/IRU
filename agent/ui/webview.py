"""IRU website in WebView2; existing Qt menu, settings and tray stay native."""
from __future__ import annotations

import logging
import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6 import QtCore, QtGui, QtWidgets
from ui.edge import EdgeWebView, origin
from ui.window_state import load_window_state, save_window_state


def resolve_site_url(config: dict) -> str:
    raw = os.environ.get("IRU_WEB_URL", "").strip() or str(config.get("server_url") or "wss://irumode.ru")
    url = urlsplit(raw)
    scheme = {"wss": "https", "ws": "http"}.get(url.scheme, url.scheme)
    if scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
        raise ValueError("IRU WebView requires an HTTP(S) URL without credentials")
    return urlunsplit((scheme, url.netloc, url.path or "/", "", ""))


class IruMainWindow(QtWidgets.QMainWindow):
    def __init__(self, *, site_url, config_dir: Path, icon, tray_available,
                 show_agent_settings, shutdown):
        super().__init__()
        self.tray_available = tray_available
        self.shutdown = shutdown
        self.web_view = None
        self.setWindowTitle("ИРУ")
        self.setWindowIcon(icon)
        self._preferences_path = config_dir / "window.json"
        self._rectangles = load_window_state(self._preferences_path)
        self._always_on_top = self._rectangles.get("always_on_top", False)
        self.is_compact = True
        self._changing_geometry = False
        self._surface_manual = self._rectangles.get("manual_compact", False)
        self._surface_measurement_pending = False
        self._surface_candidate = None
        self._interactive_size = None
        self._surface_animation = QtCore.QVariantAnimation(self)
        self._surface_animation.setDuration(190)
        self._surface_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._surface_animation.valueChanged.connect(self._apply_surface_size)
        self._surface_timer = QtCore.QTimer(self)
        self._surface_timer.setInterval(400)
        self._surface_timer.timeout.connect(self._measure_surface)
        self._notice = ""
        self._screen_connections = set()
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_geometry)
        self._surface_animation.finished.connect(self._save_timer.start)
        self.setMinimumSize(320, 160)
        self.resize(360, 400)

        menu = self.menuBar().addMenu("ИРУ")
        menu.addAction("Настройки агента", show_agent_settings)
        menu.addAction("Обновить страницу", self._reload)
        menu.addAction("Открыть в браузере", lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(site_url)))
        menu.addAction("Компактное окно", lambda: self.set_compact(True))
        menu.addAction("Размер по содержимому", self._resume_surface_size)
        menu.addAction("Развернуть окно", lambda: self.set_compact(False))
        self.always_on_top_action = menu.addAction("Всегда сверху")
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.setChecked(self._always_on_top)
        self.always_on_top_action.toggled.connect(self.set_always_on_top)
        if tray_available:
            menu.addAction("Скрыть в tray", self.hide)
        menu.addAction("Сообщение окна", self.show_notice)
        menu.addSeparator()
        menu.addAction("Выход", shutdown)
        self._compact_bar = QtWidgets.QToolBar("Окно", self)
        self._compact_bar.setMovable(False)
        self._compact_bar.setFloatable(False)
        self._compact_bar.setStyleSheet("QToolBar {background:#10171c;border:0;} QToolButton {color:#9bdee9; padding:2px 8px;}")
        self.addToolBar(self._compact_bar)
        self._compact_bar.addAction("Развернуть", lambda: self.set_compact(False))
        menu_button = QtWidgets.QToolButton(self)
        menu_button.setText("Меню")
        menu_button.setMenu(menu)
        menu_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._compact_bar.addWidget(menu_button)
        self._notice_action = self._compact_bar.addAction("Сообщение", self.show_notice)
        self._notice_action.setVisible(False)
        self.menuBar().hide()
        self._compact_bar.hide()
        self.statusBar().hide()
        self._apply_rectangle("compact")
        app = QtWidgets.QApplication.instance()
        app.screenAdded.connect(self._watch_screen)
        app.screenRemoved.connect(self._screen_removed)
        for screen in app.screens():
            self._watch_screen(screen)
        try:
            self.web_view = EdgeWebView(site_url, config_dir, self)
            self.setCentralWidget(self.web_view)
            self.web_view.loadFinished.connect(self._loaded)
            self.web_view.fatalError.connect(lambda message: QtCore.QTimer.singleShot(0, lambda: self._unavailable(message)))
            self.web_view.processFailed.connect(self._renderer_failed)
            self.web_view.speechFailed.connect(self._speech_failed)
            self.statusBar().showMessage("Открываю ИРУ…")
        except Exception:
            logging.getLogger("iru_agent").exception("[desktop] cannot create WebView2 host")
            self._unavailable("Не удалось запустить встроенный браузер. Проверьте зависимости приложения и Microsoft Edge WebView2 Runtime.")

    def _remember_geometry(self):
        if not self.isMinimized() and not self.isMaximized():
            r = self.geometry()
            width, height = r.width(), r.height()
            # Automatic growth must not overwrite the small return form.
            if self.is_compact and not self._surface_manual and (width > 640 or height > 480):
                width, height = 360, 400
            self._rectangles["compact" if self.is_compact else "expanded"] = [r.x(), r.y(), width, height]
            self._rectangles["manual_compact"] = self._surface_manual
            self._rectangles["always_on_top"] = self._always_on_top

    def _save_geometry(self):
        changing = self._changing_geometry
        self._changing_geometry = True
        try:
            self.clamp_to_screen()
            self._remember_geometry()
            save_window_state(self._preferences_path, self._rectangles)
        finally:
            self._changing_geometry = changing

    def _watch_screen(self, screen):
        if screen not in self._screen_connections:
            self._screen_connections.add(screen)
            screen.availableGeometryChanged.connect(self._schedule_clamp)
            screen.geometryChanged.connect(self._schedule_clamp)
        self._schedule_clamp()

    def _screen_removed(self, screen):
        self._screen_connections.discard(screen)
        self._schedule_clamp()

    def _schedule_clamp(self, *args):
        QtCore.QTimer.singleShot(0, self.clamp_to_screen)

    def clamp_to_screen(self):
        if self.isMinimized() or self.isMaximized():
            return
        app = QtWidgets.QApplication.instance()
        screen = app.screenAt(self.frameGeometry().center()) or app.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame, client = self.frameGeometry(), self.geometry()
        left = client.x() - frame.x()
        top = client.y() - frame.y()
        extra_w = max(0, frame.width() - client.width())
        extra_h = max(0, frame.height() - client.height())
        width = min(client.width(), max(320, area.width() - extra_w))
        height = min(client.height(), max(160, area.height() - extra_h))
        x = max(area.x(), min(frame.x(), area.right() + 1 - width - extra_w))
        y = max(area.y(), min(frame.y(), area.bottom() + 1 - height - extra_h))
        self.setGeometry(x + left, y + top, width, height)

    def _apply_rectangle(self, mode):
        saved = self._rectangles.get(mode)
        if saved:
            if mode == "compact" and not self._surface_manual and (
                    saved[2] > 640 or saved[3] > 480 or saved[2:] == [400, 280]):
                saved = [*saved[:2], 360, 400]
            self.setGeometry(*saved)
        else:
            size = (360, 400) if mode == "compact" else (1200, 800)
            self.resize(*size)
        self.clamp_to_screen()

    def set_compact(self, compact):
        if self.is_compact == compact:
            return
        self._surface_animation.stop()
        self._remember_geometry()
        self._changing_geometry = True
        try:
            if self.isMaximized() or self.isMinimized():
                self.showNormal()
            self.is_compact = compact
            self.menuBar().setVisible(not compact)
            self.statusBar().setVisible(not compact)
            self._compact_bar.hide()
            self._apply_rectangle("compact" if compact else "expanded")
            self._sync_compact_presentation()
        finally:
            self._changing_geometry = False
        self._save_geometry()
        self._schedule_clamp()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_save_timer") and not self._changing_geometry:
            self._save_timer.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange and hasattr(self, "_surface_animation"):
            self._surface_animation.stop()
        if (event.type() == QtCore.QEvent.Type.WindowStateChange
                and self.isMaximized() and getattr(self, "is_compact", False)
                and not getattr(self, "_changing_geometry", False)):
            # The title-bar maximize button remains available without a tray.
            QtCore.QTimer.singleShot(0, lambda: self.set_compact(False))

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_save_timer") and not self._changing_geometry:
            self._save_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_always_on_top()
        self._schedule_clamp()

    def _apply_always_on_top(self):
        if os.name == "nt":
            # Keep the existing HWND: changing Qt window flags recreates the
            # WebView2 parent window and can interrupt the conversation/voice.
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            if not user32.SetWindowPos(int(self.winId()), -1 if self._always_on_top else -2,
                                      0, 0, 0, 0, 0x0013):  # NOSIZE | NOMOVE | NOACTIVATE
                logging.getLogger("iru_agent").warning("[desktop] cannot change topmost: %s", ctypes.get_last_error())
                return False
        return True

    def set_always_on_top(self, enabled):
        previous = self._always_on_top
        self._always_on_top = bool(enabled)
        if not self._apply_always_on_top():
            self._always_on_top = previous
            with QtCore.QSignalBlocker(self.always_on_top_action):
                self.always_on_top_action.setChecked(previous)
            return
        self._save_geometry()

    def hideEvent(self, event):
        self._surface_animation.stop()
        super().hideEvent(event)

    def nativeEvent(self, event_type, message):
        if os.name == "nt":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0231:  # WM_ENTERSIZEMOVE, real title-bar interaction
                self._surface_animation.stop()
                self._surface_candidate = None
                self._interactive_size = self.size()
            elif msg.message == 0x0214 and self.is_compact:  # WM_SIZING, never programmatic resize
                self._surface_manual = True
            elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
                initial = self._interactive_size
                self._interactive_size = None
                if initial is not None:
                    QtCore.QTimer.singleShot(0, lambda: self._finish_manual_size(initial))
        return super().nativeEvent(event_type, message)

    def _finish_manual_size(self, initial):
        # Qt can deliver the final resize after WM_EXITSIZEMOVE.
        if self.is_compact and self.size() != initial:
            self._surface_manual = True
            self._save_geometry()

    def _resume_surface_size(self):
        self._surface_manual = False
        self._surface_candidate = None
        self.set_compact(True)
        self._measure_surface()

    def _apply_surface_size(self, size):
        changing = self._changing_geometry
        self._changing_geometry = True
        try:
            self.resize(size)
            self.clamp_to_screen()
        finally:
            self._changing_geometry = changing

    def _measure_surface(self):
        if (not self.is_compact or self._surface_manual or self._surface_measurement_pending
                or self._surface_animation.state() == QtCore.QAbstractAnimation.State.Running
                or self._interactive_size is not None or not self.isVisible()
                or self.isMinimized() or self.isMaximized() or self.web_view is None):
            return
        self._surface_measurement_pending = True

        def measured(value):
            self._surface_measurement_pending = False
            if (not self.is_compact or self._surface_manual or not self.isVisible()
                    or self._interactive_size is not None or self.isMinimized() or self.isMaximized()
                    or not isinstance(value, dict) or value.get("version") != 1):
                return
            height, viewport = value.get("height"), value.get("viewport")
            if (type(height) not in (int, float) or type(viewport) not in (int, float)
                    or not 1 <= height <= 100000 or not 1 <= viewport <= 100000
                    or type(value.get("hold")) is not bool):
                return
            screen = QtWidgets.QApplication.instance().screenAt(self.frameGeometry().center()) or self.screen()
            if screen is None:
                return
            area = screen.availableGeometry()
            # CSS px -> Qt logical px via the actual viewport, including browser zoom.
            scale = self.web_view.height() / viewport
            target = max(160, min(round(height * scale), max(160, int(area.height() * .8) - 40)))
            if abs(target - self.height()) < 8 or target < self.height() and value["hold"]:
                self._surface_candidate = None
                return
            urgent = value.get("urgent") is True
            if self._surface_candidate != target and not urgent:
                self._surface_candidate = target
                return  # two stable observations, no resize feedback loop
            size = QtCore.QSize(min(self.width(), max(320, int(area.width() * .85))), target)
            self._save_timer.stop()
            if urgent:
                # Confirmation/input controls must not wait for a visual transition.
                self._apply_surface_size(size)
            else:
                self._surface_animation.setStartValue(self.size())
                self._surface_animation.setEndValue(size)
                self._surface_animation.start()
            self._surface_candidate = None

        self.web_view.measure_surface(measured)

    def _set_notice(self, message):
        self._notice = message
        self.statusBar().showMessage(message)
        self._notice_action.setVisible(bool(message))

    def show_notice(self):
        # Explicit user action only; never activate the window on task results.
        QtWidgets.QMessageBox.information(self, "ИРУ", self._notice or "Сообщений окна нет.")

    def _unavailable(self, message):
        self.dispose_browser()
        panel = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(panel)
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        install = QtWidgets.QPushButton("Скачать Microsoft Edge WebView2 Runtime")
        install.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl("https://developer.microsoft.com/microsoft-edge/webview2/")))
        layout.addWidget(install)
        layout.addStretch()
        self.setCentralWidget(panel)
        self._set_notice(message)

    def show_iru(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.clamp_to_screen()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._surface_animation.stop()
        self._save_geometry()
        if self.tray_available:
            event.ignore()
            self.hide()
        else:
            event.accept()
            self.shutdown()

    def _reload(self):
        if self.web_view is not None:
            self.web_view.reload()

    def _loaded(self, success):
        if success:
            self._sync_compact_presentation()
            self._set_notice("")
        else:
            self._set_notice("Сайт недоступен. Проверьте сеть и выберите ИРУ → Обновить страницу.")

    def _sync_compact_presentation(self):
        if self.web_view is not None and self.web_view.site_origin == origin(self.web_view.current_url()):
            enabled = "true" if self.is_compact else "false"
            self.web_view.evaluate(
                f"if (typeof window.setAdaptiveSurface === 'function') window.setAdaptiveSurface({enabled}); "
                f"else document.documentElement.classList.toggle('iru-mini', {enabled})",
                lambda _result: None,
            )
            self._surface_timer.start()

    def _renderer_failed(self, kind):
        recovery = "Перезапустите ИРУ" if kind == "BrowserProcessExited" else "Выберите ИРУ → Обновить страницу"
        self._set_notice(f"Браузерный процесс остановился. {recovery}. Причина записана в лог агента.")

    def _speech_failed(self, code):
        reasons = {
            "network": "Нет связи с сервисом распознавания",
            "not-allowed": "Доступ к микрофону не разрешён",
            "audio-capture": "Не удалось получить звук с микрофона",
            "service-not-allowed": "Сервис распознавания отклонил доступ",
            "language-not-supported": "Язык распознавания не поддерживается",
        }
        reason = reasons.get(code, "Ошибка сервиса распознавания")
        self._set_notice(f"Голос: {reason} ({code if code in reasons else 'unknown'}). Подробности в логе агента.")

    def dispose_browser(self):
        self._surface_animation.stop()
        self._surface_timer.stop()
        self._save_timer.stop()
        self._save_geometry()
        if self.web_view is not None:
            try:
                self.web_view.dispose()
            except Exception:
                logging.getLogger("iru_agent").exception("[desktop] WebView2 cleanup failed")
