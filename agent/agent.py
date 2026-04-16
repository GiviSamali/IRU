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
import sys
import base64
from pathlib import Path

# Для PyInstaller: определяем путь к exe, а не к временной папке
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_PATH = BASE_DIR / "config.json"


def load_config():
    """Загрузить конфигурацию агента."""
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data


def save_config(data: dict):
    """Сохранить конфигурацию агента."""
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ask_setup_gui(need_token: bool = True, need_device: bool = True) -> dict | None:
    """
    Окно первоначальной настройки: токен + имя устройства.
    Возвращает {"token": ..., "device_id": ...} или None (закрыли окно).
    """
    import tkinter as tk
    import re

    result = [None]

    root = tk.Tk()
    root.title("ИРУ — Первый запуск")
    root.configure(bg="#0a0e17")
    root.resizable(False, False)

    w, h = 420, 340
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    entry_style = dict(
        font=("Consolas", 12), width=36,
        bg="#141b2a", fg="#e2e8f0", insertbackground="#00d4ff",
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground="#1e293b", highlightcolor="#00d4ff",
    )

    tk.Label(
        root, text="ИРУ — Интеллектуальный Режим Управления",
        fg="#00d4ff", bg="#0a0e17", font=("Segoe UI", 11, "bold"),
    ).pack(pady=(20, 12))

    # ── Имя устройства ──
    tk.Label(
        root, text="Имя устройства (латиница, без пробелов):",
        fg="#94a3b8", bg="#0a0e17", font=("Segoe UI", 9),
    ).pack(anchor="w", padx=40)

    device_entry = tk.Entry(root, **entry_style)
    device_entry.insert(0, platform.node())  # hostname по умолчанию
    device_entry.pack(pady=(2, 8), ipady=5)
    device_entry.focus_set()

    # ── Токен ──
    tk.Label(
        root, text="Токен доступа (получить у администратора):",
        fg="#94a3b8", bg="#0a0e17", font=("Segoe UI", 9),
    ).pack(anchor="w", padx=40)

    token_entry = tk.Entry(root, **entry_style)
    token_entry.pack(pady=(2, 4), ipady=5)

    error_label = tk.Label(
        root, text="", fg="#ef4444", bg="#0a0e17", font=("Segoe UI", 9),
    )
    error_label.pack()

    def on_submit(event=None):
        dev = device_entry.get().strip()
        tok = token_entry.get().strip()

        if not dev:
            error_label.config(text="Введите имя устройства")
            return
        # Только латиница, цифры, дефис, подчёркивание
        if not re.match(r'^[A-Za-z0-9_\-]+$', dev):
            error_label.config(text="Имя: только латиница, цифры, - и _")
            return
        if not tok:
            error_label.config(text="Введите токен доступа")
            return

        result[0] = {"token": tok, "device_id": dev}
        root.destroy()

    def on_close():
        root.destroy()

    btn = tk.Button(
        root, text="Подключиться", command=on_submit,
        bg="#00d4ff", fg="#0a0e17", font=("Segoe UI", 10, "bold"),
        activebackground="#00b8d4", activeforeground="#0a0e17",
        relief="flat", cursor="hand2", padx=20, pady=4,
    )
    btn.pack(pady=8)

    # Явные биндинги Ctrl+V/C/A (на любой раскладке)
    def _paste(e):
        try:
            e.widget.event_generate('<<Paste>>')
        except Exception:
            pass
        return 'break'

    def _copy(e):
        try:
            e.widget.event_generate('<<Copy>>')
        except Exception:
            pass
        return 'break'

    def _select_all(e):
        e.widget.select_range(0, tk.END)
        return 'break'

    for entry_w in (device_entry, token_entry):
        entry_w.bind('<Control-v>', _paste)
        entry_w.bind('<Control-V>', _paste)
        entry_w.bind('<Control-м>', _paste)  # русская В
        entry_w.bind('<Control-c>', _copy)
        entry_w.bind('<Control-C>', _copy)
        entry_w.bind('<Control-с>', _copy)   # русская С
        entry_w.bind('<Control-a>', _select_all)
        entry_w.bind('<Control-A>', _select_all)
        entry_w.bind('<Control-ф>', _select_all)  # русская Ф

    root.bind("<Return>", on_submit)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

    return result[0]


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
    cfg = load_config()
    device_id = cfg["device_id"]
    server_url = cfg["server_url"]
    user_token = cfg.get("user_token", "")

    # Первый запуск: токен не задан — показать окно настройки
    if not user_token:
        setup = ask_setup_gui()
        if not setup:
            print("[agent] настройка отменена, выход")
            return
        cfg["user_token"] = setup["token"]
        cfg["device_id"] = setup["device_id"]
        save_config(cfg)
        user_token = setup["token"]
        device_id = setup["device_id"]
        print(f"[agent] настройки сохранены в {CONFIG_PATH}")

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
