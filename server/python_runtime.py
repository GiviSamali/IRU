from __future__ import annotations

import hashlib
import json
from typing import Any
from datetime import datetime, timezone


RUNTIME_STATUSES = {"ok", "missing", "install_required", "broken", "degraded"}
PIP_STATUSES = {"ok", "missing", "broken"}
RUNTIME_MAX_AGE_SECONDS = 24 * 60 * 60


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
        "pip_version": pip.get("version"),
        "runtime_verified": True,
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


def current_runtime_summary(dev: dict | None, profile: dict | None = None, *, now: datetime | None = None) -> dict:
    """Fresh verified runtime facts win; activation is never current runtime evidence."""
    dev = dev or {}
    cached = dev.get("agent_cached_passport") or {}
    candidates = []
    receipt = dev.get("python_runtime_receipt")
    if validate_python_runtime_receipt(receipt)[0]:
        candidates.append((compact_python_runtime_summary(receipt), "verified_runtime_receipt"))
    for value, source in ((dev.get("python_runtime_summary"), "runtime_summary"),
                          (cached.get("runtime_summary"), "agent_cache"),
                          ((profile or {}).get("python_runtime_summary"), "server_cache")):
        summary = parse_python_runtime_summary(value)
        if summary:
            candidates.append((summary, source))
    now = now or datetime.now(timezone.utc)
    dated = []
    for summary, source in candidates:
        try:
            stamp = datetime.fromisoformat(str(summary.get("last_runtime_check") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            dated.append((stamp, summary, source))
        except (TypeError, ValueError):
            continue
    if dated:
        stamp, summary, source = max(dated, key=lambda item: item[0])
    else:
        summary, source = candidates[0] if candidates else ({}, "missing")
        stamp = None
    age = (now - stamp).total_seconds() if stamp is not None else None
    verified = summary.get("runtime_verified") is True or bool(summary.get("receipt_hash"))
    fresh = bool(verified and summary.get("runtime_status") in RUNTIME_STATUSES and age is not None and -300 <= age <= RUNTIME_MAX_AGE_SECONDS)
    if fresh and summary.get("runtime_status") == "ok":
        fresh = bool(summary.get("venv_python") and summary.get("python_version") and summary.get("pip_status") == "ok")
    result = {**summary, "runtime_source": source, "runtime_fresh": fresh}
    if not fresh:
        result.update(runtime_status="unknown", python_version=None, pip_status="unknown", venv_python=None,
                      last_known_runtime_status=summary.get("runtime_status") or "unknown")
    return result
