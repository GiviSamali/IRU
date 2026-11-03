from __future__ import annotations

import json
import hashlib
from typing import Any

try:
    from .tool_registry import canonical_tool_name  # type: ignore
except ImportError:
    from tool_registry import canonical_tool_name  # type: ignore


READ_ONLY_IDEMPOTENT_TOOLS = {
    "web_search",
    "system.list_tools",
    "memory.get_stats",
    "memory.list_facts",
    "device.get_passport",
    "web.tabs", "web.read", "web.elements",
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
    if canonical == 'web_search':
        normalized['query'] = str(normalized.get('query') or '').strip()
        normalized['max_results'] = min(int(normalized.get('max_results') or 5), 10)
        normalized.pop('refresh', None)
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
    # Idempotence is not freshness: files, pages, devices and memory can change
    # outside this Worker. Only its fixed tool inventory is reusable here.
    if canonical_tool_name(tool_name) != 'system.list_tools':
        return None
    key = repeat_guard_key(tool_name, args)
    if not key:
        return None
    for entry in reversed(journal):
        if entry.get("repeat_guard_key") != key:
            continue
        if entry.get("status") not in {"success"}:
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("error"):
            continue
        return entry
    return None


def repeated_search_observation(journal: list[dict[str, Any]]) -> str | None:
    """Link identical fresh search observations without reusing stale results."""
    latest = journal[-1] if journal else {}
    key = latest.get('repeat_guard_key')
    result = latest.get('result') or {}
    if latest.get('tool_name') != 'web_search' or not key or latest.get('status') != 'success':
        return None
    sources = result.get('results')
    if not sources:
        return None
    for entry in reversed(journal[:-1]):
        if entry.get('repeat_guard_key') != key or entry.get('status') != 'success':
            continue
        previous = entry.get('result') or {}
        if previous.get('results') == sources and previous.get('answer') == result.get('answer'):
            return entry.get('step_id')
        break
    return None


def repeated_command_observation(journal: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Feedback only: identical execution is never assumed safe to skip."""
    if not journal:
        return None
    latest = journal[-1]
    result = latest.get("result")
    if canonical_tool_name(latest.get("tool_name") or latest.get("action", "")) != "execute_cmd":
        return None
    if not isinstance(result, dict) or result.get("error") or result.get("returncode") not in (0, "0"):
        return None
    observation = (result.get("stdout"), result.get("stderr"))
    if not any(isinstance(text, str) and text.strip() for text in observation):
        return None
    count = 1
    first = latest
    for entry in reversed(journal[:-1]):
        previous = entry.get("result")
        if (entry.get("command") != latest.get("command") or entry.get("target_device_id") != latest.get("target_device_id")
                or entry.get("device_id") != latest.get("device_id") or entry.get("tool_name") != latest.get("tool_name")
                or not isinstance(previous, dict) or previous.get("error")
                or previous.get("returncode") not in (0, "0")
                or (previous.get("stdout"), previous.get("stderr")) != observation):
            break
        count += 1
        first = entry
    return {"count": count, "step_id": first.get("step_id")} if count >= 2 else None


def duplicate_read_only_tool_message(tool_name: str, prior_step: dict[str, Any]) -> dict[str, Any]:
    previous_step_id = str(prior_step.get("step_id") or "")
    return {
        **({'result': prior_step.get('result'), 'reused': True} if canonical_tool_name(tool_name)=='web_search' else {}),
        "status": "duplicate_read_only_tool_call",
        "tool_name": canonical_tool_name(tool_name),
        "previous_step_id": previous_step_id,
        "previous_summary": prior_step.get("summary") or "",
        "instruction": (
            "This read-only tool was already called with the same arguments in the current run. "
            f"Reuse {previous_step_id}. Answer only if the whole human goal is supported; otherwise perform the next necessary action."
        ),
    }
