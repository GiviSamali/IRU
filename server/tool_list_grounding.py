from __future__ import annotations

from typing import Any

try:
    from .tool_registry import canonical_tool_name
except ImportError:
    from tool_registry import canonical_tool_name  # type: ignore


def _tool_names_from_registry_result(result: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(result, dict):
        return names
    for tools in result.values():
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                names.append(tool["name"])
    return list(dict.fromkeys(names))


def _format_tool_list(result: dict[str, Any]) -> str:
    lines = ["Доступные инструменты:"]
    for category, tools in result.items():
        if not isinstance(tools, list) or not tools:
            continue
        names = [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]
        if names:
            lines.append(f"- {category}: {', '.join(names)}")
    return "\n".join(lines)


def sanitize_system_list_tools_answer(answer_payload: dict[str, Any], journal: list[dict[str, Any]]) -> dict[str, Any]:
    basis = set(answer_payload.get("basis") or [])
    if not basis:
        return answer_payload
    for entry in journal:
        if entry.get("step_id") not in basis:
            continue
        if canonical_tool_name(str(entry.get("tool_name") or entry.get("action") or "")) != "system.list_tools":
            continue
        result = entry.get("result")
        allowed_names = set(_tool_names_from_registry_result(result))
        if not allowed_names or not isinstance(result, dict):
            return answer_payload
        sanitized = dict(answer_payload)
        sanitized["text"] = _format_tool_list(result)
        return sanitized
    return answer_payload
