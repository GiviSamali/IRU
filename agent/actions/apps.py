# agent/actions/apps.py
import subprocess
import os
from typing import Dict

# сюда впишешь свои реальные пути
KNOWN_APPS: Dict[str, str] = {
    "Steam": r"C:\Program Files (x86)\Steam\Steam.exe",
    "Chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    # "PyCharm": r"...",
    # "VSCode": r"...",
}


def open_app(name: str) -> dict:
    path = KNOWN_APPS.get(name)
    if not path:
        raise ValueError(f"Unknown app: {name}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path not found: {path}")
    subprocess.Popen([path], shell=False)
    return {"message": f"{name} started", "path": path}
