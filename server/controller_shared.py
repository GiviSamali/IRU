import ntpath
import posixpath
import re
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from . import database as db  # type: ignore
    from .path_scope import build_target_device_block, filter_memory_facts_for_device  # type: ignore
except ImportError:
    import database as db  # type: ignore
    from path_scope import build_target_device_block, filter_memory_facts_for_device  # type: ignore


_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
]

MAX_MEMORY_BLOCK = 2048
LIVE_SNAPSHOT_CONTAINER_KEYS = ("live_snapshot", "current_state", "state_snapshot")
LIVE_SNAPSHOT_FIELDS = (
    "cpu_usage",
    "cpu_percent",
    "ram_usage",
    "memory_usage",
    "disk_usage",
    "process_count",
    "processes",
    "load",
    "uptime",
)

ONBOARDING_MARKERS = [
    "нет подключённых устройств",
    "нет подключенных устройств",
    "подключить устройство",
    "запустить IruAgent.exe",
    "скачать agent",
    "список доступных устройств пуст",
]

DENY_CONFIRM_MARKERS = [
    "команда отменена пользователем",
]


ARTIFACT_RESULT_PATH_KEYS = (
    "artifacts_created",
    "created_artifacts",
    "created_files",
    "files_created",
    "files_verified",
    "verified_files",
    "file_path",
    "path",
)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _looks_like_artifact_path(path: str) -> bool:
    text = (path or "").strip()
    if not text:
        return False
    if text.startswith(("ctx://", "http://", "https://")):
        return False
    return bool(re.search(r"[/\\]", text) or re.match(r"^[A-Za-z]:", text))


def _path_parent(path: str) -> str:
    module = ntpath if re.match(r"^[A-Za-z]:", path) or "\\" in path else posixpath
    parent = module.dirname(path.rstrip("\\/"))
    return parent or path


def _path_basename(path: str) -> str:
    module = ntpath if re.match(r"^[A-Za-z]:", path) or "\\" in path else posixpath
    return module.basename(path.rstrip("\\/"))


def _common_parent(paths: list[str]) -> str | None:
    if not paths:
        return None
    parents = [_path_parent(path) for path in paths if path]
    if not parents:
        return None
    module = ntpath if any(re.match(r"^[A-Za-z]:", path) or "\\" in path for path in parents) else posixpath
    try:
        return module.commonpath(parents)
    except Exception:
        return parents[0]


def _iter_command_artifact_paths(command: dict):
    result = command.get("result") if isinstance(command, dict) else None
    if not isinstance(result, dict):
        return
    for key in ARTIFACT_RESULT_PATH_KEYS:
        for item in _as_list(result.get(key)):
            if isinstance(item, str) and _looks_like_artifact_path(item):
                yield item.strip()


def build_recent_artifact_context(chat_history: list[dict] | None, *, max_files: int = 12) -> dict:
    """Extract exact artifact paths from previous assistant tool results."""
    files = []
    seen = set()
    source_message_id = None
    for message in reversed(list(chat_history or [])):
        if message.get("role") != "assistant":
            continue
        commands = message.get("commands") or []
        if not isinstance(commands, list):
            continue
        for command in commands:
            if not isinstance(command, dict):
                continue
            for path in _iter_command_artifact_paths(command):
                key = path.lower()
                if key in seen:
                    continue
                seen.add(key)
                files.append({
                    "path": path,
                    "step_id": command.get("step_id"),
                    "tool_name": command.get("tool_name") or command.get("action"),
                })
                source_message_id = source_message_id or message.get("id")
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
        if files:
            break
    paths = [item["path"] for item in files]
    return {
        "project_path": _common_parent(paths),
        "created_files": files,
        "source_message_id": source_message_id,
    }


