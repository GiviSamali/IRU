from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


ACTIVATION_REQUIRED = "activation_required"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def receipt_hash(receipt: dict | None) -> str:
    if not isinstance(receipt, dict):
        return ""
    return hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()


def validate_activation_receipt(receipt: dict | None) -> tuple[bool, str]:
    if not isinstance(receipt, dict):
        return False, "missing_receipt"
    if receipt.get("activation_version") != 1:
        return False, "invalid_version"
    if not str(receipt.get("device_id") or "").strip():
        return False, "missing_device_id"
    status = str(receipt.get("activation_status") or "").strip()
    if status not in {"ok", "repaired", "partial", "degraded", "failed"}:
        return False, "invalid_activation_status"
    paths = receipt.get("paths")
    if not isinstance(paths, dict) or not str(paths.get("iru_home") or "").strip():
        return False, "missing_iru_home"
    identity = receipt.get("identity")
    if not isinstance(identity, dict):
        return False, "missing_identity"
    if not (str(identity.get("hostname") or "").strip() or str(identity.get("computer_name") or "").strip()):
        return False, "missing_identity_hostname"
    if not isinstance(receipt.get("runtime"), dict):
        return False, "missing_runtime"
    if not isinstance(receipt.get("capabilities"), dict):
        return False, "missing_capabilities"
    if not str(receipt.get("created_at") or "").strip():
        return False, "missing_created_at"
    return True, "ok"


def activation_state_from_receipt(receipt: dict | None) -> str:
    valid, _ = validate_activation_receipt(receipt)
    if not valid:
        return ACTIVATION_REQUIRED
    status = str(receipt.get("activation_status") or "")
    if status in {"ok", "repaired"}:
        return "activated"
    if status in {"partial", "degraded"}:
        return "degraded"
    return "activation_failed"


def runtime_status_from_receipt(receipt: dict | None) -> str:
    runtime = receipt.get("runtime") if isinstance(receipt, dict) else None
    if not isinstance(runtime, dict):
        return "unknown"
    return str(runtime.get("managed_python_status") or "unknown")


def python_capability_from_receipt(receipt: dict | None) -> str:
    caps = receipt.get("capabilities") if isinstance(receipt, dict) else None
    if not isinstance(caps, dict):
        return "unknown"
    return str(caps.get("python") or "unknown")


def compact_activation_summary(receipt: dict | None) -> dict:
    valid, reason = validate_activation_receipt(receipt)
    if not valid:
        return {
            "activation_status": ACTIVATION_REQUIRED,
            "runtime_status": "unknown",
            "validation_error": reason,
        }
    identity = receipt.get("identity") or {}
    paths = receipt.get("paths") or {}
    caps = receipt.get("capabilities") or {}
    return {
        "device_id": receipt.get("device_id"),
        "hostname": identity.get("hostname") or identity.get("computer_name"),
        "machine_guid": identity.get("machine_guid"),
        "activation_status": activation_state_from_receipt(receipt),
        "iru_home": paths.get("iru_home"),
        "runtime_status": runtime_status_from_receipt(receipt),
        "python_capability": python_capability_from_receipt(receipt),
        "capabilities_summary": {k: v for k, v in caps.items() if v == "available"},
        "last_activation_check": datetime.now(timezone.utc).isoformat(),
        "receipt_hash": receipt_hash(receipt),
    }


def parse_activation_summary(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def activation_status_from_summary(summary: dict | None) -> str:
    if not isinstance(summary, dict) or not summary:
        return ACTIVATION_REQUIRED
    return str(summary.get("activation_status") or ACTIVATION_REQUIRED)


def runtime_status_from_summary(summary: dict | None) -> str:
    if not isinstance(summary, dict) or not summary:
        return "unknown"
    return str(summary.get("runtime_status") or "unknown")
