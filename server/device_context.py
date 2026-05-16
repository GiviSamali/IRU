from __future__ import annotations

import json
import re
from typing import Any

try:
    from . import database as db  # type: ignore
    from .device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
    )
except ImportError:
    import database as db  # type: ignore
    from device_activation import (
        activation_status_from_summary,
        compact_activation_summary,
        parse_activation_summary,
        runtime_status_from_summary,
    )


HANDLE_RE = re.compile(r"^ctx://device/([^/]+)/([^/]+)$")


def _short_did(device_id: str) -> str:
    return device_id.split(":", 1)[1] if ":" in device_id else device_id


def _summary_from(dev: dict | None, profile: dict | None) -> dict:
    if isinstance(dev, dict) and isinstance(dev.get("activation_receipt"), dict):
        return compact_activation_summary(dev["activation_receipt"])
    if isinstance(dev, dict) and isinstance(dev.get("activation_summary"), dict):
        return dict(dev["activation_summary"])
    if isinstance(profile, dict):
        return parse_activation_summary(profile.get("activation_summary"))
    return {}


def _handles(device_id: str) -> dict:
    base = f"ctx://device/{device_id}"
    return {
        "activation_receipt": f"{base}/activation",
        "python_runtime": f"{base}/python",
        "device_state": f"{base}/state",
        "artifacts": f"{base}/artifacts",
        "recent_traces": f"{base}/traces",
    }


def _capability_list(summary: dict) -> list[str]:
    caps = summary.get("capabilities_summary")
    if isinstance(caps, dict):
        return sorted(caps.keys())
    if isinstance(caps, list):
        return [str(item) for item in caps]
    return []


def _state_summary_from(dev: dict | None) -> dict:
    record = dev.get("last_state_snapshot") if isinstance(dev, dict) else None
    if not isinstance(record, dict):
        return {
            "health_status": "unknown",
            "last_snapshot_at": None,
            "identity_status": "unknown",
            "state_snapshot_fresh": False,
        }
    health = record.get("health_summary") if isinstance(record.get("health_summary"), dict) else {}
    return {
        "health_status": health.get("health_status") or "unknown",
        "last_snapshot_at": record.get("collected_at"),
        "identity_status": health.get("identity_status") or (record.get("identity_receipt") or {}).get("identity_status") or "unknown",
        "state_snapshot_fresh": bool(record.get("collected_at")),
        "cpu_load": health.get("cpu_load"),
        "ram_used_pct": health.get("ram_used_pct"),
        "disk_used_pct": health.get("disk_used_pct"),
        "process_count": health.get("process_count"),
        "uptime": health.get("uptime"),
    }


def _device_manifest(device_id: str, dev: dict | None, profile: dict | None, *, include_handles: bool) -> dict:
    info = dev.get("info", {}) if isinstance(dev, dict) else {}
    summary = _summary_from(dev, profile)
    state_summary = _state_summary_from(dev)
    health = state_summary.get("health_status") or "unknown"
    if isinstance(dev, dict) and isinstance(dev.get("activation_receipt"), dict):
        activation_health = (dev["activation_receipt"].get("health") or {}).get("agent") or "unknown"
        health = activation_health if health == "unknown" else health
    item = {
        "device_id": device_id,
        "hostname": info.get("hostname") or (profile or {}).get("hostname") or device_id,
        "online": bool(isinstance(dev, dict) and dev.get("ws") is not None),
        "activation_status": activation_status_from_summary(summary),
        "health_status": health,
        "runtime_status": runtime_status_from_summary(summary),
        "capabilities_summary": _capability_list(summary),
        "state_summary": state_summary,
    }
    if isinstance(dev, dict) and dev.get("activation_context_markers"):
        item["context_markers"] = list(dev.get("activation_context_markers") or [])
    if include_handles:
        item["context_handles"] = _handles(device_id)
    return item


