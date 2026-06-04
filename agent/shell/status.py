from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .config import get_shell_config_path, resolve_web_url


def is_module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def build_status_payload(
    *,
    web_url: str | None = None,
    config_path: Path | None = None,
    pywebview_available: bool | None = None,
    tray_available: bool | None = None,
) -> dict[str, Any]:
    resolved_config_path = config_path or get_shell_config_path()
    return {
        "web_url": web_url or resolve_web_url(resolved_config_path),
        "config_path": str(resolved_config_path),
        "pywebview_available": (
            is_module_available("webview")
            if pywebview_available is None
            else bool(pywebview_available)
        ),
        "tray_available": (
            is_module_available("pystray") and is_module_available("PIL")
            if tray_available is None
            else bool(tray_available)
        ),
    }


def format_status(payload: dict[str, Any]) -> str:
    return "\n".join([
        "Agent Shell status:",
        f"web_url: {payload.get('web_url', '')}",
        f"config_path: {payload.get('config_path', '')}",
        f"pywebview_available: {str(bool(payload.get('pywebview_available'))).lower()}",
        f"tray_available: {str(bool(payload.get('tray_available'))).lower()}",
    ])

