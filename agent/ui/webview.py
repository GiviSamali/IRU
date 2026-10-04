"""The existing IRU website hosted in the agent's existing Qt application."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6 import QtCore, QtGui, QtWidgets, QtWebEngineCore, QtWebEngineWidgets


def resolve_site_url(config: dict) -> str:
    raw = os.environ.get("IRU_WEB_URL", "").strip() or str(config.get("server_url") or "wss://irumode.ru")
    url = urlsplit(raw)
    scheme = {"wss": "https", "ws": "http"}.get(url.scheme, url.scheme)
    if scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
        raise ValueError("IRU WebView requires an HTTP(S) URL without credentials")
    return urlunsplit((scheme, url.netloc, url.path or "/", "", ""))


def _origin(url: QtCore.QUrl) -> tuple[str, str, int]:
    return url.scheme().lower(), url.host().lower(), url.port(443 if url.scheme() == "https" else 80)


class SitePage(QtWebEngineCore.QWebEnginePage):
    def __init__(self, profile, site_url, parent=None):
        super().__init__(profile, parent)
        self.site_origin = _origin(QtCore.QUrl(site_url))
        self.featurePermissionRequested.connect(self._request_permission)
        self.newWindowRequested.connect(self._new_window)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if is_main_frame and url.scheme() not in {"http", "https", "about"}:
            return False
        if (is_main_frame and navigation_type == self.NavigationType.NavigationTypeLinkClicked
                and url.scheme() in {"http", "https"} and _origin(url) != self.site_origin):
            QtGui.QDesktopServices.openUrl(url)
            return False
        return True

    def _new_window(self, request):
        url = request.requestedUrl()
        if not request.isUserInitiated() or url.scheme() not in {"http", "https"}:
            return
        if _origin(url) == self.site_origin:
            self.setUrl(url)
        else:
            QtGui.QDesktopServices.openUrl(url)

    def _request_permission(self, origin, feature):
        allow = False
        if _origin(origin) == self.site_origin and feature == self.Feature.MediaAudioCapture:
            allow = QtWidgets.QMessageBox.question(
                self.parent(), "Микрофон ИРУ", "Разрешить сайту ИРУ доступ к микрофону?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            ) == QtWidgets.QMessageBox.StandardButton.Yes
        self.setFeaturePermission(origin, feature, self.PermissionPolicy.PermissionGrantedByUser
                                  if allow else self.PermissionPolicy.PermissionDeniedByUser)


class IruMainWindow(QtWidgets.QMainWindow):
    def __init__(self, *, site_url, config_dir: Path, icon, tray_available,
                 show_agent_settings, shutdown):
        super().__init__()
        self.tray_available = tray_available
        self.shutdown = shutdown
        self.setWindowTitle("ИРУ")
        self.setWindowIcon(icon)
        self.resize(1200, 800)
        self.setMinimumSize(800, 560)

        # Named, on-disk profile: existing site cookies/localStorage survive exit.
        # Profile outlives page; release it only after the main window is deleted.
        profile_dir = config_dir / "webview"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile = QtWebEngineCore.QWebEngineProfile("IRU", QtWidgets.QApplication.instance())
        self.profile.setPersistentStoragePath(str(profile_dir / "storage"))
        self.profile.setCachePath(str(profile_dir / "cache"))
        self.profile.setPersistentCookiesPolicy(self.profile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.downloadRequested.connect(self._download)
        self.web_view = QtWebEngineWidgets.QWebEngineView(self)
        self.page = SitePage(self.profile, site_url, self.web_view)
        self.web_view.setPage(self.page)
        self.setCentralWidget(self.web_view)
        self.web_view.loadFinished.connect(self._loaded)

        menu = self.menuBar().addMenu("ИРУ")
        menu.addAction("Настройки агента", show_agent_settings)
        menu.addAction("Обновить страницу", self.web_view.reload)
        menu.addAction("Открыть в браузере", lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(site_url)))
        menu.addSeparator()
        menu.addAction("Выход", shutdown)
        self.web_view.setUrl(QtCore.QUrl(site_url))

    def show_iru(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.tray_available:
            event.ignore()
            self.hide()
        else:
            # No tray means no way back to a hidden window: quit explicitly.
            event.accept()
            self.shutdown()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Hide the native window, keep the SAME document and its voice loop alive.
        # No unload/navigation, mute, freeze or voice stop is tied to visibility.
        QtCore.QTimer.singleShot(0, self._keep_page_active)

    def _keep_page_active(self):
        self.page.setVisible(True)
        self.page.setLifecycleState(self.page.LifecycleState.Active)

    def _loaded(self, success):
        if success:
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage("Сайт недоступен. Проверьте сеть и выберите ИРУ → Обновить страницу.")

    def _download(self, download):
        filename = Path(download.downloadFileName().replace("\\", "/")).name or "download"
        destination, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить файл", filename)
        if not destination:
            download.cancel()
            return
        path = Path(destination)
        download.setDownloadDirectory(str(path.parent))
        download.setDownloadFileName(path.name)
        download.accept()
