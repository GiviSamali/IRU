from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = {
    "device_id": "",
    "server_url": "wss://irumode.ru",
    "user_token": "",
}


@dataclass(frozen=True)
class AgentPaths:
    base_dir: Path
    config_dir: Path
    config_path: Path
    legacy_config_path: Path
    logs_dir: Path
    log_path: Path
    source_icon_path: Path


def detect_paths() -> AgentPaths:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parents[1]

    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        config_root = Path(local_appdata) if local_appdata else (Path.home() / "AppData" / "Local")
        config_dir = config_root / "IRUAgent"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_dir = (Path(xdg) / "iru-agent") if xdg else (Path.home() / ".config" / "iru-agent")

    logs_dir = config_dir / "logs"
    return AgentPaths(
        base_dir=base_dir,
        config_dir=config_dir,
        config_path=config_dir / "config.json",
        legacy_config_path=base_dir / "config.json",
        logs_dir=logs_dir,
        log_path=logs_dir / "agent.log",
        source_icon_path=base_dir / "IruIcon.ico",
    )


def ensure_dirs(paths: AgentPaths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)


def normalize_server_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return DEFAULT_CONFIG["server_url"]
    if raw.startswith("https://"):
        return "wss://" + raw[len("https://") :]
    if raw.startswith("http://"):
        return "ws://" + raw[len("http://") :]
    return raw.rstrip("/")


def merge_config(data: dict | None) -> dict:
    incoming = dict(data or {})
    if incoming.get("token") and not incoming.get("user_token"):
        incoming["user_token"] = incoming.get("token", "")

    merged = dict(incoming)
    merged["device_id"] = str(incoming.get("device_id", DEFAULT_CONFIG["device_id"])).strip()
    merged["user_token"] = str(incoming.get("user_token", DEFAULT_CONFIG["user_token"])).strip()
    merged["server_url"] = normalize_server_url(
        str(incoming.get("server_url", DEFAULT_CONFIG["server_url"]))
    )

    for key, value in DEFAULT_CONFIG.items():
        merged.setdefault(key, value)
    return merged


def is_config_complete(config: dict) -> bool:
    return bool(
        str(config.get("device_id", "")).strip()
        and str(config.get("user_token", "")).strip()
        and str(config.get("server_url", "")).strip()
    )


def save_config(paths: AgentPaths, data: dict, logger: logging.Logger | None = None) -> dict:
    ensure_dirs(paths)
    merged = merge_config(data)
    tmp_path = paths.config_path.with_name(f"{paths.config_path.name}.tmp-{os.getpid()}")
    payload = json.dumps(merged, ensure_ascii=False, indent=2)
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, paths.config_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
    if logger:
        logger.info("Config saved to %s", paths.config_path)
    return merged


def migrate_legacy_config(paths: AgentPaths, logger: logging.Logger | None = None) -> bool:
    if paths.config_path.exists() or not paths.legacy_config_path.exists():
        return False
    ensure_dirs(paths)
    try:
        shutil.move(str(paths.legacy_config_path), str(paths.config_path))
        if logger:
            logger.info(
                "Migrated legacy config from %s to %s",
                paths.legacy_config_path,
                paths.config_path,
            )
        return True
    except Exception as exc:
        if logger:
            logger.warning("Legacy config migration failed: %s", exc)
        return False


def _backup_broken_config(paths: AgentPaths, logger: logging.Logger | None = None) -> Path | None:
    if not paths.config_path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    broken_path = paths.config_dir / f"config.broken.{ts}.json"
    try:
        paths.config_path.replace(broken_path)
        if logger:
            logger.warning("Broken config moved to %s", broken_path)
        return broken_path
    except Exception as exc:
        if logger:
            logger.error("Failed to back up broken config: %s", exc)
        return None


def load_config(paths: AgentPaths, logger: logging.Logger | None = None) -> dict:
    ensure_dirs(paths)
    migrate_legacy_config(paths, logger=logger)

    if not paths.config_path.exists():
        if logger:
            logger.info("Config not found, creating default at %s", paths.config_path)
        return save_config(paths, DEFAULT_CONFIG, logger=logger)

    try:
        raw = json.loads(paths.config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("config root must be an object")
    except Exception as exc:
        if logger:
            logger.warning("Failed to load config %s: %s", paths.config_path, exc)
        _backup_broken_config(paths, logger=logger)
        return save_config(paths, DEFAULT_CONFIG, logger=logger)

    merged = merge_config(raw)
    if merged != raw:
        return save_config(paths, merged, logger=logger)
    return merged