def format_recent_artifact_context_block(context: dict | None) -> str:
    context = context or {}
    files = context.get("created_files") if isinstance(context.get("created_files"), list) else []
    lines = [
        "Recent artifact context from previous assistant tool results:",
        "This is context only, not current-run evidence.",
        f"project_path: {context.get('project_path') or 'null'}",
    ]
    if files:
        lines.append("created_files:")
        for item in files[:12]:
            step = item.get("step_id") or "unknown_step"
            tool = item.get("tool_name") or "unknown_tool"
            lines.append(f"- {item.get('path')} (source_step_id={step}; tool={tool})")
    else:
        lines.append("created_files: []")
    lines.extend([
        "Open/use-created-files rule:",
        "If the user asks to open, verify, show, or continue work with recently created files, use the exact paths above.",
        "Do not recursively scan Desktop or another broad parent when exact created_files are known.",
        "If exact files are missing but project_path is known, search only inside project_path and prefer non-recursive checks.",
        "If neither exact files nor project_path are known, ask clarification instead of broad recursive search.",
        "Claims about opened/verified files still require current-run tool results and answer_text basis from current-run step_ids.",
    ])
    return "\n".join(lines)


def broad_desktop_scan_error(command: str, context: dict | None) -> str | None:
    """Block broad recursive Desktop scans when exact recent artifacts are known."""
    context = context or {}
    files = context.get("created_files") if isinstance(context.get("created_files"), list) else []
    if not files:
        return None
    text = (command or "").lower().replace("/", "\\")
    if not text:
        return None
    recursive = "-recurse" in text or "os.walk" in text or "\\**\\" in text or "/**/" in text
    if not recursive or "desktop" not in text:
        return None

    project_path = str(context.get("project_path") or "")
    project_norm = project_path.lower().replace("/", "\\")
    project_name = _path_basename(project_path).lower()
    if project_norm and project_norm in text:
        return None
    if project_name and project_name in text:
        return None

    return (
        "Broad recursive Desktop scan is not allowed when recent created_files are known. "
        "Use the exact created_files paths from recent artifact context, or search only inside project_path."
    )


def current_datetime_msk() -> str:
    """Текущая дата/время в московской таймзоне, на русском языке."""
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    weekday = _WEEKDAYS_RU[now.weekday()]
    month = _MONTHS_RU[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year}, {now.strftime('%H:%M')} MSK"


def strip_markdown(text: str) -> str:
    """Убрать markdown-разметку из текста LLM перед отправкой пользователю."""
    if not text:
        return text
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: m.group(1).upper(), text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^(\s*)[-*]\s+", r"\1— ", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text


def collect_tasks(task_ids: list[int]) -> list[dict]:
    """Подгрузить текущее состояние задач для ответа UI."""
    result = []
    for tid in task_ids:
        try:
            task = db.get_task(tid)
            if task:
                result.append({
                    "id": task["id"],
                    "goal": task["goal"],
                    "status": task["status"],
                    "steps": [
                        {
                            "idx": step["idx"],
                            "description": step["description"],
                            "status": step["status"],
                            "summary": step.get("summary"),
                        }
                        for step in task["steps"]
                    ],
                })
        except Exception as exc:
            print(f"[llm] collect_tasks error for task {tid}: {exc}")
    return result


def set_current_step(poll_task_id: str | None, text: str) -> None:
    """Обновить task.current_step для отображения live-прогресса в UI."""
    if not poll_task_id:
        return
    try:
        try:
            from .runtime_state import tasks  # type: ignore
        except ImportError:
            from runtime_state import tasks  # type: ignore

        task = tasks.get(poll_task_id)
        if task:
            task["current_step"] = text
    except Exception:
        pass


def push_tasks_view(poll_task_id: str | None, task_ids: list[int]) -> None:
    """Обновить task['tasks'] для live-отображения шагов плана в UI."""
    if not poll_task_id or not task_ids:
        return
    try:
        try:
            from .runtime_state import tasks  # type: ignore
        except ImportError:
            from runtime_state import tasks  # type: ignore

        task = tasks.get(poll_task_id)
        if task:
            task["tasks"] = collect_tasks(task_ids)
    except Exception:
        pass


