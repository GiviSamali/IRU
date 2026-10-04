"""The existing IRU website hosted in the agent's existing Qt application."""
from __future__ import annotations

import json
import logging
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
        self._permission_dialogs = []
        self.permissionRequested.connect(self._request_permission)
        self.newWindowRequested.connect(self._new_window)
        # Qt 6.11.2 exposes SpeechRecognition without its SpeechRecognizer binder:
        # constructing it kills the renderer. The prefixed API also failed to start
        # in our reproduction. Hide both unsupported APIs before site scripts run;
        # let the existing voice UI use its unsupported-browser state honestly.
        script = QtWebEngineCore.QWebEngineScript()
        script.setName("iru-unsupported-web-speech")
        script.setInjectionPoint(script.InjectionPoint.DocumentCreation)
        script.setWorldId(script.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode("""(() => {
            if (location.origin !== new URL(%s).origin) return;
            for (const name of ['SpeechRecognition', 'webkitSpeechRecognition']) {
                Object.defineProperty(window, name, {value: undefined, configurable: true, writable: true});
            }
        })();""" % json.dumps(site_url))
        self.scripts().insert(script)

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

    def _request_permission(self, permission):
        # Keep the Chromium event loop running while native UI asks for consent.
        if (_origin(permission.origin()) != self.site_origin
                or permission.permissionType() != QtWebEngineCore.QWebEnginePermission.PermissionType.MediaAudioCapture):
            permission.deny()
            return
        initial_url = self.url()
        dialog = QtWidgets.QMessageBox(self.parent())
        dialog.setWindowTitle("Микрофон ИРУ")
        dialog.setText("Разрешить сайту ИРУ доступ к микрофону?")
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._permission_dialogs.append(dialog)

        def resolved(answer):
            allowed = (answer == QtWidgets.QMessageBox.StandardButton.Yes
                       and self.url() == initial_url and permission.isValid())
            if allowed:
                permission.grant()
            else:
                permission.deny()
            logging.getLogger("iru_agent").info("[desktop] microphone permission=%s", "granted" if allowed else "denied")
            self._permission_dialogs.remove(dialog)
            dialog.deleteLater()

        dialog.finished.connect(resolved)
        dialog.open()


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
        self.page.renderProcessTerminated.connect(self._renderer_terminated)

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
            if _origin(self.page.url()) == self.page.site_origin:
                self.statusBar().showMessage(
                    "Голос недоступен в этой версии приложения. Для голоса: ИРУ → Открыть в браузере.")
            else:
                self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage("Сайт недоступен. Проверьте сеть и выберите ИРУ → Обновить страницу.")

    def _renderer_terminated(self, status, exit_code):
        if status == self.page.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        logging.getLogger("iru_agent").error("[desktop] renderer terminated status=%s code=%s", status.name, exit_code)
        self.statusBar().showMessage(
            "Браузерный процесс остановился. Выберите ИРУ → Обновить страницу. Причина записана в лог агента.")
        # No automatic reload: it would destroy voice state and could repeat actions.

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
