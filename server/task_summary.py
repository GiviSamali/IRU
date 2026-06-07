from __future__ import annotations

from typing import Any

try:
    from .runtime_state import tasks
    from .tool_registry import canonical_tool_name, compact_tool_summary
except ImportError:
    from runtime_state import tasks  # type: ignore
    from tool_registry import canonical_tool_name, compact_tool_summary  # type: ignore


FAILED_STATUSES = {"error", "failed", "blocked", "cancelled"}
SUCCESS_STATUSES = {"done", "ok", "success", "completed", "completed_with_recovery"}


def _flatten_commands(task: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    direct = task.get("commands")
    if isinstance(direct, list):
        commands.extend(entry for entry in direct if isinstance(entry, dict))
    results = task.get("results")
    if isinstance(results, dict):
        for result in results.values():
            if isinstance(result, dict) and isinstance(result.get("commands"), list):
                commands.extend(entry for entry in result["commands"] if isinstance(entry, dict))
    return commands


def _action_for(entry: dict[str, Any]) -> str:
    return canonical_tool_name(str(entry.get("tool_name") or entry.get("action") or "").strip())


def _entry_failed(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or entry.get("tool_status") or "").lower()
    if status in FAILED_STATUSES:
        return True
    result = entry.get("result")
    if isinstance(result, dict):
        if result.get("error"):
            return True
        result_status = str(result.get("status") or "").lower()
        if result_status in FAILED_STATUSES:
            return True
        rc = result.get("returncode")
        if rc not in (None, 0, "0"):
            return True
    return False


def _entry_success(entry: dict[str, Any]) -> bool:
    if _entry_failed(entry):
        return False
    status = str(entry.get("status") or entry.get("tool_status") or "").lower()
    if status in SUCCESS_STATUSES:
        return True
    result = entry.get("result")
    if isinstance(result, dict):
        if result.get("status") in SUCCESS_STATUSES:
            return True
        if result.get("url") or result.get("window_found") or result.get("path"):
            return True
        if result.get("returncode") in (0, "0"):
            return True
    return bool(_action_for(entry))


def _failure_reason(task: dict[str, Any], failed_entries: list[dict[str, Any]]) -> str:
    if failed_entries:
        entry = failed_entries[-1]
        result = entry.get("result")
        if isinstance(result, dict):
            return str(result.get("error") or result.get("stderr") or result.get("status") or "tool failed")[:500]
        return str(entry.get("summary") or "tool failed")[:500]
    answer = str(task.get("answer") or "").strip()
    if answer:
        return answer[:500]
    return str(task.get("status") or "unknown")


def _last_successful_evidence(success_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not success_entries:
        return None
    entry = success_entries[-1]
    action = _action_for(entry)
    return {
        "step_id": entry.get("step_id"),
        "tool_name": action,
        "summary": entry.get("summary") or compact_tool_summary(action, entry.get("result"), entry.get("command", "")),
    }


def get_last_run_summary(
    user_id: int | None,
    chat_id: int | None = None,
    include_success: bool = True,
    exclude_task_id: str | None = None,
) -> dict[str, Any]:
    candidates = []
    for task in tasks.values():
        if user_id is not None and task.get("user_id") != user_id:
            continue
        if chat_id is not None and task.get("chat_id") != chat_id:
            continue
        if exclude_task_id and task.get("task_id") == exclude_task_id:
            continue
        candidates.append(task)
    candidates.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    if not include_success:
        candidates = [task for task in candidates if str(task.get("status") or "").lower() in FAILED_STATUSES]
    if not candidates:
        return {
            "status": "missing",
            "last_task_id": None,
            "used_tools": [],
            "failed_tools": [],
            "last_successful_evidence": None,
            "failure_reason": "No previous task found for this chat/user.",
            "partial_success_likely": False,
        }

    task = candidates[0]
    commands = _flatten_commands(task)
    used_tools = [_action_for(entry) for entry in commands if _action_for(entry)]
    failed_entries = [entry for entry in commands if _entry_failed(entry)]
    success_entries = [entry for entry in commands if _entry_success(entry)]
    task_status = str(task.get("status") or "unknown")
    partial_success_likely = bool(success_entries and (failed_entries or task_status.lower() in FAILED_STATUSES))
    return {
        "status": task_status,
        "last_task_id": task.get("task_id"),
        "chat_id": task.get("chat_id"),
        "used_tools": list(dict.fromkeys(used_tools)),
        "failed_tools": list(dict.fromkeys(_action_for(entry) for entry in failed_entries if _action_for(entry))),
        "last_successful_evidence": _last_successful_evidence(success_entries),
        "failure_reason": _failure_reason(task, failed_entries) if (failed_entries or task_status.lower() in FAILED_STATUSES) else "",
        "partial_success_likely": partial_success_likely,
        "answer": str(task.get("answer") or "")[:500],
    }
