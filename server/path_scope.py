import re
from pathlib import PurePosixPath, PureWindowsPath


PATH_SCOPE_ERROR = "Путь относится к другому профилю пользователя или устройству. Используйте путь текущего устройства."
_WINDOWS_ABS_RE = re.compile(r"(?i)(?:^|[\s\"'(:])([a-z]:[\\/][^\s\"')]+)")
_LINUX_ABS_RE = re.compile(r"(?<![\w:])(/[A-Za-z0-9._~/-]+)")
_WINDOWS_PATH_MUTATION_RE = re.compile(
    r"(?i)\b(mkdir|md|new-item|ni|set-content|add-content|out-file|copy-item|move-item|xcopy|robocopy)\b"
    r"|\b(remove-item|rm|del|erase|rmdir|rd)\b"
    r"|::write(alltext|allbytes|alllines)|\bopen\s*\([^)]*,\s*['\"][wax]"
)


def contains_absolute_path(text: str | None) -> bool:
    value = text or ""
    return bool(_WINDOWS_ABS_RE.search(value) or _LINUX_ABS_RE.search(value))


def filter_memory_facts_for_device(facts: list[dict]) -> list[dict]:
    safe = []
    for fact in facts:
        text = fact.get("text") or fact.get("fact_text") or ""
        source = fact.get("source") or "user"
        if source != "device" and contains_absolute_path(text):
            continue
        safe.append(fact)
    return safe


def _norm(value: str | None) -> str:
    return (value or "").replace("\\", "/").rstrip("/").lower()


def infer_home_path(device_info: dict | None, profile: dict | None) -> str | None:
    data = {**(device_info or {}), **(profile or {})}
    for key in ("home_path", "home", "user_home", "user_profile"):
        if data.get(key):
            return str(data[key])
    username = data.get("username")
    desktop = data.get("desktop_path")
    if username and desktop:
        match = re.search(rf"(?i)^([a-z]:[\\/]+users[\\/]+{re.escape(str(username))})(?:[\\/]|$)", str(desktop))
        if match:
            return match.group(1)
        lower_desktop = str(desktop).replace("\\", "/").lower()
        suffix = f"/{str(username).lower()}/desktop"
        if lower_desktop.endswith(suffix):
            return str(desktop)[: -len("/Desktop")]
    return None


def build_target_device_block(device_id: str, device_info: dict | None, profile: dict | None) -> str:
    data = {**(device_info or {}), **(profile or {})}
    lines = [
        "## Target device context",
        f"device_id: {device_id}",
        f"hostname: {data.get('hostname') or 'unknown'}",
        f"os: {data.get('os') or 'unknown'} {data.get('os_version') or ''}".rstrip(),
    ]
    if data.get("username"):
        lines.append(f"username: {data['username']}")
    home_path = infer_home_path(device_info, profile)
    if home_path:
        lines.append(f"home_path: {home_path}")
    if data.get("desktop_path"):
        lines.append(f"desktop_path: {data['desktop_path']}")
    return "\n".join(lines)


def validate_write_path_for_device(path: str, device_info: dict | None, profile: dict | None) -> None:
    target = str(path or "").strip()
    if not target:
        return
    win_path = PureWindowsPath(target)
    parts = win_path.parts
    if len(parts) < 3 or parts[0].lower() != "c:\\" or parts[1].lower() != "users":
        return

    requested_user = parts[2].lower()
    data = {**(device_info or {}), **(profile or {})}
    username = str(data.get("username") or "").lower()
    home_path = _norm(infer_home_path(device_info, profile))
    target_norm = _norm(str(win_path))

    if username and requested_user == username:
        return
    if home_path and (target_norm == home_path or target_norm.startswith(home_path + "/")):
        return
    raise ValueError(PATH_SCOPE_ERROR)


def validate_execute_command_paths_for_device(command: str, device_info: dict | None, profile: dict | None) -> None:
    text = command or ""
    if not (_WINDOWS_PATH_MUTATION_RE.search(text) or ">" in text):
        return
    for match in _WINDOWS_ABS_RE.finditer(text):
        validate_write_path_for_device(match.group(1).rstrip(".,;"), device_info, profile)


def resolve_relative_preference(name: str, device_info: dict | None, profile: dict | None, *, prefer_desktop: bool = False) -> str:
    clean = str(PurePosixPath(str(name).replace("\\", "/"))).strip("/")
    if not clean or contains_absolute_path(clean) or clean.startswith("../") or "/../" in f"/{clean}/":
        raise ValueError("Preference must be a relative path name")
    data = {**(device_info or {}), **(profile or {})}
    base = data.get("desktop_path") if prefer_desktop else infer_home_path(device_info, profile) or data.get("desktop_path")
    if not base:
        raise ValueError("Target device home/desktop path is unknown")
    return str(PureWindowsPath(str(base), clean)) if "\\" in str(base) or ":" in str(base) else str(PurePosixPath(str(base), clean))
