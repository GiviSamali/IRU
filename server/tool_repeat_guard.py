from __future__ import annotations

import json
from typing import Any

try:
    from .tool_registry import canonical_tool_name  # type: ignore
except ImportError:
    from tool_registry import canonical_tool_name  # type: ignore


READ_ONLY_IDEMPOTENT_TOOLS = {
    "system.list_tools",
    "memory.get_stats",
    "memory.list_facts",
    "device.get_passport",
}


def is_read_only_idempotent_tool(tool_name: str | None) -> bool:
    return canonical_tool_name(tool_name or "") in READ_ONLY_IDEMPOTENT_TOOLS


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {
            str(key): _clean_value(item)
            for key, item in value.items()
            if item not in (None, "")
        }
        return {key: cleaned[key] for key in sorted(cleaned)}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def normalize_tool_args_for_repeat_guard(tool_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    canonical = canonical_tool_name(tool_name)
    normalized = dict(args or {})
    if canonical == "system.list_tools" and not normalized.get("category"):
        normalized["category"] = "all"
    if canonical == "memory.list_facts" and not normalized.get("limit"):
        normalized["limit"] = 20
    return _clean_value(normalized)


def repeat_guard_key(tool_name: str, args: dict[str, Any] | None) -> str | None:
    canonical = canonical_tool_name(tool_name)
    if canonical not in READ_ONLY_IDEMPOTENT_TOOLS:
        return None
    normalized = normalize_tool_args_for_repeat_guard(canonical, args)
    return json.dumps(
        {"tool_name": canonical, "args": normalized},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def mark_read_only_tool_step(entry: dict[str, Any], tool_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    key = repeat_guard_key(tool_name, args)
    if key:
        entry["repeat_guard_key"] = key
    return entry


def find_prior_successful_read_only_tool_step(
    journal: list[dict[str, Any]],
    tool_name: str,
    args: dict[str, Any] | None,
) -> dict[str, Any] | None:
    key = repeat_guard_key(tool_name, args)
    if not key:
        return None
    for entry in journal:
        if entry.get("repeat_guard_key") != key:
            continue
        if entry.get("status") in {"failed", "error", "blocked"}:
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("error"):
            continue
        return entry
    return None


def duplicate_read_only_tool_message(tool_name: str, prior_step: dict[str, Any]) -> dict[str, Any]:
    previous_step_id = str(prior_step.get("step_id") or "")
    return {
        "status": "duplicate_read_only_tool_call",
        "tool_name": canonical_tool_name(tool_name),
        "previous_step_id": previous_step_id,
        "previous_summary": prior_step.get("summary") or "",
        "instruction": (
            "This read-only tool was already called with the same arguments in the current run. "
            f"Use answer_text now and cite {previous_step_id} in basis if the answer depends on it."
        ),
    }