def build_minimal_llm_context(current_device_id: str, all_devices: dict, current_profile: dict | None = None) -> dict:
    current_id = _short_did(current_device_id)
    current_dev = (all_devices or {}).get(current_id) or (all_devices or {}).get(current_device_id)
    if current_profile is None:
        try:
            current_profile = db.get_device_profile(current_id)
        except Exception:
            current_profile = None
    other_devices = []
    for raw_id, dev in (all_devices or {}).items():
        did = _short_did(raw_id)
        if did == current_id:
            continue
        try:
            profile = db.get_device_profile(did)
        except Exception:
            profile = None
        other_devices.append(_device_manifest(did, dev, profile, include_handles=True))
    return {
        "current_device": _device_manifest(current_id, current_dev, current_profile, include_handles=True),
        "other_devices": other_devices,
    }


def format_minimal_llm_context_block(context: dict) -> str:
    return "## Compact device manifest\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)


def get_context_handle(handle: str, *, all_devices: dict | None = None) -> dict:
    match = HANDLE_RE.match(handle or "")
    if not match:
        return {"status": "not_found", "source": "missing", "data": None}
    device_id, kind = match.groups()
    dev = (all_devices or {}).get(device_id)
    live = bool(isinstance(dev, dict) and dev.get("ws") is not None)
    try:
        profile = db.get_device_profile(device_id)
    except Exception:
        profile = None
    receipt = dev.get("activation_receipt") if isinstance(dev, dict) else None
    if kind == "activation":
        if isinstance(receipt, dict):
            return {"status": "ok" if live else "stale", "source": "agent_live" if live else "server_cache", "data": receipt}
        summary = _summary_from(dev, profile)
        if summary:
            return {"status": "stale", "source": "server_cache", "data": summary}
        return {"status": "not_found", "source": "missing", "data": None}
    if kind == "python":
        if isinstance(receipt, dict):
            return {"status": "ok" if live else "stale", "source": "agent_live" if live else "server_cache", "data": receipt.get("runtime")}
        summary = _summary_from(dev, profile)
        if summary:
            return {"status": "stale", "source": "server_cache", "data": {"runtime_status": runtime_status_from_summary(summary)}}
        return {"status": "not_found", "source": "missing", "data": None}
    if kind == "state":
        record = dev.get("last_state_snapshot") if isinstance(dev, dict) else None
        if isinstance(record, dict):
            return {"status": "ok" if live else "stale", "source": "agent_live" if live else "server_cache", "data": record}
        return {"status": "not_found", "source": "missing", "data": None}
    if kind in {"artifacts", "traces"}:
        return {"status": "unavailable", "source": "missing", "data": None}
    return {"status": "not_found", "source": "missing", "data": None}


def resolve_context_bundle(bundle_name: str, device_id: str, *, all_devices: dict | None = None) -> dict:
    name = (bundle_name or "baseline").strip()
    if name == "baseline":
        return {"status": "ok", "source": "server_cache", "data": build_minimal_llm_context(device_id, all_devices or {})}
    if name == "full_activation_receipt":
        return get_context_handle(f"ctx://device/{device_id}/activation", all_devices=all_devices)
    if name == "full_python_receipt":
        return get_context_handle(f"ctx://device/{device_id}/python", all_devices=all_devices)
    if name in {"device_inventory", "activation_summary", "python_runtime", "device_state", "artifacts_summary", "recent_trace_summary"}:
        context = build_minimal_llm_context(device_id, all_devices or {})
        return {"status": "ok", "source": "server_cache", "data": context}
    return {"status": "not_found", "source": "missing", "data": None}


def activation_markers_for_task(message: str, manifest: dict) -> list[str]:
    lower = (message or "").lower()
    heavy = any(token in lower for token in ("python", "pip", "venv", "word", "excel", "docx", "xlsx", "exe", "github", "artifact", "файл", "питон"))
    if not heavy:
        return []
    current = manifest.get("current_device") or {}
    markers = []
    if current.get("activation_status") in {"activation_required", "degraded", "activation_failed"}:
        markers.append("target_device_not_activated")
    caps = current.get("capabilities_summary") or []
    runtime_status = current.get("runtime_status") or "unknown"
    if runtime_status != "ok" or "python" not in caps:
        markers.append("target_device_runtime_not_ready")
    return markers
