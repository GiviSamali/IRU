from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.shell.config import get_shell_config_path, load_shell_config, resolve_web_url
    from agent.shell.tray import ShellTrayController, start_tray_in_thread
else:
    from .config import get_shell_config_path, load_shell_config, resolve_web_url
    from .tray import ShellTrayController, start_tray_in_thread


def _open_in_browser(url: str) -> None:
    print(f"WebView недоступен, открываю ИРУ в браузере: {url}")
    webbrowser.open(url)


def main() -> int:
    config = load_shell_config()
    url = resolve_web_url()
    config_path = get_shell_config_path()
    window = config.get("window") or {}
    title = str(window.get("title") or "ИРУ")
    width = int(window.get("width") or 1200)
    height = int(window.get("height") or 800)
    min_width = int(window.get("min_width") or 900)
    min_height = int(window.get("min_height") or 600)
    exit_event = threading.Event()
    tray_controller = ShellTrayController(
        web_url=url,
        config_path=config_path,
        exit_callback=exit_event.set,
    )

    try:
        import webview  # type: ignore
    except Exception:
        tray_thread = start_tray_in_thread(tray_controller)
        _open_in_browser(url)
        if tray_thread is not None:
            exit_event.wait()
        return 0

    try:
        create_kwargs = {
            "title": title,
            "url": url,
            "width": width,
            "height": height,
        }
        if min_width > 0:
            create_kwargs["min_size"] = (min_width, min_height)
        try:
            window_ref = webview.create_window(**create_kwargs)
        except TypeError:
            create_kwargs.pop("min_size", None)
            window_ref = webview.create_window(**create_kwargs)
        tray_controller.webview_window = window_ref
        tray_controller.webview_module = webview
        start_tray_in_thread(tray_controller)
        webview.start()
        return 0
    except Exception as exc:
        print(f"WebView не удалось запустить: {exc}", file=sys.stderr)
        if tray_controller.tray_icon is None:
            start_tray_in_thread(tray_controller)
        _open_in_browser(url)
        if tray_controller.tray_icon is not None:
            exit_event.wait()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
