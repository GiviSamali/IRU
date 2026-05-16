from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from .device_context import build_minimal_llm_context
except ImportError:
    from device_context import build_minimal_llm_context


CANONICAL_TOOL_NAMES = {
    "system_list_tools": "system.list_tools",
    "device_get_passport": "device.get_passport",
    "device_refresh_state": "device.refresh_state",
    "device_activate": "device.activate",
    "device_repair_activation": "device.repair_activation",
    "write_content": "write_content",
    "execute_cmd": "execute_cmd",
}


TOOL_METADATA = {
    "system.list_tools": {
        "category": "system",
        "tool_type": "system",
        "tool_label": "Tool registry",
        "purpose": "List available typed tools grouped by category",
        "when_to_use": ["need available capabilities", "before choosing a tool"],
        "returns": "compact grouped tool registry",
        "danger": "safe",
    },
    "device.get_passport": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Device passport",
        "purpose": "Get compact device passport and context handles",
        "when_to_use": [
            "user asks about device passport",
            "need current activation/runtime/health status",
            "need context handles for device data",
        ],
        "returns": "compact passport + context handles",
        "danger": "safe",
    },
    "device.refresh_state": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Обновление паспорта устройства",
        "purpose": "Get a fresh live device state snapshot and update the passport",
        "when_to_use": [
            "user asks current device state",
            "need to refresh device passport",
            "before performance diagnostics",
        ],
        "returns": "compact health summary + state handle",
        "danger": "safe",
        "uses_shell_internally": True,
    },
    "device.activate": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Активация устройства",
        "purpose": "Run soft device activation and store a validated activation summary",
        "when_to_use": ["activation is required", "device needs activation before work"],
        "returns": "compact activation summary",
        "danger": "safe",
    },
    "device.repair_activation": {
        "category": "device",
        "tool_type": "typed",
        "tool_label": "Repair activation",
        "purpose": "Repair degraded or failed activation without claiming to install Python",
        "when_to_use": ["activation_status is degraded", "activation_status is activation_failed"],
        "returns": "compact activation summary",
        "danger": "safe",
    },
    "write_content": {
        "category": "files",
        "tool_type": "typed",
        "tool_label": "Запись файла",
        "purpose": "Create or overwrite a text file without shell escaping",
        "when_to_use": ["create txt/json/py/html file", "write text to a file"],
        "returns": "file write result",
        "danger": "write",
    },
    "execute_cmd": {
        "category": "fallback",
        "tool_type": "fallback",
        "tool_label": "PowerShell / shell fallback",
        "purpose": "Low-level shell fallback, use only when typed tools are unavailable",
        "when_to_use": ["no typed tool exists", "no playbook exists"],
        "returns": "command result",
        "danger": "depends_on_command",
    },
}


DEVICE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "system_list_tools",
            "description": "List compact available tools grouped by category. Use before choosing a capability when unsure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["all", "device", "files", "python", "app", "artifact"],
                        "default": "all",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_get_passport",
            "description": "Get compact device passport: hostname, online, activation/runtime/health/identity status, capabilities, and context handles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_refresh_state",
            "description": "Get a fresh live state snapshot for a device and update its passport. Do not use raw shell for device-state requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_activate",
            "description": "Run soft device activation and return compact activation summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."},
                    "mode": {"type": "string", "enum": ["soft"], "default": "soft"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "device_repair_activation",
            "description": "Repair degraded or failed activation. This does not install managed Python.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device ID. Defaults to current device."}
                },
            },
        },
    },
]


def canonical_tool_name(name: str) -> str:
    return CANONICAL_TOOL_NAMES.get(name, name)


def _compact_tool_meta(name: str, meta: dict[str, Any]) -> dict[str, Any]:
    result = {
        "name": name,
        "purpose": meta.get("purpose"),
        "when_to_use": meta.get("when_to_use") or [],
        "danger": meta.get("danger"),
    }
    if meta.get("returns"):
        result["returns"] = meta["returns"]
    if meta.get("uses_shell_internally") is not None:
        result["uses_shell_internally"] = bool(meta.get("uses_shell_internally"))
    return result


