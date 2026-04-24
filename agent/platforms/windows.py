"""
Windows-специфичные функции агента ИРУ.

Реализует интерфейс platforms:
- name
- execute_cmd(command, timeout, shell)
- get_desktop_path()
- get_username()
- get_machine_guid()
- get_system_info()
"""
import os
import platform as _platform
import subprocess
from pathlib import Path

name = "Windows"


def execute_cmd(command: str, timeout: int = 30, shell: str = "auto") -> dict:
    """Выполнить команду. По умолчанию — PowerShell.
    Значения shell: auto | powershell | cmd."""
    try:
        ps_prefix = (
            "chcp 65001 > $null; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        )

        if shell == "cmd":
            shell_cmd = ["cmd", "/c", f"chcp 65001 >nul & {command}"]
        else:
            # auto и powershell — оба идут через PowerShell
            shell_cmd = [
                "powershell", "-NoProfile", "-NonInteractive",
                "-Command", ps_prefix + command
            ]

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        # Скрыть мелькающее окно PowerShell/cmd
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE

        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
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


def get_desktop_path() -> str:
    """Путь к рабочему столу: winreg → OneDrive → Desktop → Home."""
    home = str(Path.home())

    # 1. Реестр — самый надёжный источник
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        ) as key:
            desktop_reg = winreg.QueryValueEx(key, "Desktop")[0]
            if desktop_reg and Path(desktop_reg).exists():
                return desktop_reg
    except Exception:
        pass

    # 2. Fallback каскад
    candidates = [
        Path(home) / "OneDrive" / "Desktop",
        Path(home) / "OneDrive" / "Рабочий стол",
        Path(home) / "Desktop",
        Path(home) / "Рабочий стол",
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    return home


def get_username() -> str:
    return os.environ.get("USERNAME", "") or os.environ.get("USER", "")


def get_machine_guid() -> str:
    """Machine GUID из реестра."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        ) as key:
            return winreg.QueryValueEx(key, "MachineGuid")[0] or ""
    except Exception:
        return ""


def _ps(cmd: str, timeout: int = 10) -> str:
    """Хелпер: выполнить PowerShell команду и вернуть stdout."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def get_system_info() -> dict:
    """Собрать информацию о системе через WMI/PowerShell."""
    info = {
        "cpu": _ps("(Get-CimInstance Win32_Processor).Name"),
        "gpu": _ps("(Get-CimInstance Win32_VideoController).Name -join '; '"),
        "ram_gb": 0,
        "disks": [],
    }

    # RAM
    ram_str = _ps(
        "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)"
    )
    try:
        info["ram_gb"] = float(ram_str) if ram_str else 0
    except ValueError:
        info["ram_gb"] = 0

    # Диски
    disks_raw = _ps(
        'Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | '
        "ForEach-Object { $_.DeviceID + '|' + "
        "[math]::Round($_.Size/1GB,1).ToString() + '|' + "
        "[math]::Round($_.FreeSpace/1GB,1).ToString() }"
    )
    if disks_raw:
        for line in disks_raw.splitlines():
            parts = line.strip().split("|")
            if len(parts) == 3:
                try:
                    info["disks"].append({
                        "drive": parts[0],
                        "total_gb": float(parts[1]),
                        "free_gb": float(parts[2]),
                    })
                except ValueError:
                    pass

    return info
