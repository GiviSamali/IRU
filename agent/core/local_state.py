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
    passport = read_json_state("device_passport")
    if not passport:
        return passport
    return _with_current_runtime(passport)


def _with_current_runtime(passport: dict) -> dict:
    # A derived passport, not a rewrite of historic activation/runtime receipts.
    summary = dict(passport.get("runtime_summary") or {})
    receipt = read_json_state("runtime_receipt") or read_json_state("python_runtime_receipt")
    valid_receipt = (receipt.get("runtime_receipt_version") == 1 and receipt.get("device_id") == passport.get("device_id")
        and receipt.get("mode") in {"check", "prepare", "repair"}
        and receipt.get("status") in {"ok", "missing", "install_required", "broken", "degraded"}
        and isinstance(receipt.get("python"), dict) and isinstance(receipt.get("pip"), dict)
        and isinstance(receipt.get("paths"), dict) and receipt["paths"].get("iru_home")
        and isinstance(receipt.get("health"), dict) and isinstance(receipt.get("packages"), dict))
    if valid_receipt:
        python, pip, paths = receipt.get("python") or {}, receipt.get("pip") or {}, receipt.get("paths") or {}
        candidate = {"runtime_status": receipt.get("status"), "python_source": python.get("source"),
                   "python_version": python.get("venv_version") or python.get("base_version"),
                   "pip_status": pip.get("status"), "pip_version": pip.get("version"),
                   "venv_python": paths.get("venv_python"), "last_runtime_check": receipt.get("created_at"),
                   "runtime_verified": True}
        def checked_at(value):
            try:
                stamp = datetime.fromisoformat(str(value.get("last_runtime_check") or "").replace("Z", "+00:00"))
                return stamp if stamp.tzinfo is not None else datetime.min.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return datetime.min.replace(tzinfo=timezone.utc)
        if checked_at(candidate) >= checked_at(summary):
            summary = candidate
        else:
            valid_receipt = False
    try:
        stamp = datetime.fromisoformat(str(summary.get("last_runtime_check") or "").replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        fresh = summary.get("runtime_status") in {"ok", "missing", "install_required", "broken", "degraded"} and -300 <= age <= 86400 and (summary.get("runtime_verified") is True or bool(summary.get("receipt_hash")))
    except (TypeError, ValueError):
        fresh = False
    if summary.get("runtime_status") == "ok":
        fresh = fresh and bool(summary.get("venv_python") and summary.get("python_version") and summary.get("pip_status") == "ok")
    known_status = summary.get("runtime_status") or "unknown"
    summary.update(runtime_fresh=bool(fresh), runtime_source="verified_runtime_receipt" if valid_receipt else "agent_cache")
    if not fresh:
        summary.update(runtime_status="unknown", python_version=None, pip_status="unknown", venv_python=None,
                       last_known_runtime_status=known_status)
    caps = dict(passport.get("capabilities") or {})
    caps["python"] = "available" if fresh and known_status == "ok" else ("missing" if fresh and known_status in {"missing", "install_required", "broken"} else "unknown")
    activation = dict(passport.get("activation_summary") or {})
    activation.update(runtime_status=summary.get("runtime_status") or "unknown", python_capability=caps["python"],
                      runtime_fresh=bool(fresh), runtime_source=summary["runtime_source"])
    available = dict(activation.get("capabilities_summary") or {})
    available.pop("python", None)
    if caps["python"] == "available":
        available["python"] = "available"
    activation["capabilities_summary"] = available
    return {**passport, "runtime_summary": summary, "activation_summary": activation, "capabilities": caps,
            "python_runtime_status": summary.get("runtime_status") or "unknown", "python_version": summary.get("python_version"),
            "pip_status": summary.get("pip_status"), "venv_python": summary.get("venv_python"), "runtime_fresh": bool(fresh)}


def update_device_passport_cache(partial: dict) -> dict:
    current = load_device_passport_cache()
    merged = {**current, **(partial or {})}
    merged = _with_current_runtime(merged)
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_state("device_passport", merged)
    return merged
