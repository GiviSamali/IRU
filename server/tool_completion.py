from __future__ import annotations

import re
from typing import Any




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


SHELL_PROCESSES = {"explorer.exe", "explorer", "searchapp.exe", "shellexperiencehost.exe",
                   "startmenuexperiencehost.exe", "applicationframehost.exe"}
SHELL_WINDOW_TITLES = {"thumbnaildevicehelperwnd", "program manager", "msctfime ui", "default ime"}
SHELL_WINDOW_CLASSES = {"thumbnaildevicehelperwnd", "progman", "workerw", "shell_traywnd",
                        "applicationframewindow", "windows.ui.core.corewindow", "sysshadow",
                        "multitaskingviewframe", "xamlexplorerhostislandwindow"}
EXPLORER_WINDOW_CLASSES = {"cabinetwclass", "explorewclass"}
# A shell association for these executes code; it is never a document open.
UNSAFE_OPEN_SUFFIXES = {".lnk", ".url", ".hta", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                        ".bat", ".cmd", ".ps1", ".msi", ".reg", ".scr", ".pif", ".exe", ".com",
                        ".cpl", ".jar", ".application", ".gadget", ".cer", ".ins"}
DOCUMENT_SUFFIXES = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".txt", ".md",
                     ".csv", ".rtf", ".odt", ".ods", ".odp", ".png", ".jpg", ".jpeg", ".gif",
                     ".bmp", ".svg", ".webp", ".zip"}


def _normalized_path(value):
    from pathlib import PureWindowsPath
    text = str(value or "").strip().strip('"')
    return PureWindowsPath(text.replace('/', '\\')).as_posix().casefold() if text else ''


def object_open_proven(requested, observed, result):
    """An opened object is proven only by an independent observation of it.

    `requested` is the requirement; `observed.observed_path` must come from the
    agent's own observation (process table or shell API), never from the
    argument. A title match or an echoed path is not identity.
    """
    from pathlib import PureWindowsPath
    if not isinstance(requested, dict) or not isinstance(observed, dict):
        return False
    if result.get('object_open_verified') is not True:
        return False
    path = str(requested.get('path') or '')
    if not path:
        return False
    observed_path = str(observed.get('observed_path') or '')
    if not observed_path or _normalized_path(observed_path) != _normalized_path(path):
        return False
    kind = str(requested.get('type') or '')
    suffix = PureWindowsPath(path).suffix.casefold()
    if kind == 'file' and (suffix in UNSAFE_OPEN_SUFFIXES or suffix not in DOCUMENT_SUFFIXES):
        # The declared type is not trusted: the actual object must be a document.
        return False
    class_name = str(observed.get('class_name') or '').strip().casefold()
    if class_name in SHELL_WINDOW_CLASSES:
        return False
    if kind == 'folder':
        # A folder is proven by the path of one concrete Explorer window.
        if not str(observed.get('hwnd') or '').strip():
            return False
        if class_name not in EXPLORER_WINDOW_CLASSES:
            return False
    title = str(observed.get('title') or '').strip().casefold()
    if not title and kind != 'folder':
        return False
    if title in SHELL_WINDOW_TITLES:
        return False
    process = str(observed.get('process') or '').casefold()
    if process in SHELL_PROCESSES and kind != 'folder':
        return False
    return True


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
    requested = result.get('requested_object')
    if isinstance(requested, dict):
        return 'open' if object_open_proven(requested, result.get('observed_object'), result) else None
    if tool == 'app.launch':
        # A legacy receipt carries no object identity: the launch is a request,
        # never proof that a particular document was opened.
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
