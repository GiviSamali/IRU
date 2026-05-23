from __future__ import annotations

import base64
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from platforms import get_platform

platform_mod = get_platform()

WS_FILE_DOWNLOAD_MAX_BYTES = 5 * 1024 * 1024

IRU_DIRS = (
    "agent",
    "runtime/python",
    "runtime/venv",
    "runtime/project_envs",
    "cache",
    "scripts",
    "tools",
    "logs",
    "traces",
    "artifacts",
    "state",
)


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


def _iru_home() -> Path:
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return (Path(root) if root else Path.home() / "AppData" / "Local") / "IRU"
    return Path.home() / ".iru"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _activation_paths(home: Path) -> dict:
    return {
        "iru_home": str(home),
        "cache": str(home / "cache"),
        "scripts": str(home / "scripts"),
        "tools": str(home / "tools"),
        "logs": str(home / "logs"),
        "traces": str(home / "traces"),
        "artifacts": str(home / "artifacts"),
        "state": str(home / "state"),
    }


def _activation_identity(device_id: str) -> dict:
    info = collect_system_info(device_id=device_id)
    return {
        "hostname": info.get("hostname") or platform.node(),
        "computer_name": os.environ.get("COMPUTERNAME", ""),
        "machine_guid": info.get("machine_guid") or "",
        "user": info.get("username") or getpass.getuser(),
        "os": info.get("os") or platform.system(),
        "os_build": info.get("os_version") or platform.version(),
    }


def _runtime_receipt(home: Path) -> dict:
    managed_python = home / "runtime" / "python" / ("python.exe" if sys.platform == "win32" else "bin/python")
    venv_python = _runtime_venv_python(home)
    python_path = managed_python if managed_python.exists() else None
    status = "ok" if python_path else "missing"
    return {
        "managed_python_status": status,
        "python_path": str(python_path) if python_path else None,
        "venv_path": str(_runtime_venv_path(home)) if venv_python.exists() else None,
        "venv_python": str(venv_python) if venv_python.exists() else None,
        "python_version": None,
        "pip_status": "unknown" if python_path else "missing",
    }


def _runtime_home(home: Path) -> Path:
    return home / "runtime"


def _runtime_venv_path(home: Path) -> Path:
    return _runtime_home(home) / "venv"


def _runtime_venv_python(home: Path) -> Path:
    if sys.platform == "win32":
        return _runtime_venv_path(home) / "Scripts" / "python.exe"
    return _runtime_venv_path(home) / "bin" / "python"


def _runtime_pip_path(home: Path) -> Path:
    if sys.platform == "win32":
        return _runtime_venv_path(home) / "Scripts" / "pip.exe"
    return _runtime_venv_path(home) / "bin" / "pip"


def _run_python(args: list[str], timeout: int = 45) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _find_base_python() -> str | None:
    candidates = [sys.executable, shutil.which("python3"), shutil.which("python"), shutil.which("py")]
    for candidate in candidates:
        if not candidate:
            continue
        cmd = [candidate, "-3", "-c", "import sys; print(sys.executable)"] if Path(candidate).name.lower() == "py.exe" else [candidate, "-c", "import sys; print(sys.executable)"]
        rc, out, _ = _run_python(cmd, timeout=10)
        path = out.splitlines()[0].strip() if out else candidate
        if rc == 0 and path and "WindowsApps" not in path:
            return candidate
    return None


def _python_version(python_path: Path | str) -> str:
    rc, out, _ = _run_python([str(python_path), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], timeout=15)
    return out.splitlines()[0].strip() if rc == 0 and out else ""


def _pip_info(venv_python: Path) -> dict:
    rc, out, err = _run_python([str(venv_python), "-m", "pip", "--version"], timeout=20)
    if rc == 0:
        parts = out.split()
        return {"status": "ok", "version": parts[1] if len(parts) > 1 else out}
    text = (out + "\n" + err).strip()
    if "No module named pip" in text:
        return {"status": "missing", "version": ""}
    return {"status": "broken", "version": ""}


def _check_packages(venv_python: Path, packages: list[str]) -> dict:
    checked = [str(pkg).strip() for pkg in packages or [] if str(pkg).strip()]
    result = {"checked": checked, "installed": [], "missing": [], "failed": []}
    for package in checked:
        probe = (
            "import importlib.util,sys; "
            f"name={package!r}; "
            "sys.exit(0 if importlib.util.find_spec(name) else 1)"
        )
        rc, _, err = _run_python([str(venv_python), "-c", probe], timeout=15)
        if rc == 0:
            result["installed"].append(package)
        elif err:
            result["failed"].append({"name": package, "error": err[:240]})
        else:
            result["missing"].append(package)
    return result


