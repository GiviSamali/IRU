"""Bind a spoken deletion decision to one pending command."""
import uuid

try:
    from .api_support import is_deletion_command
except ImportError:
    from api_support import is_deletion_command


def command_confirmation(data):
    result = dict(data)
    result["confirmation_id"] = uuid.uuid4().hex
    result["kind"] = "deletion" if is_deletion_command(str(data.get("command") or "")) else "command"
    if result["kind"] == "deletion":
        result["speech"] = ("Команда включает удаление файлов или папок. Полная команда показана в чате. "
                            "Разрешить выполнение этой команды? Скажите да или нет.")
    return result
