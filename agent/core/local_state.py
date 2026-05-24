from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_iru_home() -> Path:
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return (Path(root) if root else Path.home() / "AppData" / "Local") / "IRU"
    return Path.home() / ".iru"


def get_state_dir() -> Path:
    return get_iru_home() / "state"


def _state_path(name: str) -> Path:
    safe = name if name.endswith(".json") else f"{name}.json"
    return get_state_dir() / safe


def read_json_state(name: str) -> dict:
    path = _state_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_state(name: str, data: dict) -> None:
    path = _state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_device_passport_cache() -> dict:
    return read_json_state("device_passport")


def update_device_passport_cache(partial: dict) -> dict:
    current = load_device_passport_cache()
    merged = {**current, **(partial or {})}
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_state("device_passport", merged)
    return merged