class ConfirmationRequired(Exception):
    """Команда требует подтверждения пользователя."""

    def __init__(self, command: str, device_id: str, params: dict, answer: str, commands_log: list):
        self.command = command
        self.device_id = device_id
        self.params = params
        self.answer = answer
        self.commands_log = commands_log
        super().__init__(f"Подтверждение: {command[:80]}")


def _device_online(dev: dict) -> str:
    if not isinstance(dev, dict):
        return "unknown"
    status = str(dev.get("status") or dev.get("connection_status") or "").strip().lower()
    if status in {"offline", "disconnected"}:
        return "no"
    if dev.get("ws") is not None or status in {"online", "connected", "ok"}:
        return "yes"
    return "yes"


def _live_snapshot_for_device(device_id: str, dev: dict) -> tuple[dict | None, str]:
    if not isinstance(dev, dict):
        return None, "missing"
    candidates = [dev]
    info = dev.get("info")
    if isinstance(info, dict):
        candidates.append(info)
    for container in candidates:
        for key in LIVE_SNAPSHOT_CONTAINER_KEYS:
            snapshot = container.get(key)
            if isinstance(snapshot, dict) and snapshot:
                source_device_id = str(snapshot.get("source_device_id") or snapshot.get("device_id") or device_id)
                if source_device_id != device_id:
                    return None, f"invalid_source:{source_device_id}"
                normalized = dict(snapshot)
                normalized["source_device_id"] = source_device_id
                return normalized, "present"
    return None, "missing"


def _live_snapshot_summary(snapshot: dict) -> str:
    parts = []
    collected_at = snapshot.get("collected_at") or snapshot.get("timestamp")
    if collected_at:
        parts.append(f"collected_at={collected_at}")
    parts.append(f"source_device_id={snapshot.get('source_device_id')}")
    for key in LIVE_SNAPSHOT_FIELDS:
        if key in snapshot and snapshot.get(key) is not None:
            parts.append(f"{key}={snapshot.get(key)}")
    return ", ".join(parts)


def build_devices_block(all_devices: dict) -> str:
    """Build inventory separately from per-device live state."""
    if not all_devices:
        return "No connected devices."

    lines = [
        "Device inventory. Live state is separate and must be grounded per device_id.",
        "Every device state fact must include device_id/source. Do not copy CPU/RAM/disk/process/load between devices.",
    ]
    for did, dev in all_devices.items():
        info = dev.get("info", {}) if isinstance(dev, dict) else {}
        hostname = info.get("hostname", "?")
        os_name = info.get("os", "?")
        os_ver = info.get("os_version", "")
        snapshot, snapshot_status = _live_snapshot_for_device(did, dev)
        profile_hint = "available" if any(info.get(key) for key in ("cpu", "gpu", "ram_gb", "disks")) else "missing"
        if snapshot:
            state_part = f"live_snapshot=present, {_live_snapshot_summary(snapshot)}"
        else:
            state_part = f"live_snapshot={snapshot_status}, state=fresh state unavailable"
        lines.append(
            f"- device_id={did}; hostname={hostname}; os={os_name} ({os_ver}); "
            f"online={_device_online(dev)}; {state_part}; cached_profile={profile_hint}"
        )
    return "\n".join(lines)


