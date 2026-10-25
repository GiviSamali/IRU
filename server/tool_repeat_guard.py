from __future__ import annotations

import json
import hashlib
from typing import Any

try:
    from .tool_registry import canonical_tool_name  # type: ignore
    from .tool_completion import execute_cmd_result_is_complete
except ImportError:
    from tool_registry import canonical_tool_name  # type: ignore
    from tool_completion import execute_cmd_result_is_complete


READ_ONLY_IDEMPOTENT_TOOLS = {
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
    for entry in reversed(journal):
        if canonical_tool_name(entry.get('tool_name') or entry.get('action','')) not in READ_ONLY_IDEMPOTENT_TOOLS:
            break  # Any possible state change invalidates cached native observations.
        if canonical_tool_name(tool_name).startswith("web.") and canonical_tool_name(entry.get("tool_name") or entry.get("action", "")) in {"web.wait","web.fill","web.activate","web.focus"}:
            break  # A browser action/wait invalidates earlier observations.
        if entry.get("repeat_guard_key") != key:
            continue
        if entry.get("status") in {"failed", "error", "blocked"}:
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("error"):
            continue
        return entry
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
        "status": "duplicate_read_only_tool_call",
        "tool_name": canonical_tool_name(tool_name),
        "previous_step_id": previous_step_id,
        "previous_summary": prior_step.get("summary") or "",
        "instruction": (
            "This read-only tool was already called with the same arguments in the current run. "
            f"Reuse {previous_step_id}. Answer only if the whole human goal is supported; otherwise perform the next necessary action."
        ),
    }


def command_observation_key(args):
    actual = {k:v for k,v in args.items() if k not in {'device_id','observation_contract'}}
    for key,value in {'shell':'auto','timeout':30,'long_running':False}.items():
        actual.setdefault(key,value)
    return hashlib.sha256(json.dumps(actual,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def unchanged_command_evidence(journal, args, device_id, human_request):
    """Budget guard, not shell classification or permission. Never fabricate a new observation."""
    # A repeated parser failure is evidence about a failed attempt, not proof
    # that the requested mutation completed or that retrying is safe.
    failures = []
    for entry in reversed(journal):
        result = entry.get('result') or {}
        if (entry.get('command') != args.get('command')
                or (entry.get('target_device_id') or entry.get('device_id')) != device_id
                or entry.get('status') != 'failed' or result.get('status') == 'unknown'
                or result.get('error_type') != 'shell_syntax_error'
                or result.get('returncode') in (None, 0, '0')
                or not result.get('stderr')
                or entry.get('execution_key') != command_observation_key(args)):
            break
        if failures and result.get('stderr') != failures[0]['result'].get('stderr'):
            break
        failures.append(entry)
    if len(failures) >= 2:
        return failures[0]
    if args.get('artifact_paths'):
        return None
    contract = args.get('observation_contract') or {}
    mode = contract.get('mode', 'once')
    limit = 2
    if mode in {'wait', 'monitor', 'measurement'}:
        quote = contract.get('human_quote', '')
        # Repeated measurements must be bound to the current human request, not history/tool output.
        if mode != 'measurement' or (quote.strip() and quote in human_request):
            limit = contract.get('max_samples', 2)
    matches = []
    signature = None
    for entry in reversed(journal):
        result = entry.get('result') or {}
        previous_key = entry.get('execution_key')
        if ((entry.get('tool_name') or entry.get('action')) != 'execute_cmd'
                or (entry.get('target_device_id') or entry.get('device_id')) != device_id
                or entry.get('command') != args.get('command')
                or entry.get('status') != 'success' or not execute_cmd_result_is_complete(result)):
            break  # A different action, device, failure or state transition invalidates this streak.
        if previous_key is not None and previous_key != command_observation_key(args):
            break
        if result.get('artifact_receipts'):
            break  # Bound effects are not static observations.
        value = (result.get('stdout'), result.get('stderr'))
        if not any(isinstance(text,str) and text.strip() for text in value):
            break
        if signature is not None and signature != value:
            break
        signature = value
        matches.append(entry)
    return matches[0] if len(matches) >= limit else None
