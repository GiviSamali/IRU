from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any


DEFAULT_WEB_URL = "http://127.0.0.1:8000"
DEFAULT_WINDOW = {
    "title": "ИРУ",
    "width": 1200,
    "height": 800,
    "min_width": 900,
    "min_height": 600,
}
SECRET_CONFIG_KEYS = {
    "auth_token",
    "access_token",
    "refresh_token",
    "password",
    "token",
    "user_token",
}


def get_iru_home() -> Path:
    explicit = os.environ.get("IRU_HOME")
    if explicit:
        return Path(explicit).expanduser()
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return (Path(root) if root else Path.home() / "AppData" / "Local") / "IRU"
    return Path.home() / ".iru"


def _state_config_path(iru_home: Path | None = None) -> Path:
    return (iru_home or get_iru_home()) / "state" / "shell_config.json"


def _legacy_config_path(iru_home: Path | None = None) -> Path:
    return (iru_home or get_iru_home()) / "shell_config.json"


def get_shell_config_path(iru_home: Path | None = None) -> Path:
    home = iru_home or get_iru_home()
    state_path = _state_config_path(home)
    legacy_path = _legacy_config_path(home)
    if state_path.exists():
        return state_path
    if legacy_path.exists():
        return legacy_path
    return state_path


def default_shell_config() -> dict[str, Any]:
    return {
        "web_url": DEFAULT_WEB_URL,
        "window": dict(DEFAULT_WINDOW),
    }


def _clean_config(data: dict[str, Any] | None) -> dict[str, Any]:
    incoming = dict(data or {})
    for key in SECRET_CONFIG_KEYS:
        incoming.pop(key, None)

    config = default_shell_config()
    web_url = str(incoming.get("web_url") or "").strip()
    if web_url:
        config["web_url"] = web_url

    window = incoming.get("window")
    if isinstance(window, dict):
        safe_window = dict(DEFAULT_WINDOW)
        title = str(window.get("title") or "").strip()
        if title:
            safe_window["title"] = title
        for key in ("width", "height", "min_width", "min_height"):
            try:
                value = int(window.get(key, safe_window[key]))
            except (TypeError, ValueError):
                continue
            if value > 0:
                safe_window[key] = value
        config["window"] = safe_window

    return config


def write_default_config_if_missing(path: Path | None = None) -> Path:
    config_path = path or get_shell_config_path()
    if config_path.exists():
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(default_shell_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


def load_shell_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or get_shell_config_path()
    write_default_config_if_missing(config_path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default_shell_config()
    if not isinstance(raw, dict):
        return default_shell_config()
    return _clean_config(raw)


def resolve_web_url(path: Path | None = None) -> str:
    env_url = os.environ.get("IRU_WEB_URL", "").strip()
    if env_url:
        return env_url
    return str(load_shell_config(path).get("web_url") or DEFAULT_WEB_URL).strip() or DEFAULT_WEB_URL
