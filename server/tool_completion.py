from __future__ import annotations

import re
from typing import Any


TERMINAL_CORRECTION = (
    "You have sufficient current-run evidence. Call answer_text now. "
    "Do not call more tools unless the user explicitly requested deeper verification."
)


_EXECUTE_CMD_OUTCOME_RE = re.compile(r"(?im)^\s*(OK|NO|ERROR):")


def execute_cmd_outcome_marker(result: dict[str, Any] | None) -> str | None:
    """Return the first machine-readable execute_cmd marker from stdout."""
    if not isinstance(result, dict):
        return None
    stdout = str(result.get("stdout") or "")
    match = _EXECUTE_CMD_OUTCOME_RE.search(stdout)
    return match.group(1).upper() if match else None


def execute_cmd_returncode_is_zero(code: Any) -> bool:
    """Keep the existing legacy string zero; bool/float are not exit codes."""
    return (type(code) is int and code == 0) or (type(code) is str and code == "0")


def normalize_operation_result(tool_name: str, result: Any) -> str:
    """One operation outcome. Never establishes completion of a user goal."""
    if not isinstance(result, dict):
        return 'unknown'
    if result.get('dispatched') is False:
        return 'not_executed'
    status, completion = result.get('status'), result.get('completion_state')
    if status is not None and not isinstance(status, str):
        return 'unknown'
    if completion is not None and not isinstance(completion, str):
        return 'unknown'
    error = str(result.get('error') or '')
    if tool_name == 'write_content' and result.get('committed') is True and result.get('verified') is not True:
        return 'unknown'
    if tool_name == 'write_content' and not result.get('append') and result.get('mode') not in {'replace','replace_exact'}:
        intended = result.get('expected_content_sha256') or result.get('content_sha256')
        if intended and result.get('file_sha256') and intended != result['file_sha256']:
            return 'unknown'
    if result.get('returncode') == -1 and result.get('error'):
        return 'unknown'
    if status in {'unknown','disconnected'} or completion == 'unknown' or any(
            word in error.lower() for word in ('outcome_unknown','postcondition_unknown','agent_disconnected','timeout','timed out','таймаут')):
        return 'unknown'
    if tool_name=='execute_cmd' and (status in {'pending','started','launch_requested'} or completion=='partial_success'):
        return 'unknown'
    if status in {'partial','partial_success'} or completion == 'partial_success':
        return 'partial'
    if result.get('error') or status in {'error','failed','blocked','cancelled','missing','not_found'} or completion in {'error','failed'}:
        return 'failed'
    if status in {'pending','started','launch_requested','staged'}:
        return 'partial'
    code = result.get('returncode')
    if code is not None and not execute_cmd_returncode_is_zero(code):
        return 'failed' if type(code) is int else 'unknown'
    if tool_name == 'execute_cmd':
        return 'success' if (execute_cmd_returncode_is_zero(code) and isinstance(result.get('stdout'), str)
            and status in {None,'','ok','success','done','completed','executed','finished'}
            and completion in {None,'','success'}) else 'unknown'
    if tool_name == 'write_content':
        return 'success' if (result.get('verified') is True and result.get('committed') is True
            and isinstance(result.get('path'),str) and isinstance(result.get('file_sha256'),str)
            and re.fullmatch('[a-fA-F0-9]{64}',result['file_sha256'])) else 'partial'
    return 'success'


def operation_step_status(tool_name: str, result: Any, *, terminal=False) -> str:
    if terminal:
        return 'terminal'
    if isinstance(result,dict) and result.get('status') == 'skipped':
        return 'skipped'
    outcome = normalize_operation_result(tool_name, result)
    return 'failed' if outcome == 'not_executed' else outcome


def confirmed_window_effect(entry):
    """Read the existing native receipt; process launch alone is not an open window."""
    try:
        from .tool_registry import canonical_tool_name
    except ImportError:
        from tool_registry import canonical_tool_name
    tool = canonical_tool_name(entry.get('tool_name') or entry.get('action') or '')
    result = entry.get('result') or {}
    if (not isinstance(result,dict) or entry.get('status') == 'unknown'
            or normalize_operation_result(tool,result) != 'success'):
        return None
    if tool == 'window.control':
        return result.get('action') if result.get('completion_state') == 'success' else None
    if tool not in {'app.launch','app.open_url'} or result.get('status') == 'ambiguous':
        return None
    window = result.get('window')
    verified = result.get('status') in {'launched_verified','opened_verified'} or result.get('completion_state') == 'success'
    observed_window = (isinstance(window,dict) and bool(window) and window.get('visible') is not False
        or 'window' not in result and result.get('window_found') is True)
    if verified and observed_window and result.get('window_found') is not False:
        return 'open'
    return None


def execute_cmd_result_is_complete(result: dict[str, Any] | None) -> bool:
    return normalize_operation_result('execute_cmd', result) == 'success'


def execute_cmd_result_is_negative(result: dict[str, Any] | None) -> bool:
    return normalize_operation_result('execute_cmd', result) in {'failed','not_executed'}


def write_content_result_is_ok(result: dict[str, Any] | None) -> bool:
    return normalize_operation_result('write_content', result) == 'success'


def write_content_result_is_negative(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("status") in ("failed", "error"):
        return True
    return result.get("status") in ("missing", "not_found", "blocked", "cancelled")


def render_operation_answer(entry):
    """Render an operation receipt, without synthetic verification or goal status."""
    result=entry.get('result') if isinstance(entry.get('result'),dict) else {}
    text=result.get('summary') or result.get('stdout') or result.get('error') or entry.get('summary') or 'Результат операции сохранён.'
    return {'text':str(text),'answer_type':'result'}
