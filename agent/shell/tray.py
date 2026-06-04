from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .status import build_status_payload, format_status


OPEN_IRU_LABEL = "Открыть ИРУ"
OPEN_BROWSER_LABEL = "Открыть в браузере"
SETTINGS_LABEL = "Настройки"
STATUS_LABEL = "Статус"
EXIT_LABEL = "Выход"


@dataclass
class ShellTrayController:
    web_url: str
    config_path: Path
    webview_window: Any = None
    webview_module: Any = None
    tray_icon: Any = None
    exit_callback: Callable[[], None] | None = None

    def open_iru(self) -> None:
        window = self.webview_window
        if window is not None:
            for method_name in ("restore", "show", "focus"):
                method = getattr(window, method_name, None)
                if callable(method):
                    try:
                        method()
                        return
                    except Exception:
                        continue
        webbrowser.open(self.web_url)

    def open_browser(self) -> None:
        webbrowser.open(self.web_url)

    def open_settings(self) -> None:
        open_config_location(self.config_path)

    def show_status(self) -> None:
        payload = build_status_payload(
            web_url=self.web_url,
            config_path=self.config_path,
            pywebview_available=self.webview_module is not None,
            tray_available=self.tray_icon is not None,
        )
        message = format_status(payload)
        print(message)
        notify_status(self.tray_icon, message)

    def exit_shell(self) -> None:
        icon = self.tray_icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

        window = self.webview_window
        if window is not None:
            destroy = getattr(window, "destroy", None)
            if callable(destroy):
                try:
                    destroy()
                except Exception:
                    pass

        if self.exit_callback:
            self.exit_callback()


def open_config_location(config_path: Path) -> None:
    try:
        if sys.platform == "win32":
            if config_path.exists():
                subprocess.Popen(["explorer", "/select,", str(config_path)])
            else:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                os.startfile(str(config_path.parent))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(config_path.parent)])
            return
        subprocess.Popen(["xdg-open", str(config_path.parent)])
    except Exception:
        print(f"Shell config: {config_path}")


def notify_status(icon: Any, message: str) -> None:
    notify = getattr(icon, "notify", None)
    if callable(notify):
        try:
            notify(message, "ИРУ")
        except Exception:
            pass


def _build_icon_image() -> Any:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    image = Image.new("RGB", (64, 64), "#020308")
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill="#07131d", outline="#00d4ff", width=3)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 15), "И", fill="#00d4ff", font=font)
    return image


def create_tray_icon(controller: ShellTrayController) -> Any | None:
    try:
        import pystray  # type: ignore
        from PIL import Image  # noqa: F401  # type: ignore
    except Exception as exc:
        print(f"Tray недоступен, продолжаю без tray: {exc}")
        return None

    try:
        menu = pystray.Menu(
            pystray.MenuItem(OPEN_IRU_LABEL, lambda icon, item: controller.open_iru()),
            pystray.MenuItem(OPEN_BROWSER_LABEL, lambda icon, item: controller.open_browser()),
            pystray.MenuItem(SETTINGS_LABEL, lambda icon, item: controller.open_settings()),
            pystray.MenuItem(STATUS_LABEL, lambda icon, item: controller.show_status()),
            pystray.MenuItem(EXIT_LABEL, lambda icon, item: controller.exit_shell()),
        )
        icon = pystray.Icon("iru-agent-shell", _build_icon_image(), "ИРУ", menu)
        controller.tray_icon = icon
        return icon
    except Exception as exc:
        print(f"Tray не удалось запустить, продолжаю без tray: {exc}")
        return None


def start_tray_in_thread(controller: ShellTrayController) -> threading.Thread | None:
    icon = create_tray_icon(controller)
    if icon is None:
        return None

    thread = threading.Thread(target=icon.run, name="iru-agent-shell-tray", daemon=True)
    thread.start()
    return thread

