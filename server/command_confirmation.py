"""Bind a confirmation to one pending command and its permitted input channel."""
import uuid

try:
    from .api_support import is_deletion_command, is_command_safe, needs_confirmation
    from .tool_completion import execute_cmd_result_is_negative, execute_cmd_result_is_ok
except ImportError:
    from api_support import is_deletion_command, is_command_safe, needs_confirmation
    from tool_completion import execute_cmd_result_is_negative, execute_cmd_result_is_ok


def command_confirmation(data):
    result = dict(data)
    result["confirmation_id"] = uuid.uuid4().hex
    command = str(data.get("command") or "")
    risk = (data.get("params") or {}).get("risk")
    if is_deletion_command(command):
        kind = "deletion"
    elif not is_command_safe(command) or needs_confirmation(command) or risk not in (None, "", "low", "safe"):
        kind = "dangerous"
    else:
        kind = "command"
    result["kind"] = kind
    result["voice_allowed"] = kind == "command"
    result.pop("speech", None)
    if result["voice_allowed"]:
        result["speech"] = "Действие требует подтверждения. Подробности показаны в чате. Выполнить? Скажите да или нет."
    return result


def confirmed_command_outcome(result):
    """Command evidence only. Even success does not prove the original task's goal."""
    if not isinstance(result, dict):
        return "unknown"
    status = result.get("status")
    completion = result.get("completion_state")
    if status is not None and not isinstance(status, str) or completion is not None and not isinstance(completion, str):
        return "unknown"
    if status in {"failed", "error", "blocked", "cancelled"} or completion in {"failed", "error"}:
        return "failed"
    # Explicit uncertainty/launch-only receipts cannot prove execution.
    if status not in {None, "", "ok", "success", "done", "completed", "executed", "finished"}:
        return "unknown"
    if completion not in {None, "", "success"}:
        return "unknown"
    if result.get("error"):
        return "failed"
    code = result.get("returncode")
    if code is not None and type(code) is not int and code != "0":
        return "unknown"
    if execute_cmd_result_is_negative(result):
        return "failed"
    if type(result.get("returncode")) is not int and result.get("returncode") != "0":
        return "unknown"
    stdout = result.get("stdout")
    if not isinstance(stdout, str):
        return "unknown"
    return "success" if execute_cmd_result_is_ok(result) else "unknown"
