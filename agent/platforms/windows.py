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
import ctypes
import string
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


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('sullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


def _get_ram_gb() -> int:
    """Получить объём ОЗУ через kernel32.GlobalMemoryStatusEx."""
    try:
        mem = _MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        return round(mem.ullTotalPhys / (1024 ** 3))
    except Exception:
        return 0


def _get_disks_info() -> list:
    """Получить список фиксированных дисков через kernel32."""
    try:
        drives = []
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
                if drive_type == 3:  # DRIVE_FIXED
                    free_bytes = ctypes.c_ulonglong(0)
                    total_bytes = ctypes.c_ulonglong(0)
                    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                        drive, None, ctypes.byref(total_bytes), ctypes.byref(free_bytes)
                    )
                    drives.append({
                        "drive": f"{letter}:",
                        "total_gb": round(total_bytes.value / (1024 ** 3)),
                        "free_gb": round(free_bytes.value / (1024 ** 3)),
                    })
            bitmask >>= 1
        return drives
    except Exception:
        return []


def get_system_info() -> dict:
    """Собрать информацию о системе: CPU/GPU через PowerShell, RAM/диски через ctypes."""
    info = {
        "cpu": _ps("(Get-CimInstance Win32_Processor).Name"),
        "gpu": _ps("(Get-CimInstance Win32_VideoController).Name -join '; '"),
        "ram_gb": _get_ram_gb(),
        "disks": _get_disks_info(),
    }
    return info