def list_tools(category: str = "all") -> dict[str, list[dict[str, Any]]]:
    requested = (category or "all").strip().lower()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, meta in TOOL_METADATA.items():
        group = meta.get("category") or "other"
        if requested != "all" and group != requested:
            continue
        grouped.setdefault(group, []).append(_compact_tool_meta(name, meta))
    return grouped


def _result_status(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("error"):
            return "failed"
        if result.get("status") in {"failed", "error"}:
            return "failed"
        if result.get("status") == "skipped":
            return "skipped"
    return "success"


def compact_tool_summary(action: str, result: Any = None, command: str = "") -> str:
    name = canonical_tool_name(action)
    if isinstance(result, dict):
        if result.get("summary"):
            return str(result["summary"])[:240]
        if result.get("error"):
            return str(result["error"])[:240]
        if name == "device.refresh_state":
            health = result.get("health_summary") or {}
            status = result.get("status") or "ok"
            health_status = health.get("health_status") or "unknown"
            return f"status={status}; health={health_status}"
        if name in {"device.activate", "device.repair_activation"}:
            summary = result.get("activation_summary") or result.get("summary") or {}
            if isinstance(summary, dict):
                return f"activation={summary.get('activation_status') or summary.get('status') or 'unknown'}"
        if name == "device.get_passport":
            return f"passport={result.get('device_id') or 'device'}"
        if name == "write_content":
            path = result.get("path") or result.get("file_path") or ""
            return f"file written: {path}" if path else "file write completed"
        if name == "execute_cmd":
            rc = result.get("returncode")
            return f"returncode={rc}" if rc is not None else "shell command completed"
    if command:
        return command[:240]
    return f"{name} completed"


def tool_log_fields(action: str, result: Any = None, command: str = "", target_device_id: str | None = None) -> dict[str, Any]:
    name = canonical_tool_name(action)
    meta = TOOL_METADATA.get(name)
    if not meta:
        return {}
    return {
        "tool_name": name,
        "tool_type": meta.get("tool_type", "typed"),
        "tool_label": meta.get("tool_label") or name,
        "tool_status": _result_status(result),
        "target_device_id": target_device_id,
        "summary": compact_tool_summary(action, result, command),
    }


def tool_log_entry(
    action: str,
    result: Any = None,
    *,
    command: str = "",
    target_device_id: str | None = None,
    hostname: str | None = None,
    iteration: int | None = None,
) -> dict[str, Any]:
    entry = {
        "action": action,
        "command": command or f"[tool] {canonical_tool_name(action)}",
        "device_id": target_device_id,
        "target_device_id": target_device_id,
        "hostname": hostname or target_device_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    if iteration is not None:
        entry["iteration"] = iteration
    entry.update(tool_log_fields(action, result, command, target_device_id))
    return entry


def compact_device_passport(device_id: str, dev: dict | None, profile: dict | None = None) -> dict[str, Any]:
    context = build_minimal_llm_context(device_id, {device_id: dev or {}}, profile)
    current = context.get("current_device") or {}
    state = current.get("state_summary") if isinstance(current.get("state_summary"), dict) else {}
    return {
        "device_id": current.get("device_id") or device_id,
        "hostname": current.get("hostname") or device_id,
        "online": bool(current.get("online")),
        "activation_status": current.get("activation_status"),
        "runtime_status": current.get("runtime_status"),
        "health_status": current.get("health_status"),
        "last_snapshot_at": state.get("last_snapshot_at"),
        "identity_status": state.get("identity_status"),
        "capabilities_summary": current.get("capabilities_summary") or [],
        "context_handles": current.get("context_handles") or {},
    }