def _runtime_paths(home: Path) -> dict:
    runtime_home = _runtime_home(home)
    return {
        "iru_home": str(home),
        "runtime_home": str(runtime_home),
        "venv_path": str(_runtime_venv_path(home)),
        "venv_python": str(_runtime_venv_python(home)) if _runtime_venv_python(home).exists() else "",
        "pip_path": str(_runtime_pip_path(home)) if _runtime_pip_path(home).exists() else "",
    }


def _runtime_receipt_v1(
    *,
    home: Path,
    mode: str,
    device_id: str,
    status: str,
    base_python: str | None,
    packages: dict,
    warnings: list[str],
    next_actions: list[str],
    stage: str = "completed",
) -> dict:
    paths = _runtime_paths(home)
    venv_python = Path(paths["venv_python"]) if paths.get("venv_python") else _runtime_venv_python(home)
    venv_version = _python_version(venv_python) if venv_python.exists() else ""
    base_version = _python_version(base_python) if base_python else ""
    pip = _pip_info(venv_python) if venv_python.exists() else {"status": "missing", "version": ""}
    health = {
        "runtime": "ok" if status == "ok" else ("warning" if status == "degraded" else "error"),
        "venv": "ok" if venv_python.exists() else ("missing" if status in {"missing", "install_required"} else "broken"),
        "pip": pip["status"],
    }
    if status == "ok" and pip["status"] != "ok":
        status = "degraded"
        health["runtime"] = "warning"
        warnings.append("pip is not healthy in managed venv")
    return {
        "runtime_receipt_version": 1,
        "device_id": device_id,
        "mode": mode,
        "status": status,
        "stage": stage,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paths": paths,
        "python": {
            "source": "system" if base_python else "unknown",
            "base_python": base_python or "",
            "base_version": base_version,
            "venv_python": paths.get("venv_python") or "",
            "venv_version": venv_version,
            "architecture": "x64" if platform.machine().endswith("64") else "unknown",
        },
        "pip": pip,
        "packages": packages,
        "health": health,
        "warnings": warnings,
        "next_actions": next_actions,
    }


def _save_runtime_receipt(home: Path, receipt: dict) -> None:
    _write_json(home / "state" / "python_runtime_receipt.json", receipt)
    _write_json(_runtime_home(home) / "receipts" / "python_runtime_receipt.json", receipt)


def prepare_runtime(
    mode: str = "check",
    packages: list[str] | None = None,
    python_version_policy: str = "existing",
    device_id: str = "",
    upgrade_pip: bool = False,
) -> dict:
    mode = (mode or "check").strip().lower()
    if mode not in {"check", "prepare", "repair"}:
        return {"error": f"unsupported runtime mode: {mode}"}
    policy = (python_version_policy or "existing").strip().lower()
    if policy not in {"existing", "managed"}:
        return {"error": f"unsupported python_version_policy: {python_version_policy}"}

    home = _iru_home()
    warnings: list[str] = []
    next_actions: list[str] = []
    base_python = _find_base_python()
    venv_path = _runtime_venv_path(home)
    venv_python = _runtime_venv_python(home)

    if mode == "check":
        if not venv_python.exists():
            status = "missing" if base_python else "install_required"
            if not base_python:
                next_actions.append("install_python")
            receipt = _runtime_receipt_v1(
                home=home,
                mode=mode,
                device_id=device_id,
                status=status,
                base_python=base_python,
                packages={"checked": [], "installed": [], "missing": [], "failed": []},
                warnings=warnings,
                next_actions=next_actions,
                stage="missing",
            )
            _save_runtime_receipt(home, receipt)
            return receipt
    else:
        if not base_python:
            next_actions.append("install_python")
            return _runtime_receipt_v1(
                home=home,
                mode=mode,
                device_id=device_id,
                status="install_required",
                base_python=None,
                packages={"checked": [], "installed": [], "missing": [], "failed": []},
                warnings=["no usable system Python found"],
                next_actions=next_actions,
                stage="missing",
            )
        for path in (_runtime_home(home), _runtime_home(home) / "wheels", _runtime_home(home) / "receipts", home / "state"):
            path.mkdir(parents=True, exist_ok=True)
        if mode == "repair" and venv_path.exists() and not venv_python.exists():
            shutil.rmtree(venv_path, ignore_errors=True)
        if not venv_python.exists():
            rc, _, err = _run_python([base_python, "-m", "venv", str(venv_path)], timeout=120)
            if rc != 0 or not venv_python.exists():
                receipt = _runtime_receipt_v1(
                    home=home,
                    mode=mode,
                    device_id=device_id,
                    status="broken",
                    base_python=base_python,
                    packages={"checked": [], "installed": [], "missing": [], "failed": []},
                    warnings=[f"venv creation failed: {err[:240]}"],
                    next_actions=["repair_runtime"],
                    stage="failed",
                )
                _save_runtime_receipt(home, receipt)
                return receipt
        if venv_python.exists():
            receipt = _runtime_receipt_v1(
                home=home,
                mode=mode,
                device_id=device_id,
                status="ok",
                base_python=base_python,
                packages={"checked": [], "installed": [], "missing": [], "failed": []},
                warnings=list(warnings),
                next_actions=list(next_actions),
                stage="venv_created",
            )
            _save_runtime_receipt(home, receipt)
            receipt = _runtime_receipt_v1(
                home=home,
                mode=mode,
                device_id=device_id,
                status="ok",
                base_python=base_python,
                packages={"checked": [], "installed": [], "missing": [], "failed": []},
                warnings=list(warnings),
                next_actions=list(next_actions),
                stage="pip_checked",
            )
            _save_runtime_receipt(home, receipt)
        if upgrade_pip:
            rc, _, err = _run_python([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], timeout=45)
            if rc != 0:
                warnings.append(f"pip bootstrap upgrade failed: {err[:240]}")

    status = "ok" if venv_python.exists() else ("missing" if base_python else "install_required")
    checked_packages = _check_packages(venv_python, packages or []) if venv_python.exists() else {"checked": [], "installed": [], "missing": [], "failed": []}
    if checked_packages["missing"] or checked_packages["failed"]:
        warnings.append("requested packages are not fully available in managed venv")
        if status == "ok":
            status = "degraded"
    receipt = _runtime_receipt_v1(
        home=home,
        mode=mode,
        device_id=device_id,
        status=status,
        base_python=base_python,
        packages=checked_packages,
        warnings=warnings,
        next_actions=next_actions,
        stage="completed",
    )
    _save_runtime_receipt(home, receipt)
    return receipt


