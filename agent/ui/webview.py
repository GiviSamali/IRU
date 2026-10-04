"""IRU website in WebView2; existing Qt menu, settings and tray stay native."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6 import QtCore, QtGui, QtWidgets
from ui.edge import EdgeWebView


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
        self.resize(1200, 800)
        self.setMinimumSize(800, 560)

        menu = self.menuBar().addMenu("ИРУ")
        menu.addAction("Настройки агента", show_agent_settings)
        menu.addAction("Обновить страницу", self._reload)
        menu.addAction("Открыть в браузере", lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(site_url)))
        menu.addSeparator()
        menu.addAction("Выход", shutdown)
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
        self.statusBar().showMessage(message)

    def show_iru(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
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
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage("Сайт недоступен. Проверьте сеть и выберите ИРУ → Обновить страницу.")

    def _renderer_failed(self, kind):
        recovery = "Перезапустите ИРУ" if kind == "BrowserProcessExited" else "Выберите ИРУ → Обновить страницу"
        self.statusBar().showMessage(f"Браузерный процесс остановился. {recovery}. Причина записана в лог агента.")

    def _speech_failed(self, code):
        reasons = {
            "network": "Нет связи с сервисом распознавания",
            "not-allowed": "Доступ к микрофону не разрешён",
            "audio-capture": "Не удалось получить звук с микрофона",
            "service-not-allowed": "Сервис распознавания отклонил доступ",
            "language-not-supported": "Язык распознавания не поддерживается",
        }
        reason = reasons.get(code, "Ошибка сервиса распознавания")
        self.statusBar().showMessage(f"Голос: {reason} ({code}). Подробности в логе агента.")

    def dispose_browser(self):
        if self.web_view is not None:
            try:
                self.web_view.dispose()
            except Exception:
                logging.getLogger("iru_agent").exception("[desktop] WebView2 cleanup failed")