def build_device_profile_block(profile: dict | None) -> str:
    """Build cached profile context; this is not a live state snapshot."""
    if not profile:
        return ""

    lines = [
        "\n## Cached device profile",
        "source=cached_profile; not live current state; do not describe as current device state.",
    ]
    for key in ("username", "desktop_path", "home_path", "home", "machine_guid", "updated_at"):
        if profile.get(key):
            lines.append(f"cached_{key}: {profile.get(key)}")
    for key in ("cpu", "gpu", "ram_gb"):
        if profile.get(key):
            lines.append(f"cached_{key}: {profile.get(key)}")
    disks = profile.get("disks")
    if disks and isinstance(disks, list):
        disk_lines = []
        for disk in disks:
            drive = disk.get("drive", "?")
            total = disk.get("total_gb", 0)
            free = disk.get("free_gb", 0)
            disk_lines.append(f"{drive} {total} GB total, {free} GB free")
        lines.append(f"cached_disks: {'; '.join(disk_lines)}")
    return "\n".join(lines)


def build_memory_block(machine_guid: str | None, user_id: str | None = None) -> str:
    """Собрать блок памяти для промпта (≤2048 символов)."""
    if not machine_guid and not user_id:
        return ""

    memory_stats = db.get_memory_stats(machine_guid, user_id)
    memory_facts = filter_memory_facts_for_device(memory_stats.get("facts_list", []))
    commands = db.get_recent_commands(machine_guid, user_id, 20) if machine_guid else []

    if not memory_facts and not commands:
        return ""

    facts_lines = []
    if memory_facts:
        facts_lines.append("Факты обо мне, пользователе:")
        for fact in memory_facts:
            category = f"[{fact['category']}] " if fact.get("category") else ""
            source = fact.get("source") or "user"
            facts_lines.append(f"- source={source} id={fact['id']} {category}{fact.get('text', '')}")

    def command_line(command: dict, preview_limit: int) -> str:
        tag = "[OK]" if command["success"] else "[FAIL]"
        intent_part = f" — (intent: {command['intent']})" if command.get("intent") else ""
        if command["success"]:
            preview = (command.get("stdout_preview") or "")[:preview_limit]
            output_part = f" — stdout: {preview}" if preview else ""
        else:
            preview = (command.get("stderr_preview") or "")[:preview_limit]
            output_part = f" — stderr: {preview}" if preview else ""
        return f"- {tag} {command['command']} — exit={command['exit_code']}{intent_part}{output_part}"

    def assemble(command_lines: list[str]) -> str:
        parts = ["## Память", ""]
        if facts_lines:
            parts.extend(facts_lines)
            parts.append("")
        if command_lines:
            parts.append("Последние команды на этом устройстве:")
            parts.extend(command_lines)
        return "\n".join(parts) + "\n"

    command_lines = [command_line(command, 200) for command in commands]
    block = assemble(command_lines)
    if len(block) <= MAX_MEMORY_BLOCK:
        return block

    command_lines = [command_line(command, 100) for command in commands]
    block = assemble(command_lines)
    if len(block) <= MAX_MEMORY_BLOCK:
        return block

    while command_lines:
        command_lines.pop()
        block = assemble(command_lines)
        if len(block) <= MAX_MEMORY_BLOCK:
            return block

    return assemble([])


def is_onboarding_message(content: str) -> bool:
    """Проверить, является ли сообщение онбординговым."""
    lower = content.lower()
    return sum(1 for marker in ONBOARDING_MARKERS if marker in lower) >= 2


def is_transient_confirmation_message(content: str) -> bool:
    """Проверить, является ли сообщение временным следом confirm/deny flow."""
    lower = (content or "").strip().lower()
    return any(marker == lower or marker == lower.rstrip(".") for marker in DENY_CONFIRM_MARKERS)


def build_chat_messages(chat_history: list[dict], filter_onboarding: bool = False) -> list[dict]:
    """
    Конвертировать историю чата в формат messages для API.
    Только role='user' и role='assistant', без tool-вызовов из прошлых сессий.
    """
    messages = []
    for msg in chat_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content or role not in ("user", "assistant"):
            continue
        if filter_onboarding and role == "assistant" and is_onboarding_message(content):
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            continue
        if role == "assistant" and is_transient_confirmation_message(content):
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            continue
        messages.append({"role": role, "content": content})
    return messages
