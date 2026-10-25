from __future__ import annotations

from typing import Any
import json
import hashlib

try:
    from . import database as db
    from .runtime_state import tasks
    from .tool_registry import canonical_tool_name, compact_tool_summary
except ImportError:
    import database as db
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


def recovered_execution_summary(task: dict[str, Any], *, user_id=None) -> str:
    """Read existing receipts only; never infer goal completion or repeat an action."""
    items = []
    for entry in _flatten_commands(task):
        result = entry.get('result')
        if not isinstance(result, dict) or entry.get('status') != 'success' or result.get('error'):
            continue
        name = _action_for(entry)
        if name == 'remember_fact' and result.get('status') == 'ok' and type(result.get('fact_id')) is int:
            # A historical receipt proves the write, not that the fact still exists.
            owner = user_id if user_id is not None else task.get('user_id')
            with db.get_db() as c:
                fact = c.execute('SELECT fact_text FROM user_memory WHERE id=? AND user_id=?',
                                 (result['fact_id'], str(owner))).fetchone()
            if fact:
                items.append(f"Факт №{result['fact_id']} сохранён и присутствует в памяти: «{fact['fact_text']}».")
            else:
                items.append(f"Журнал подтверждает запись факта №{result['fact_id']}, но сейчас эта запись в памяти не найдена.")
        else:
            # Describe a completed tool step, never promote rc=0 to goal success.
            items.append(f"В журнале есть успешный шаг {entry.get('step_id', '')} ({name}); это не подтверждает выполнение всего поручения.")
    if not items:
        return 'Исход задачи не подтверждён. Сохранённые результаты не позволяют подтвердить выполненное действие. Повторное исполнение не запускал.'
    return '\n'.join(items[-4:]) + '\nИтог всего поручения не подтверждён. Повторное исполнение не запускал.'


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
    source_task_id: str | None = None,
    report_offset: int = 0,
    allowed_devices: set[str] | None = None,
    source_message_ids: list[int] | None = None,
    source_message_range: dict | None = None,
    expected_source_sha256: str | None = None,
    summary_archive_offset: int | None = None,
    source_summary_id: str | None = None,
) -> dict[str, Any]:
    if summary_archive_offset is not None or source_summary_id is not None:
        try:from .context_history import summary_archive
        except ImportError:from context_history import summary_archive
        return summary_archive(user_id,chat_id,summary_archive_offset if summary_archive_offset is not None else 0,
            summary_id=source_summary_id,report_offset=report_offset,expected_sha256=expected_source_sha256)
    if source_message_ids is not None or source_message_range is not None:
        try:from .context_history import resolve_sources
        except ImportError:from context_history import resolve_sources
        return resolve_sources(user_id,chat_id,source_message_ids,report_offset,message_range=source_message_range,expected_sha256=expected_source_sha256)
    if source_task_id:
        # Explicit persisted source: ownership/chat/device boundaries precede all content access.
        try:
            from .worker_scheduler import owned_job, restore_task
            from .response_presentation import saved_step_reports
        except ImportError:
            from worker_scheduler import owned_job, restore_task
            from response_presentation import saved_step_reports
        row = owned_job(source_task_id, user_id) if user_id is not None else None
        if not row or chat_id is None or row['chat_id'] != chat_id or source_task_id == exclude_task_id:
            return {"status": "blocked", "error": "source_task_not_owned_or_in_chat"}
        consumer = owned_job(exclude_task_id, user_id) if exclude_task_id else None
        if consumer and json.loads(consumer['payload']).get('goal_id'):
            try:
                from .worker_scheduler import pinned_results, _result_json
            except ImportError:
                from worker_scheduler import pinned_results, _result_json
            if consumer['chat_id'] != chat_id:
                return {"status":"blocked", "error":"dependency_consumer_not_owned"}
            consumer_payload = json.loads(consumer['payload'])
            device_ids = consumer_payload.get('device_ids') or []
            allowed = {did.split(':')[-1] for did in device_ids} or {'server'}
            if not allowed_devices or not allowed.intersection(allowed_devices):
                return {"status":"blocked", "error":"source_device_out_of_scope"}
            try:
                pinned = next((item for item in pinned_results(exclude_task_id, user_id, chat_id, device_ids)
                               if item['data']['task_id'] == source_task_id), None)
            except ValueError as exc:
                return {"status":"blocked", "error":str(exc)}
            if pinned:
                text = _result_json(pinned['data'])
                if hashlib.sha256(text.encode('utf-8')).hexdigest() != pinned['sha256']:
                    return {"status":"blocked", "error":"dependency_result_corrupt"}
                if type(report_offset) is not int or not 0 <= report_offset < len(text):
                    return {"status":"blocked", "error":"invalid_result_offset"}
                end = min(len(text), report_offset + 4000)
                live = tasks.get(exclude_task_id)
                if live and live.get('user_id') == user_id and live.get('chat_id') == chat_id:
                    progress = live.setdefault('_pinned_read_offsets', {})
                    if report_offset <= progress.get(pinned['result_id'], 0):
                        progress[pinned['result_id']] = max(end, progress.get(pinned['result_id'], 0))
                return {"status":"success", "source_task_id":source_task_id,
                    "result_id":pinned['result_id'], "sha256":pinned['sha256'], "schema_version":1,
                    "historical_material_only":True, "current_execution_evidence":False,
                    "representation":"immutable_result_json_page", "report_text":text[report_offset:end],
                    "report_offset":report_offset, "next_offset":end if end < len(text) else None,
                    "total_chars":len(text)}
        source = restore_task(row)
        report = source.get('worker_report') or {}
        if not allowed_devices or not report.get('target_device_ids') or not set(report['target_device_ids']).issubset(allowed_devices):
            return {"status": "blocked", "error": "source_device_out_of_scope"}
        saved = saved_step_reports(source)
        text = '\n\n'.join(r['text'] for r in saved)
        offset = max(0, report_offset)
        end = min(len(text), offset + 4000)
        return {"status": "success", "source_status": row['state'], "source_task_id": source_task_id,
                "last_task_id": source_task_id,
                "historical_material_only": True, "current_execution_evidence": False,
                "report_text": text[offset:end], "report_offset": offset,
                "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "saved_report_refs": [r['step_id'] for r in saved]}
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
