"""Real Qt/WebEngine checks in isolated processes; no production login/microphone."""
import importlib.util
import http.server
import threading
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def web_origin():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html>Storage test</html>"
            if self.path == "/voice":
                body = b"<script>window.marker='same-document';window.ticks=0;setInterval(()=>ticks++,20)</script>"
            if self.path == "/capture":
                body = b"<html><body>capture-marker<button id='mic' onclick=\"navigator.mediaDevices.getUserMedia({audio:true}).then(s=>{window.outcome='captured';s.getTracks().forEach(t=>t.stop())}).catch(e=>window.outcome=e.name)\">Mic</button></body></html>"
            if self.path == "/voice-adapter":
                body = (ROOT / "ui/js/voice.js").read_bytes()
            if self.path == "/speech":
                body = b"<html><head><script>window.initialSpeech=typeof(window.SpeechRecognition||window.webkitSpeechRecognition);window.constructed=false;if(window.SpeechRecognition||window.webkitSpeechRecognition){new (window.SpeechRecognition||window.webkitSpeechRecognition)();window.constructed=true}</script></head><body>speech-marker<button id='voiceBtn'>Voice</button><span id='voiceStatus'></span><button id='voiceStopSpeech'>Stop</button><script>window.createVoiceSession=()=>({enabled:false,disable(){},stopSpeech(){}})</script><script src='/voice-adapter'></script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript" if self.path == "/voice-adapter" else "text/html")
            if self.path == "/login":
                self.send_header("Set-Cookie", "desktop-session=ok; HttpOnly; Path=/")
            if self.path == "/session":
                body = self.headers.get("Cookie", "").encode("ascii")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_qt(code, tmp_path, web_origin="http://127.0.0.1:9"):

    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("PySide6/WebEngine required for desktop integration checks")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1",
               IRU_DESKTOP_TEST_DIR=str(tmp_path), IRU_DESKTOP_TEST_ORIGIN=web_origin)
    env.pop("IRU_WEB_URL", None)
    env["PYTHONPATH"] = str(ROOT / "agent") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run([sys.executable, "-B", "-c", textwrap.dedent(code)],
                               env=env, capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def test_desktop_close_reopen_and_explicit_exit_keep_runtime_independent(tmp_path):
    output = run_qt(r'''
        import logging, os
        from pathlib import Path
        from PySide6 import QtCore, QtWidgets
        from core.config import AgentPaths
        from core.state import AgentState, AgentSnapshot
        from ui.shell import launch_windows_shell
        from ui.webview import IruMainWindow
        app = QtWidgets.QApplication([])
        QtWidgets.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
        class Runtime:
            started = 0
            stopped = 0
            def start(self): self.started += 1
            def stop(self, wait=True):
                assert wait
                self.stopped += 1
            def request_reconnect(self): pass
        runtime = Runtime()
        root = Path(os.environ["IRU_DESKTOP_TEST_DIR"])
        paths = AgentPaths(root, root, root/"config.json", root/"legacy.json",
                           root, root/"agent.log", root/"missing.ico")
        state = AgentState(AgentSnapshot(version="test", device_id="demo", server_url="ws://127.0.0.1:9",
                                        config_path=str(paths.config_path), logs_dir=str(root), log_path=str(paths.log_path)))
        def check():
            main = next(w for w in app.topLevelWidgets() if isinstance(w, IruMainWindow))
            assert main.isVisible() and runtime.started == 1 and runtime.stopped == 0
            page = main.page
            main.close()
            assert not main.isVisible() and runtime.stopped == 0
            def reopen():
                assert main.page is page
                assert page.lifecycleState() == page.LifecycleState.Active
                assert page.isVisible()
                main.show_iru()
                assert main.isVisible() and runtime.started == 1
                menu = main.menuBar().actions()[0].menu()
                menu.actions()[0].trigger()
                settings = next(w for w in app.topLevelWidgets() if w.windowTitle() == "ИРУ — Настройки агента")
                assert settings.isVisible()
                settings.close()
                assert not settings.isVisible() and main.isVisible() and runtime.stopped == 0
                tray = next(o for o in app.children() if isinstance(o, QtWidgets.QSystemTrayIcon))
                labels = [a.text() for a in tray.contextMenu().actions()]
                assert all(s in labels for s in ["Открыть ИРУ", "Настройки агента", "Выход"])
                tray.contextMenu().actions()[-1].trigger()
                assert runtime.stopped == 1
                main.shutdown()
                assert runtime.stopped == 1
            QtCore.QTimer.singleShot(50, reopen)
        # Timer assertion failures must cause an observable non-success/timeout.
        QtCore.QTimer.singleShot(300, check)
        QtCore.QTimer.singleShot(10000, lambda: os._exit(4))
        assert launch_windows_shell(runtime, state, {"server_url":"ws://127.0.0.1:9"}, paths,
                                    logging.getLogger("test-desktop")) == 0
        assert runtime.started == 1 and runtime.stopped == 1
        print("desktop lifecycle passed")
    ''', tmp_path)
    assert "desktop lifecycle passed" in output


def test_webview_document_and_timers_survive_hide(tmp_path, web_origin):
    output = run_qt(r'''
        import os
        from pathlib import Path
        from PySide6 import QtCore, QtWidgets
        from ui.webview import IruMainWindow
        app = QtWidgets.QApplication([])
        origin = os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window = IruMainWindow(site_url=origin, config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                               icon=app.windowIcon(), tray_available=True, show_agent_settings=lambda:None, shutdown=app.quit)
        window.web_view.setUrl(QtCore.QUrl(origin + "/voice"))
        def loaded(ok):
            if not ok: return
            window.show()
            window.close()
            def observe():
                window.page.runJavaScript("JSON.stringify({marker,ticks,speech:typeof(window.SpeechRecognition||window.webkitSpeechRecognition)})", checked)
            QtCore.QTimer.singleShot(250, observe)
        def checked(value):
            import json
            result=json.loads(value)
            assert result["marker"] == "same-document" and result["ticks"] >= 2
            assert result["speech"] == "undefined"
            assert not window.isVisible()
            print("hidden document alive")
            app.quit()
        window.web_view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
    ''', tmp_path, web_origin)
    assert "hidden document alive" in output


def test_webview_storage_survives_process_restart(tmp_path, web_origin):
    shared = r'''
        import os
        from pathlib import Path
        from PySide6 import QtCore, QtWidgets
        from ui.webview import IruMainWindow
        app = QtWidgets.QApplication([])
        origin = os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window = IruMainWindow(site_url=origin,config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                               icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        assert not window.profile.isOffTheRecord()
        window.web_view.setUrl(QtCore.QUrl(origin + ("/login" if "setItem" in EXPRESSION else "/session")))
        def ready(ok):
            if not ok: return
            expression = EXPRESSION
            if "setItem" not in EXPRESSION:
                expression = "document.body.textContent.includes('desktop-session=ok') ? (" + EXPRESSION + ") : 'cookie-missing'"
            window.page.runJavaScript(expression, checked)
        def checked(value):
            assert value == "saved-session"
            print("storage passed")
            QtCore.QTimer.singleShot(300,app.quit)
        window.web_view.loadFinished.connect(ready)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
        profile=window.profile
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        profile.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    '''
    for expression in ["localStorage.setItem('test-session','saved-session');localStorage.getItem('test-session')",
                       "localStorage.getItem('test-session')"]:
        assert "storage passed" in run_qt("EXPRESSION=" + repr(expression) + "\n" + textwrap.dedent(shared),tmp_path,web_origin)


def test_url_navigation_permission_and_download_boundaries(tmp_path):
    assert "boundaries passed" in run_qt(r'''
        import os
        from pathlib import Path
        from PySide6 import QtCore, QtGui, QtWidgets, QtWebEngineCore
        from ui.webview import IruMainWindow, resolve_site_url
        assert resolve_site_url({"server_url":"wss://irumode.ru"}) == "https://irumode.ru/"
        assert resolve_site_url({"server_url":"ws://localhost:8000"}) == "http://localhost:8000/"
        for invalid in ["file:///private", "javascript:evil()", "https://secret@example.test/"]:
            os.environ["IRU_WEB_URL"]=invalid
            try: resolve_site_url({})
            except ValueError: pass
            else: raise AssertionError("invalid URL accepted")
        os.environ.pop("IRU_WEB_URL")
        app=QtWidgets.QApplication([])
        root=Path(os.environ["IRU_DESKTOP_TEST_DIR"])
        window=IruMainWindow(site_url="https://irumode.ru",config_dir=root,icon=app.windowIcon(),tray_available=False,
                             show_agent_settings=lambda:None,shutdown=lambda:exits.append(1))
        opened=[];exits=[]
        QtGui.QDesktopServices.openUrl=lambda url:opened.append(url.toString()) or True
        kind=window.page.NavigationType.NavigationTypeLinkClicked
        assert window.page.acceptNavigationRequest(QtCore.QUrl("https://irumode.ru/chat"),kind,True)
        assert not window.page.acceptNavigationRequest(QtCore.QUrl("https://outside.test"),kind,True)
        assert opened == ["https://outside.test"]
        assert not window.page.acceptNavigationRequest(QtCore.QUrl("file:///private"),kind,True)
        class Permission:
            def __init__(self,origin,valid=True):
                self.address=origin;self.valid=valid;self.granted=0;self.denied=0
            def origin(self):return QtCore.QUrl(self.address)
            def permissionType(self):return QtWebEngineCore.QWebEnginePermission.PermissionType.MediaAudioCapture
            def isValid(self):return self.valid
            def grant(self):self.granted+=1
            def deny(self):self.denied+=1
        denied=Permission("https://outside.test")
        window.page._request_permission(denied)
        assert denied.denied == 1 and not window.page._permission_dialogs
        granted=Permission("https://irumode.ru")
        window.page._request_permission(granted)
        assert granted.granted == 0 and len(window.page._permission_dialogs) == 1
        window.page._permission_dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes)
        assert granted.granted == 1 and not window.page._permission_dialogs
        invalid=Permission("https://irumode.ru",valid=False)
        window.page._request_permission(invalid)
        window.page._permission_dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes)
        assert invalid.granted == 0 and invalid.denied == 1
        before=window.page.url()
        window._renderer_terminated(window.page.RenderProcessTerminationStatus.CrashedTerminationStatus,17)
        assert "Обновить страницу" in window.statusBar().currentMessage() and window.page.url() == before
        QtWidgets.QFileDialog.getSaveFileName=lambda *args:("", "")
        class Download:
            cancelled=False
            def downloadFileName(self):return "test.docx"
            def cancel(self):self.cancelled=True
        download=Download();window._download(download);assert download.cancelled
        window.show();window.close();assert exits == [1]
        print("boundaries passed")
    ''',tmp_path)


@pytest.mark.parametrize("accepted", [True, False])
def test_real_microphone_permission_is_async_and_preserves_document(tmp_path, web_origin, monkeypatch, accepted):
    # Synthetic Chromium device: never capture the developer's actual microphone.
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --use-fake-device-for-media-stream")
    code = r'''
        import os,json
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/capture",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                             icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        window.show()
        permission_seen=[];permission_states=[];crashes=[]
        window.page.renderProcessTerminated.connect(lambda *args:crashes.append(args))
        def permitted(permission):
            permission_seen.append(permission.permissionType())
            assert len(window.page._permission_dialogs) == 1
            def answer():
                window.page._permission_dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes if ACCEPTED else QtWidgets.QMessageBox.StandardButton.No)
                permission_states.append(permission.state())
                QtCore.QTimer.singleShot(200,poll)
            QtCore.QTimer.singleShot(20,answer)
        window.page.permissionRequested.connect(permitted)
        def loaded(ok):
            assert ok
            window.page.runJavaScript("document.getElementById('mic').click()")
        window.web_view.loadFinished.connect(loaded)
        def observed(value):
            result=json.loads(value)
            if (ACCEPTED and not result.get("outcome")) or not permission_states:
                QtCore.QTimer.singleShot(50,poll);return
            assert len(permission_seen) == 1 and not crashes
            from PySide6.QtWebEngineCore import QWebEnginePermission
            assert permission_states == [QWebEnginePermission.State.Granted if ACCEPTED else QWebEnginePermission.State.Denied]
            if ACCEPTED:
                assert result["outcome"] == "captured"
            else:
                # Qt can report AbortError for a denied media request.
                assert result.get("outcome") in (None, "NotAllowedError", "AbortError")
            assert "capture-marker" in result["text"] and not window.page._permission_dialogs
            window.close()
            assert not window.isVisible()
            print("microphone permission passed")
            app.quit()
        def poll():
            window.page.runJavaScript("JSON.stringify({outcome:window.outcome,text:document.body.innerText})",observed)
        QtCore.QTimer.singleShot(100,poll)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
    '''
    assert "microphone permission passed" in run_qt("ACCEPTED="+repr(accepted)+"\n"+textwrap.dedent(code),tmp_path,web_origin)


def test_site_scripts_never_construct_unsupported_speech_recognizer(tmp_path, web_origin):
    assert "speech crash guarded" in run_qt(r'''
        import os,json
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/speech",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                             icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        crashes=[]
        window.page.renderProcessTerminated.connect(lambda *args:crashes.append(args))
        def loaded(ok):
            assert ok
            def observe():
                window.page.runJavaScript("JSON.stringify({speech:window.initialSpeech,constructed:window.constructed,disabled:document.getElementById('voiceBtn').disabled,enabled:window.iruVoice.enabled,text:document.body.innerText})",checked)
            QtCore.QTimer.singleShot(500,observe)
        def checked(value):
            result=json.loads(value)
            assert result["speech"] == "undefined" and result["constructed"] is False
            assert result["disabled"] is True and result["enabled"] is False
            assert "speech-marker" in result["text"] and not crashes
            assert "Голос недоступен" in window.statusBar().currentMessage()
            print("speech crash guarded")
            app.quit()
        window.web_view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
    ''',tmp_path,web_origin)


def test_speech_guard_does_not_modify_other_origins(tmp_path, web_origin):
    assert "speech origin guard passed" in run_qt(r'''
        import os,json
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url="https://iru.example.test",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                             icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        # Different origin: inspect the exposed API without instantiating it.
        window.web_view.setUrl(QtCore.QUrl(origin+"/voice"))
        def loaded(ok):
            if not ok:return
            window.page.runJavaScript("typeof(window.SpeechRecognition||window.webkitSpeechRecognition)",checked)
        def checked(value):
            assert value == "function"
            print("speech origin guard passed")
            app.quit()
        window.web_view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
    ''',tmp_path,web_origin)
