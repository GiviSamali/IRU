"""Windows WebView2 hosted as a native child of the existing Qt desktop UI.

Qt owns the only UI message loop. WebView2/.NET callbacks stay on that same
STA thread. No pywebview JS bridge, agent credentials or privileged host objects
are exposed to the document; pywebview supplies the Microsoft SDK assemblies.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from PySide6 import QtCore, QtGui, QtWidgets

LOG = logging.getLogger("iru_agent")
BACKGROUND_ARGUMENTS = "--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows"


def origin(url: str):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _load_sdk():
    if sys.platform != "win32":
        raise RuntimeError("WebView2 requires Windows")
    # Balance this COM initialization when this control is disposed. Qt normally
    # already initialized this thread as STA; S_FALSE (1) is a valid success too.
    ole = ctypes.OleDLL("ole32")
    result = ole.CoInitializeEx(None, 2)
    if result < 0:
        raise RuntimeError("WebView2 requires an STA UI thread")
    dll_directory = None
    try:
        import clr
        spec = importlib.util.find_spec("webview")
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError("Install pywebview and pythonnet for WebView2")
        lib = Path(next(iter(spec.submodule_search_locations))) / "lib"
        architecture = "win-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else ("win-x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "win-x86")
        dll_directory = os.add_dll_directory(str(lib / "runtimes" / architecture / "native"))
        # Explicit DLL load also works on Windows/.NET versions that ignore
        # AddDllDirectory when resolving a P/Invoke dependency.
        loader = ctypes.WinDLL(str(lib / "runtimes" / architecture / "native" / "WebView2Loader.dll"))
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        clr.AddReference(str(lib / "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference(str(lib / "Microsoft.Web.WebView2.WinForms.dll"))
        from System.Windows.Forms import Panel, DockStyle, WindowsFormsSynchronizationContext
        from System.Threading import SynchronizationContext
        from Microsoft.Web.WebView2.WinForms import WebView2, CoreWebView2CreationProperties
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState
        SynchronizationContext.SetSynchronizationContext(WindowsFormsSynchronizationContext())
        return SimpleNamespace(Panel=Panel, DockStyle=DockStyle, WebView2=WebView2,
                               Properties=CoreWebView2CreationProperties,
                               PermissionKind=CoreWebView2PermissionKind,
                               PermissionState=CoreWebView2PermissionState,
                               ole=ole, dll_directory=dll_directory, loader=loader)
    except Exception:
        if dll_directory is not None:
            dll_directory.close()
        ole.CoUninitialize()
        raise


class EdgeWebView(QtWidgets.QWidget):
    loadFinished = QtCore.Signal(bool)
    fatalError = QtCore.Signal(str)
    processFailed = QtCore.Signal(str)
    ready = QtCore.Signal()

    def __init__(self, site_url: str, config_dir: Path, parent=None):
        super().__init__(parent)
        self.site_url = site_url
        self.site_origin = origin(site_url)
        self.profile_path = config_dir / "webview2"
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.core = None
        self._disposed = False
        self._dialogs = []
        self._microphone_allowed = False
        self._sdk = _load_sdk()
        try:
            self._panel = self._sdk.Panel()
            self._panel.CreateControl()
            self._hwnd = self._panel.Handle.ToInt64()
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow)
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
            self._user32.SetParent.restype = wintypes.HWND
            self._user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            ctypes.set_last_error(0)
            self._user32.SetParent(self._hwnd, int(self.winId()))
            if ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            self._browser = self._sdk.WebView2()
            props = self._sdk.Properties()
            props.UserDataFolder = str(self.profile_path)
            props.IsInPrivateModeEnabled = False
            props.AdditionalBrowserArguments = BACKGROUND_ARGUMENTS
            self._browser.CreationProperties = props
            self._panel.Controls.Add(self._browser)
            self._browser.Dock = self._sdk.DockStyle.Fill
            self._browser.CoreWebView2InitializationCompleted += self._initialized
            self._resize_native()
            # Start only after the window connected its error signals.
            QtCore.QTimer.singleShot(0, self._initialize)
        except Exception:
            self.dispose()
            raise

    def _initialize(self):
        if self._disposed:
            return
        try:
            self._browser.EnsureCoreWebView2Async(None)
            QtCore.QTimer.singleShot(30000, self._initialization_deadline)
        except Exception:
            LOG.exception("[desktop] WebView2 initialization failed")
            self.fatalError.emit("Не удалось запустить WebView2. Проверьте Microsoft Edge WebView2 Runtime.")

    def _initialization_deadline(self):
        if not self._disposed and self.core is None:
            self.fatalError.emit("WebView2 не ответил при запуске. Перезапустите ИРУ и проверьте WebView2 Runtime.")

    def _initialized(self, sender, args):
        if self._disposed:
            return
        if not args.IsSuccess:
            LOG.error("[desktop] WebView2 initialization failed: %s", args.InitializationException)
            self.fatalError.emit("Не удалось запустить WebView2. Установите или восстановите Microsoft Edge WebView2 Runtime.")
            return
        self.core = sender.CoreWebView2
        settings = self.core.Settings
        settings.IsWebMessageEnabled = False
        settings.AreHostObjectsAllowed = False
        settings.AreDevToolsEnabled = False
        settings.IsStatusBarEnabled = False
        settings.AreDefaultScriptDialogsEnabled = True
        self.core.NavigationStarting += self._navigation_starting
        self.core.NavigationCompleted += self._navigation_completed
        self.core.NewWindowRequested += self._new_window
        self.core.PermissionRequested += self._permission
        self.core.DownloadStarting += self._download
        self.core.ProcessFailed += self._process_failed
        LOG.info("[desktop] engine=WebView2 runtime=%s", self.core.Environment.BrowserVersionString)
        self._resize_native()
        self.ready.emit()
        self.core.Navigate(self.site_url)

    def _navigation_starting(self, sender, args):
        address = str(args.Uri)
        destination = origin(address)
        if destination is None:
            args.Cancel = True
        elif args.IsUserInitiated and destination != self.site_origin:
            args.Cancel = True
            QtCore.QTimer.singleShot(0, lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(address)))

    def _navigation_completed(self, sender, args):
        if not args.IsSuccess:
            LOG.warning("[desktop] WebView2 navigation failed status=%s", args.WebErrorStatus)
        self.loadFinished.emit(bool(args.IsSuccess))

    def _new_window(self, sender, args):
        args.Handled = True
        address = str(args.Uri)
        if not args.IsUserInitiated or origin(address) is None:
            return
        if origin(address) == self.site_origin:
            QtCore.QTimer.singleShot(0, lambda: self.navigate(address))
        else:
            QtCore.QTimer.singleShot(0, lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(address)))

    def _permission(self, sender, args):
        args.State = self._sdk.PermissionState.Deny
        args.SavesInProfile = False
        if (args.PermissionKind != self._sdk.PermissionKind.Microphone
                or origin(str(args.Uri)) != self.site_origin or origin(self.current_url()) != self.site_origin):
            return
        # SpeechRecognition restarts frequently. Remember explicit consent for
        # this application session only, always behind both origin checks above.
        if self._microphone_allowed:
            args.State = self._sdk.PermissionState.Allow
            return
        deferral = args.GetDeferral()
        initial_url = self.current_url()
        dialog = QtWidgets.QMessageBox(self.window())
        dialog.setWindowTitle("Микрофон ИРУ")
        dialog.setText("Разрешить сайту ИРУ доступ к микрофону?")
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._dialogs.append(dialog)

        def resolved(answer):
            try:
                allowed = (answer == QtWidgets.QMessageBox.StandardButton.Yes and not self._disposed
                           and self.current_url() == initial_url)
                self._microphone_allowed = allowed
                args.State = self._sdk.PermissionState.Allow if allowed else self._sdk.PermissionState.Deny
                LOG.info("[desktop] microphone permission=%s", "granted" if allowed else "denied")
            finally:
                deferral.Complete()
                self._dialogs.remove(dialog)
                dialog.deleteLater()
        dialog.finished.connect(resolved)
        dialog.open()

    def _download(self, sender, args):
        args.Cancel = True
        args.Handled = True
        deferral = args.GetDeferral()
        dialog = QtWidgets.QFileDialog(self.window(), "Сохранить файл")
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.AnyFile)
        filename = Path(str(args.ResultFilePath).replace("\\", "/")).name or "download"
        dialog.selectFile(filename)
        # Standard save dialog includes overwrite confirmation; never bypass it.
        self._dialogs.append(dialog)

        def resolved(answer):
            try:
                if answer == QtWidgets.QDialog.DialogCode.Accepted and not self._disposed:
                    files = dialog.selectedFiles()
                    if files:
                        args.ResultFilePath = str(Path(files[0]))
                        args.Cancel = False
            finally:
                deferral.Complete()
                self._dialogs.remove(dialog)
                dialog.deleteLater()
        dialog.finished.connect(resolved)
        dialog.open()

    def _process_failed(self, sender, args):
        kind = str(args.ProcessFailedKind)
        LOG.error("[desktop] WebView2 process failed kind=%s", kind)
        self.processFailed.emit(kind)
        # Keep task state: don't reload or replay requests automatically.

    def current_url(self):
        return str(self.core.Source) if self.core is not None else ""

    def navigate(self, address):
        if not self._disposed and self.core is not None and origin(address):
            self.core.Navigate(address)

    def reload(self):
        if not self._disposed and self.core is not None:
            self.core.Reload()

    def evaluate(self, script, callback, timeout=10):
        """Non-blocking native diagnostic/test API; never exposed to the site."""
        if self._disposed or self.core is None:
            callback(None)
            return
        task = self.core.ExecuteScriptAsync(script)
        deadline = time.monotonic() + timeout

        def poll():
            if self._disposed:
                callback(None)
            elif not task.IsCompleted:
                if time.monotonic() >= deadline:
                    callback(None)
                else:
                    QtCore.QTimer.singleShot(20, poll)
            elif task.IsFaulted or task.IsCanceled:
                callback(None)
            else:
                callback(json.loads(str(task.Result)))
        poll()

    def _resize_native(self):
        if self._disposed:
            return
        rectangle = wintypes.RECT()
        if self._user32.GetClientRect(int(self.winId()), ctypes.byref(rectangle)):
            # Resize through WinForms, not only the HWND. SetWindowPos leaves
            # managed bounds/layout stale and Dock=Fill at its default 100x30.
            # GetClientRect provides physical pixels even on scaled Qt screens.
            self._panel.SetBounds(0, 0, max(1, rectangle.right - rectangle.left),
                                  max(1, rectangle.bottom - rectangle.top))
            self._panel.PerformLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_user32"):
            # Qt delivers resizeEvent before its native HWND gets new bounds.
            QtCore.QTimer.singleShot(0, self._resize_native)

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._resize_native)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self._disposed:
            self._browser.Focus()

    def dispose(self):
        if self._disposed:
            return
        # Finish outstanding deferrals BEFORE disposing their COM objects.
        for dialog in list(self._dialogs):
            dialog.reject()
        self._disposed = True
        self._microphone_allowed = False
        self.core = None
        try:
            if hasattr(self, "_browser"):
                self._browser.Dispose()
        finally:
            try:
                if hasattr(self, "_panel"):
                    self._panel.Dispose()
            finally:
                self._sdk.dll_directory.close()
                self._sdk.ole.CoUninitialize()
