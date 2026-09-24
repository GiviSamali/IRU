"""Bind a confirmation to one pending command and its permitted input channel."""
import uuid

try:
    from .api_support import is_deletion_command, is_command_safe, needs_confirmation
except ImportError:
    from api_support import is_deletion_command, is_command_safe, needs_confirmation


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
