"""
agent.py — универсальный агент-исполнитель ИРУ v3.2

Подключается к серверу по WebSocket с токеном пользователя и выполняет команды:
- execute_cmd: выполнить команду в PowerShell/bash (с корректной кириллицей)
- list_dir: показать содержимое директории (для проводника UI)
- get_file_content: прочитать файл в base64 (для скачивания)
"""

import json
import asyncio
import subprocess
import websockets
import platform
import os
import base64
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    """Загрузить конфигурацию агента."""
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data["device_id"], data["server_url"], data.get("user_token", "")


def execute_cmd(command: str, timeout: int = 30, shell: str = "auto") -> dict:
    """
    Выполняет команду в PowerShell (Windows) или bash (Linux).
    Кириллица обрабатывается корректно через chcp 65001 и явную кодировку.
    """
    try:
        is_windows = platform.system() == "Windows"

        if shell == "auto":
            if is_windows:
                ps_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                shell_cmd = [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-Command", ps_prefix + command
                ]
            else:
                shell_cmd = ["bash", "-c", command]
        elif shell == "powershell":
            ps_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            shell_cmd = [
                "powershell", "-NoProfile", "-NonInteractive",
                "-Command", ps_prefix + command
            ]
        elif shell == "cmd":
            shell_cmd = ["cmd", "/c", f"chcp 65001 >nul & {command}"]
        else:
            shell_cmd = ["bash", "-c", command]

        env = os.environ.copy()
        if is_windows:
            env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"Таймаут: команда выполнялась дольше {timeout} сек"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}


def list_dir(path: str = None) -> dict:
    """Содержимое директории для проводника UI."""
    if not path:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "Рабочий стол"
        path = str(desktop) if desktop.exists() else str(Path.home())

    target = Path(path)
    if not target.exists():
        return {"error": f"Не найдено: {path}"}
    if not target.is_dir():
        return {"error": f"Не директория: {path}"}

    dirs_list = []
    files_list = []
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

    dirs_list.sort(key=lambda x: x["name"].lower())
    files_list.sort(key=lambda x: x["name"].lower())

    return {
        "path": str(target),
        "dirs": dirs_list,
        "files": files_list,
        "dirs_count": len(dirs_list),
        "files_count": len(files_list),
    }


def get_file_content(path: str, max_size: int = 50_000_000) -> dict:
    """Прочитать файл и вернуть содержимое в base64."""
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Файл не найден: {path}"}
        if not p.is_file():
            return {"error": f"Не файл: {path}"}
        if p.stat().st_size > max_size:
            return {"error": f"Файл слишком большой: {p.stat().st_size} байт (макс. {max_size})"}

        data = p.read_bytes()
        return {
            "filename": p.name,
            "size": len(data),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    except Exception as e:
        return {"error": str(e)}


# Реестр доступных действий
ACTIONS = {
    "execute_cmd": lambda **p: execute_cmd(**p),
    "list_dir": lambda **p: list_dir(**p),
    "get_file_content": lambda **p: get_file_content(**p),
}


async def run_agent():
    """Основной цикл агента: подключение к серверу и обработка команд."""
    device_id, server_url, user_token = load_config()

    # Добавить токен в URL
    ws_url = f"{server_url}/ws/{device_id}?user_token={user_token}"
    print(f"[agent] device={device_id}, connecting to {server_url}/ws/{device_id}")

    async for ws in websockets.connect(ws_url):
        try:
            print(f"[agent] connected")
            # Отправить информацию об устройстве
            await ws.send(json.dumps({
                "type": "register",
                "payload": {
                    "device_id": device_id,
                    "os": platform.system(),
                    "os_version": platform.version(),
                    "hostname": platform.node(),
                }
            }))

            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                if data.get("type") == "command":
                    cmd = data["payload"]
                    cmd_id = cmd["id"]
                    action_name = cmd["action"]
                    params = cmd.get("params", {})

                    print(f"[agent] executing: {action_name} | {params}")

                    try:
                        func = ACTIONS.get(action_name)
                        if func is None:
                            raise ValueError(f"Неизвестное действие: {action_name}")
                        result = func(**params)
                        payload = {"id": cmd_id, "status": "ok", "result": result}
                    except Exception as e:
                        payload = {"id": cmd_id, "status": "error", "error": str(e)}

                    await ws.send(json.dumps({"type": "result", "payload": payload}))

        except Exception as e:
            print(f"[agent] disconnected: {e}, reconnecting in 3s...")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run_agent())
