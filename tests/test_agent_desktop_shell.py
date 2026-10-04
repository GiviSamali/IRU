"""Real Qt/WebView2 checks in isolated processes; no production login/microphone."""
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
            if self.path == "/download":
                body = bytes(range(256))*4
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript" if self.path == "/voice-adapter" else "text/html")
            if self.path == "/download":
                self.send_header("Content-Disposition", 'attachment; filename="binary.dat"')
            if self.path == "/login":
                self.send_header("Set-Cookie", "desktop-session=ok; HttpOnly; Path=/; Max-Age=86400")
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

    if sys.platform != "win32":
        pytest.skip("WebView2 desktop checks require Windows")
    for package in ("PySide6", "clr", "webview"):
        if importlib.util.find_spec(package) is None:
            pytest.skip("WebView2 desktop checks require PySide6, pywebview and pythonnet")
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONDONTWRITEBYTECODE="1",
               IRU_DESKTOP_TEST_DIR=str(tmp_path), IRU_DESKTOP_TEST_ORIGIN=web_origin)
    env.pop("IRU_WEB_URL", None)
    env["PYTHONPATH"] = str(ROOT / "agent") + os.pathsep + env.get("PYTHONPATH", "")
    # WebView2 needs a real HWND, unlike Qt's synthetic offscreen platform.
    # Keep test windows outside the visible desktop; use no production account.
    prefix = """
from ui.webview import IruMainWindow
from PySide6 import QtWidgets
_original_init = IruMainWindow.__init__
def isolated_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    self.move(-3000, -3000)
IruMainWindow.__init__ = isolated_init
_original_show_normal = QtWidgets.QWidget.showNormal
def isolated_show_normal(self):
    self.move(-3000, -3000)
    _original_show_normal(self)
QtWidgets.QWidget.showNormal = isolated_show_normal
"""
    completed = subprocess.run([sys.executable, "-B", "-c", prefix + textwrap.dedent(code)],
                               env=env, capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout + completed.stderr


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
            page = main.web_view
            main.close()
            assert not main.isVisible() and runtime.stopped == 0
            def reopen():
                assert main.web_view is page
                assert not page._disposed
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
        window = IruMainWindow(site_url=origin + "/voice", config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                               icon=app.windowIcon(), tray_available=True, show_agent_settings=lambda:None, shutdown=app.quit)
        def loaded(ok):
            if not ok: return
            window.show()
            window.close()
            def observe():
                window.web_view.evaluate("JSON.stringify({marker,ticks,speech:typeof(window.SpeechRecognition||window.webkitSpeechRecognition)})", checked)
            QtCore.QTimer.singleShot(250, observe)
        def checked(value):
            import json
            result=json.loads(value)
            assert result["marker"] == "same-document" and result["ticks"] >= 2
            assert result["speech"] == "function"
            assert not window.isVisible()
            window.dispose_browser()
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
        window = IruMainWindow(site_url=origin + ("/login" if "setItem" in EXPRESSION else "/session"),config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
                               icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        assert window.web_view.profile_path == Path(os.environ["IRU_DESKTOP_TEST_DIR"])/"webview2"
        def ready(ok):
            if not ok: return
            expression = EXPRESSION
            if "setItem" not in EXPRESSION:
                expression = "document.body.textContent.includes('desktop-session=ok') ? (" + EXPRESSION + ") : 'cookie-missing'"
            window.web_view.evaluate(expression, checked)
        def checked(value):
            assert value == "saved-session"
            print("storage passed")
            QtCore.QTimer.singleShot(300,app.quit)
        window.web_view.loadFinished.connect(ready)
        QtCore.QTimer.singleShot(10000,lambda:os._exit(4))
        app.exec()
        window.dispose_browser()
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
    '''
    for expression in ["localStorage.setItem('test-session','saved-session');localStorage.getItem('test-session')",
                       "localStorage.getItem('test-session')"]:
        assert "storage passed" in run_qt("EXPRESSION=" + repr(expression) + "\n" + textwrap.dedent(shared),tmp_path,web_origin)



def test_url_navigation_permission_and_download_boundaries(tmp_path):
    assert "boundaries passed" in run_qt(r'''
        import os
        from pathlib import Path
        from types import SimpleNamespace
        from PySide6 import QtCore,QtGui,QtWidgets
        from ui.webview import IruMainWindow,resolve_site_url
        from ui.edge import origin
        assert resolve_site_url({"server_url":"wss://irumode.ru"}) == "https://irumode.ru/"
        assert resolve_site_url({"server_url":"ws://localhost:8000"}) == "http://localhost:8000/"
        for invalid in ["file:///private", "javascript:evil()", "https://secret@example.test/"]:
            os.environ["IRU_WEB_URL"]=invalid
            try:resolve_site_url({})
            except ValueError:pass
            else:raise AssertionError("invalid URL accepted")
        os.environ.pop("IRU_WEB_URL")
        assert origin("https://iru.example:443/path") == origin("https://iru.example")
        assert origin("https://iru.example:444") != origin("https://iru.example")
        app=QtWidgets.QApplication([])
        window=IruMainWindow(site_url="https://iru.example",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=False,show_agent_settings=lambda:None,shutdown=lambda:exits.append(1))
        assert window.web_view is not None
        view=window.web_view
        exits=[];opened=[]
        QtGui.QDesktopServices.openUrl=lambda url:opened.append(url.toString()) or True
        args=SimpleNamespace(Uri="file:///private",Cancel=False,IsUserInitiated=True)
        view._navigation_starting(None,args);assert args.Cancel
        args=SimpleNamespace(Uri="https://outside.test",Cancel=False,IsUserInitiated=True)
        view._navigation_starting(None,args);assert args.Cancel
        app.processEvents();assert opened == ["https://outside.test"]
        args=SimpleNamespace(Uri="https://iru.example/chat",Cancel=False,IsUserInitiated=True)
        view._navigation_starting(None,args);assert not args.Cancel
        pop=SimpleNamespace(Uri="https://outside.test",IsUserInitiated=False,Handled=False)
        view._new_window(None,pop);app.processEvents();assert pop.Handled and len(opened)==1
        class Deferral:
            completed=0
            def Complete(self):self.completed+=1
        class Permission:
            def __init__(self,uri,kind):
                self.Uri=uri;self.PermissionKind=kind;self.deferral=Deferral()
            def GetDeferral(self):return self.deferral
        view.current_url=lambda:"https://iru.example"
        denied=Permission("https://outside.test",view._sdk.PermissionKind.Microphone)
        view._permission(None,denied)
        assert denied.State == view._sdk.PermissionState.Deny and not view._dialogs
        camera=Permission("https://iru.example",view._sdk.PermissionKind.Camera)
        view._permission(None,camera);assert camera.State == view._sdk.PermissionState.Deny
        permitted=Permission("https://iru.example",view._sdk.PermissionKind.Microphone)
        view._permission(None,permitted)
        assert permitted.deferral.completed == 0
        view._dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes)
        assert permitted.State == view._sdk.PermissionState.Allow and permitted.deferral.completed==1
        repeated=Permission("https://iru.example",view._sdk.PermissionKind.Microphone)
        view._permission(None,repeated)
        assert repeated.State == view._sdk.PermissionState.Allow and not view._dialogs
        assert repeated.deferral.completed == 0 and not repeated.SavesInProfile
        foreign=Permission("https://outside.test",view._sdk.PermissionKind.Microphone)
        view._permission(None,foreign)
        assert foreign.State == view._sdk.PermissionState.Deny and not view._dialogs
        view._microphone_allowed=False
        moved=Permission("https://iru.example",view._sdk.PermissionKind.Microphone)
        view._permission(None,moved);view.current_url=lambda:"https://outside.test"
        view._dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes)
        assert moved.State == view._sdk.PermissionState.Deny and moved.deferral.completed==1
        download=SimpleNamespace(ResultFilePath="C:\\private\\file.docx",GetDeferral=lambda:Deferral())
        view._download(None,download);view._dialogs[0].reject()
        assert download.Cancel and download.Handled and not view._dialogs
        window._renderer_failed("RenderProcessExited")
        assert "Обновить страницу" in window.statusBar().currentMessage()
        window._renderer_failed("BrowserProcessExited")
        assert "Перезапустите ИРУ" in window.statusBar().currentMessage()
        window.show();window.close();assert exits==[1]
        view.dispose();view.dispose();assert view._disposed
        print("boundaries passed")
    ''',tmp_path)


@pytest.mark.parametrize("accepted", [True,False])
def test_real_microphone_permission_is_async_and_preserves_document(tmp_path,web_origin,monkeypatch,accepted):
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS","--use-fake-device-for-media-stream")
    code=r'''
        import os,json
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/capture",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        view=window.web_view;assert view is not None
        window.show();errors=[];capture_count=[]
        view.processFailed.connect(errors.append)
        def respond():
            if not view._dialogs:QtCore.QTimer.singleShot(30,respond);return
            view._dialogs[0].done(QtWidgets.QMessageBox.StandardButton.Yes if ACCEPTED else QtWidgets.QMessageBox.StandardButton.No)
        def loaded(ok):
            assert ok
            view.evaluate("document.getElementById('mic').click()",lambda _:None)
            respond()
            QtCore.QTimer.singleShot(100,poll)
        def poll():
            view.evaluate("JSON.stringify({outcome:window.outcome,text:document.body.innerText})",observed)
        def observed(value):
            result=json.loads(value)
            if not result.get("outcome"):QtCore.QTimer.singleShot(50,poll);return
            assert result["outcome"] == ("captured" if ACCEPTED else "NotAllowedError")
            assert "capture-marker" in result["text"] and not errors and not view._dialogs
            if ACCEPTED and not capture_count:
                capture_count.append(1)
                # A second real capture must reuse session consent; no new dialog.
                view.evaluate("window.outcome=null;document.getElementById('mic').click()",lambda _:None)
                QtCore.QTimer.singleShot(50,poll)
                return
            assert not ACCEPTED or capture_count==[1]
            window.close();assert not window.isVisible()
            window.dispose_browser()
            print("microphone permission passed")
            app.quit()
        view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(12000,lambda:os._exit(4))
        app.exec()
    '''
    assert "microphone permission passed" in run_qt("ACCEPTED="+repr(accepted)+"\n"+textwrap.dedent(code),tmp_path,web_origin)


def test_webview2_leaves_site_speech_recognition_enabled(tmp_path,web_origin):
    assert "speech API enabled" in run_qt(r'''
        import os,json
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/speech",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        view=window.web_view;assert view is not None
        errors=[];view.processFailed.connect(errors.append)
        def loaded(ok):
            assert ok
            view.evaluate("JSON.stringify({speech:initialSpeech,constructed,disabled:document.getElementById('voiceBtn').disabled,text:document.body.innerText})",checked)
        def checked(value):
            result=json.loads(value)
            assert result["speech"]=="function" and result["constructed"] and not result["disabled"]
            assert "speech-marker" in result["text"] and not errors
            assert not view.core.Settings.AreHostObjectsAllowed and not view.core.Settings.IsWebMessageEnabled
            assert view.core.Environment.BrowserVersionString
            arguments=str(view._browser.CreationProperties.AdditionalBrowserArguments)
            assert "--disable-features=msSpeechRecognitionServiceUseCetoService" in arguments
            assert "--disable-background-timer-throttling" in arguments
            assert "--no-sandbox" not in arguments and "--disable-web-security" not in arguments
            window.dispose_browser();print("speech API enabled");app.quit()
        view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(12000,lambda:os._exit(4))
        app.exec()
    ''',tmp_path,web_origin)


def test_missing_sdk_keeps_menu_and_gives_runtime_install_message(tmp_path):
    assert "missing SDK handled" in run_qt(r'''
        import os
        from pathlib import Path
        from PySide6 import QtWidgets
        import ui.webview as module
        app=QtWidgets.QApplication([])
        def unavailable(*a,**kw):raise RuntimeError("synthetic missing SDK")
        module.EdgeWebView=unavailable
        settings=[];exits=[]
        window=module.IruMainWindow(site_url="https://iru.example",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:settings.append(1),shutdown=lambda:exits.append(1))
        assert "WebView2 Runtime" in window.statusBar().currentMessage()
        assert window.web_view is None
        menu=window.menuBar().actions()[0].menu()
        menu.actions()[0].trigger();assert settings==[1]
        menu.actions()[-1].trigger();assert exits==[1]
        window.dispose_browser()
        print("missing SDK handled")
    ''',tmp_path)


@pytest.mark.parametrize("accepted",[True,False])
def test_native_download_save_and_cancel(tmp_path,web_origin,accepted):
    code=r'''
        import os
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        root=Path(os.environ["IRU_DESKTOP_TEST_DIR"])
        window=IruMainWindow(site_url=origin+"/voice",config_dir=root,icon=app.windowIcon(),
            tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        window.show();view=window.web_view;assert view is not None
        target=root/"saved.dat";responded=[]
        def respond():
            if not view._dialogs:QtCore.QTimer.singleShot(30,respond);return
            dialog=view._dialogs[0]
            assert isinstance(dialog,QtWidgets.QFileDialog)
            # Use the Qt dialog for deterministic synthetic interaction in CI.
            dialog.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog,True)
            dialog.selectFile(str(target))
            dialog.done(QtWidgets.QDialog.DialogCode.Accepted if ACCEPTED else QtWidgets.QDialog.DialogCode.Rejected)
            responded.append(True)
            QtCore.QTimer.singleShot(100,check)
        def loaded(ok):
            if not ok:return
            view.navigate(origin+"/download");respond()
        def check():
            if ACCEPTED and not target.exists():QtCore.QTimer.singleShot(50,check);return
            if ACCEPTED:
                assert target.read_bytes()==bytes(range(256))*4
            else:assert not target.exists()
            assert responded and not view._dialogs
            window.dispose_browser();print("native download passed");app.quit()
        view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(12000,lambda:os._exit(4));app.exec()
    '''
    assert "native download passed" in run_qt("ACCEPTED="+repr(accepted)+"\n"+textwrap.dedent(code),tmp_path,web_origin)


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5"])
def test_webview_viewport_fills_native_host_on_start_resize_and_reopen(tmp_path,web_origin,monkeypatch,scale):
    monkeypatch.setenv("QT_SCALE_FACTOR",scale)
    assert "viewport fills native host" in run_qt(r'''
        import ctypes,json,os,time
        from ctypes import wintypes
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/voice",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        window.show();view=window.web_view;assert view is not None
        observations=[];deadline=[time.monotonic()+6]
        def observe():
            view.evaluate("JSON.stringify({w:innerWidth,h:innerHeight,dpr:devicePixelRatio,marker,ticks})",checked)
        def checked(value):
            result=json.loads(value)
            rectangle=wintypes.RECT()
            assert view._user32.GetClientRect(int(view.winId()),ctypes.byref(rectangle))
            width=rectangle.right-rectangle.left;height=rectangle.bottom-rectangle.top
            assert width>=800 and height>=400
            # WebView2 applies compositor/renderer resize asynchronously.
            # Wait for actual geometry, keeping a bounded failure deadline.
            if (abs(result["w"]*result["dpr"]-width)>3
                    or abs(result["h"]*result["dpr"]-height)>3):
                assert time.monotonic()<deadline[0],(result,width,height)
                QtCore.QTimer.singleShot(40,observe)
                return
            assert view._panel.ClientSize.Width==width and view._panel.ClientSize.Height==height
            assert view._browser.Width==width and view._browser.Height==height
            assert result["marker"]=="same-document"
            if observations:assert result["ticks"]>observations[-1]
            observations.append(result["ticks"])
            deadline[0]=time.monotonic()+3
            if len(observations)==1:
                window.resize(900,640)
            elif len(observations)==2:
                window.resize(1440,900)
            elif len(observations)==3:
                window.close();assert not window.isVisible()
                window.show_iru()
                assert window.web_view is view and window.isVisible()
            else:
                window.dispose_browser();print("viewport fills native host");app.quit();return
            QtCore.QTimer.singleShot(150,observe)
        view.loadFinished.connect(lambda ok:QtCore.QTimer.singleShot(150,observe) if ok else None)
        QtCore.QTimer.singleShot(18000,lambda:os._exit(4));app.exec()
    ''',tmp_path,web_origin)


def test_native_speech_error_diagnostics_preserve_recognizer_and_hide_other_console_data(tmp_path,web_origin):
    output=run_qt(r'''
        import os,logging
        from pathlib import Path
        from PySide6 import QtCore,QtWidgets
        from ui.webview import IruMainWindow
        logging.basicConfig(level=logging.WARNING)
        app=QtWidgets.QApplication([])
        origin=os.environ["IRU_DESKTOP_TEST_ORIGIN"]
        window=IruMainWindow(site_url=origin+"/voice",config_dir=Path(os.environ["IRU_DESKTOP_TEST_DIR"]),
            icon=app.windowIcon(),tray_available=True,show_agent_settings=lambda:None,shutdown=app.quit)
        window.show();view=window.web_view;assert view is not None
        errors=[];view.speechFailed.connect(errors.append)
        def loaded(ok):
            assert ok
            view.evaluate("""(() => {
                console.warn('PRIVATE_CONSOLE_FIXTURE_DO_NOT_LOG');
                const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
                const rec = new SR();
                window.recognizerPreserved = rec instanceof SR && rec.lang === '';
                window.siteErrorCount = 0;
                rec.onerror = () => window.siteErrorCount++;
                for (const code of ['no-speech','aborted','network']) {
                    const event = new Event('error');
                    Object.defineProperty(event,'error',{value:code});
                    rec.dispatchEvent(event);
                }
                return window.recognizerPreserved && window.siteErrorCount === 3;
            })()""",checked)
        def checked(value):
            assert value is True
            def observe():
                if not errors:QtCore.QTimer.singleShot(40,observe);return
                assert errors==['network']
                assert 'network' in window.statusBar().currentMessage()
                assert not view.core.Settings.AreHostObjectsAllowed and not view.core.Settings.IsWebMessageEnabled
                window.dispose_browser();print('speech error diagnostics passed');app.quit()
            observe()
        view.loadFinished.connect(loaded)
        QtCore.QTimer.singleShot(12000,lambda:os._exit(4));app.exec()
    ''',tmp_path,web_origin)
    assert "speech error diagnostics passed" in output
    assert "PRIVATE_CONSOLE_FIXTURE_DO_NOT_LOG" not in output
    assert "speech recognition error=network runtime=" in output
