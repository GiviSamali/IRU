from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.shell.config import load_shell_config, resolve_web_url
else:
    from .config import load_shell_config, resolve_web_url


def _open_in_browser(url: str) -> None:
    print(f"WebView недоступен, открываю ИРУ в браузере: {url}")
    webbrowser.open(url)


def main() -> int:
    config = load_shell_config()
    url = resolve_web_url()
    window = config.get("window") or {}
    title = str(window.get("title") or "ИРУ")
    width = int(window.get("width") or 1200)
    height = int(window.get("height") or 800)
    min_width = int(window.get("min_width") or 900)
    min_height = int(window.get("min_height") or 600)

    try:
        import webview  # type: ignore
    except Exception:
        _open_in_browser(url)
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
        webview.create_window(**create_kwargs)
        webview.start()
        return 0
    except Exception as exc:
        print(f"WebView не удалось запустить: {exc}", file=sys.stderr)
        _open_in_browser(url)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
