"""IRU website in WebView2; existing Qt menu, settings and tray stay native."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6 import QtCore, QtGui, QtWidgets
from ui.edge import EdgeWebView
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
        self.is_compact = True
        self._changing_geometry = False
        self._notice = ""
        self._screen_connections = set()
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_geometry)
        self.setMinimumSize(320, 240)
        self.resize(400, 280)

        menu = self.menuBar().addMenu("ИРУ")
        menu.addAction("Настройки агента", show_agent_settings)
        menu.addAction("Обновить страницу", self._reload)
        menu.addAction("Открыть в браузере", lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(site_url)))
        menu.addAction("Компактное окно", lambda: self.set_compact(True))
        menu.addAction("Развернуть окно", lambda: self.set_compact(False))
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
            # A manually enlarged widget must still have a small return form.
            if self.is_compact and (width > 640 or height > 480):
                width, height = 400, 280
            self._rectangles["compact" if self.is_compact else "expanded"] = [r.x(), r.y(), width, height]

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
        height = min(client.height(), max(240, area.height() - extra_h))
        x = max(area.x(), min(frame.x(), area.right() + 1 - width - extra_w))
        y = max(area.y(), min(frame.y(), area.bottom() + 1 - height - extra_h))
        self.setGeometry(x + left, y + top, width, height)

    def _apply_rectangle(self, mode):
        saved = self._rectangles.get(mode)
        if saved:
            if mode == "compact" and (saved[2] > 640 or saved[3] > 480):
                saved = [*saved[:2], 400, 280]
            self.setGeometry(*saved)
        else:
            size = (400, 280) if mode == "compact" else (1200, 800)
            self.resize(*size)
        self.clamp_to_screen()

    def set_compact(self, compact):
        if self.is_compact == compact:
            return
        self._remember_geometry()
        self._changing_geometry = True
        try:
            if self.isMaximized() or self.isMinimized():
                self.showNormal()
            self.is_compact = compact
            self.menuBar().setVisible(not compact)
            self.statusBar().setVisible(not compact)
            self._compact_bar.setVisible(compact)
            self._apply_rectangle("compact" if compact else "expanded")
        finally:
            self._changing_geometry = False
        self._save_geometry()
        self._schedule_clamp()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_save_timer") and not self._changing_geometry:
            self._save_timer.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_save_timer") and not self._changing_geometry:
            self._save_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_clamp()

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
            self._set_notice("")
        else:
            self._set_notice("Сайт недоступен. Проверьте сеть и выберите ИРУ → Обновить страницу.")

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
        self._save_timer.stop()
        self._save_geometry()
        if self.web_view is not None:
            try:
                self.web_view.dispose()
            except Exception:
                logging.getLogger("iru_agent").exception("[desktop] WebView2 cleanup failed")
