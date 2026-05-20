from __future__ import annotations

import hashlib
import json
from typing import Any


RUNTIME_STATUSES = {"ok", "missing", "install_required", "broken", "degraded"}
PIP_STATUSES = {"ok", "missing", "broken"}


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def runtime_receipt_hash(receipt: dict | None) -> str:
    if not isinstance(receipt, dict):
        return ""
    return hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()


def parse_python_runtime_summary(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def validate_python_runtime_receipt(receipt: dict | None) -> tuple[bool, str]:
    if not isinstance(receipt, dict):
        return False, "missing_receipt"
    if receipt.get("runtime_receipt_version") != 1:
        return False, "invalid_version"
    if not str(receipt.get("device_id") or "").strip():
        return False, "missing_device_id"
    if str(receipt.get("mode") or "").strip() not in {"check", "prepare", "repair"}:
        return False, "invalid_mode"
    status = str(receipt.get("status") or "").strip()
    if status not in RUNTIME_STATUSES:
        return False, "invalid_status"
    if not str(receipt.get("created_at") or "").strip():
        return False, "missing_created_at"
    paths = receipt.get("paths")
    if not isinstance(paths, dict) or not str(paths.get("iru_home") or "").strip():
        return False, "missing_iru_home"
    if not isinstance(receipt.get("python"), dict):
        return False, "missing_python"
    pip = receipt.get("pip")
    if not isinstance(pip, dict):
        return False, "missing_pip"
    if str(pip.get("status") or "").strip() not in PIP_STATUSES:
        return False, "invalid_pip_status"
    if not isinstance(receipt.get("packages"), dict):
        return False, "missing_packages"
    health = receipt.get("health")
    if not isinstance(health, dict):
        return False, "missing_health"
    if status == "ok":
        if not str(paths.get("venv_path") or "").strip():
            return False, "missing_venv_path"
        if not str(paths.get("venv_python") or "").strip():
            return False, "missing_venv_python"
        if not str((receipt.get("python") or {}).get("venv_version") or "").strip():
            return False, "missing_venv_version"
        if str(pip.get("status") or "") != "ok":
            return False, "ok_without_pip"
    return True, "ok"


def compact_python_runtime_summary(receipt: dict | None) -> dict:
    valid, reason = validate_python_runtime_receipt(receipt)
    if not valid:
        return {
            "runtime_status": "unknown",
            "validation_error": reason,
        }
    paths = receipt.get("paths") or {}
    python = receipt.get("python") or {}
    pip = receipt.get("pip") or {}
    return {
        "runtime_status": receipt.get("status"),
        "python_source": python.get("source") or "unknown",
        "venv_python": paths.get("venv_python") or python.get("venv_python"),
        "python_version": python.get("venv_version") or python.get("base_version"),
        "pip_status": pip.get("status") or "unknown",
        "last_runtime_check": receipt.get("created_at"),
        "receipt_hash": runtime_receipt_hash(receipt),
    }


def python_runtime_status_from_summary(summary: dict | None) -> str:
    if not isinstance(summary, dict) or not summary:
        return "unknown"
    return str(summary.get("runtime_status") or "unknown")


def python_runtime_context_markers(summary: dict | None) -> list[str]:
    status = python_runtime_status_from_summary(summary)
    if status in {"missing", "install_required", "broken"}:
        return ["target_device_runtime_not_ready"]
    if status == "degraded":
        return ["target_device_runtime_degraded"]
    return []
