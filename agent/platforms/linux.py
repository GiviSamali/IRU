"""
Linux-специфичные функции агента ИРУ.

Реализует тот же интерфейс что и windows.py.
Команды через /bin/bash, system info через /proc, lscpu, free, lspci.
"""
import os
import subprocess
from pathlib import Path

name = "Linux"


def execute_cmd(command: str, timeout: int = 30, shell: str = "auto") -> dict:
    """Выполнить команду через bash. shell параметр игнорируется на Linux."""
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["LANG"] = env.get("LANG", "C.UTF-8")

        result = subprocess.run(
            ["/bin/bash", "-c", command],
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


def get_desktop_path() -> str:
    """Путь к рабочему столу: xdg-user-dir → ~/Desktop → Home."""
    home = str(Path.home())

    # 1. xdg-user-dir — стандартный способ в freedesktop.org
    try:
        r = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True, encoding="utf-8", timeout=3
        )
        if r.returncode == 0 and r.stdout.strip():
            path = r.stdout.strip()
            if Path(path).exists():
                return path
    except Exception:
        pass

    # 2. Fallback каскад
    candidates = [
        Path(home) / "Desktop",
        Path(home) / "Рабочий стол",
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    return home


def get_username() -> str:
    return os.environ.get("USER", "") or os.environ.get("USERNAME", "")


def get_machine_guid() -> str:
    """Machine ID из /etc/machine-id (systemd) или /var/lib/dbus/machine-id."""
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        try:
            content = Path(path).read_text().strip()
            if content:
                return content
        except Exception:
            pass
    return ""


def _read(path: str) -> str:
    """Безопасное чтение файла."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _sh(cmd: str, timeout: int = 5) -> str:
    """Хелпер: выполнить команду и вернуть stdout."""
    try:
        r = subprocess.run(
            ["/bin/bash", "-c", cmd],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def _get_cpu() -> str:
    """Получить имя процессора."""
    # /proc/cpuinfo
    cpuinfo = _read("/proc/cpuinfo")
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    # Fallback через lscpu
    out = _sh("lscpu | grep 'Model name' | sed 's/Model name:[[:space:]]*//'")
    return out or "Unknown"


def _get_gpu() -> str:
    """Получить GPU через lspci."""
    out = _sh("lspci | grep -E 'VGA|3D|Display' | cut -d: -f3 | paste -sd '; '")
    return out.strip() if out else ""


def _get_ram_gb() -> float:
    """Получить общий объём RAM в ГБ."""
    meminfo = _read("/proc/meminfo")
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            try:
                kb = int(line.split()[1])
                return round(kb / (1024 * 1024), 1)
            except Exception:
                return 0
    return 0


def _get_disks() -> list:
    """Получить список дисков (смонтированные, не tmpfs)."""
    out = _sh("df -B1 --output=target,size,avail,fstype | tail -n +2")
    disks = []
    seen_targets = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        target, size, avail, fstype = parts[0], parts[1], parts[2], parts[3]
        # Пропустить псевдо-ФС
        if fstype in ("tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs"):
            continue
        if target in seen_targets:
            continue
        seen_targets.add(target)
        try:
            total_gb = round(int(size) / (1024**3), 1)
            free_gb = round(int(avail) / (1024**3), 1)
            if total_gb < 1:  # пропустить совсем мелкие
                continue
            disks.append({
                "drive": target,
                "total_gb": total_gb,
                "free_gb": free_gb,
            })
        except Exception:
            continue
    return disks


def get_system_info() -> dict:
    """Собрать информацию о Linux-системе."""
    return {
        "cpu": _get_cpu(),
        "gpu": _get_gpu(),
        "ram_gb": _get_ram_gb(),
        "disks": _get_disks(),
    }