def _activation_capabilities(runtime: dict) -> dict:
    python_state = "available" if runtime.get("managed_python_status") == "ok" else "missing"
    return {
        "execute_cmd": "available",
        "write_content": "available",
        "read_files": "available",
        "python": python_state,
        "gui": "available" if platform.system() == "Windows" else "unknown",
        "screenshot": "unknown",
        "process_inspection": "available",
        "temperature_sensors": "unknown",
    }


def activate_device(mode: str = "soft", device_id: str = "") -> dict:
    mode = (mode or "soft").strip().lower()
    if mode not in {"soft", "full", "repair"}:
        return {"error": f"unsupported activation mode: {mode}"}

    home = _iru_home()
    warnings: list[str] = []
    next_actions: list[str] = []
    try:
        for rel in IRU_DIRS:
            (home / rel).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"error": str(exc), "activation_status": "failed"}

    identity = _activation_identity(device_id)
    paths = _activation_paths(home)
    runtime = _runtime_receipt(home)
    capabilities = _activation_capabilities(runtime)
    health = {
        "agent": "ok",
        "disk": "ok" if shutil.disk_usage(home).free > 256 * 1024 * 1024 else "warning",
        "runtime": "ok" if runtime["managed_python_status"] == "ok" else "warning",
    }
    status = "repaired" if mode == "repair" else "ok"
    if mode == "full" and runtime["managed_python_status"] != "ok":
        runtime["managed_python_status"] = "install_required"
        status = "degraded"
        next_actions.append("install_managed_python")
        warnings.append("managed Python runtime is not installed")

    receipt = {
        "activation_version": 1,
        "device_id": device_id,
        "activation_mode": mode,
        "activation_status": status,
        "identity": identity,
        "paths": paths,
        "runtime": runtime,
        "capabilities": capabilities,
        "health": health,
        "warnings": warnings,
        "next_actions": next_actions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    state_dir = home / "state"
    _write_json(state_dir / "identity.json", identity)
    _write_json(state_dir / "capabilities.json", capabilities)
    _write_json(state_dir / "python_receipt.json", runtime)
    _write_json(state_dir / "health.json", health)
    _write_json(state_dir / "activation.json", receipt)
    return receipt


ACTIONS = {
    "execute_cmd": lambda **params: execute_cmd(**params),
    "list_dir": lambda **params: list_dir(**params),
    "get_file_content": lambda **params: get_file_content(**params),
    "write_content": lambda **params: write_content(**params),
    "device.activate": lambda **params: activate_device(**params),
    "device.prepare_runtime": lambda **params: prepare_runtime(**params),
}
