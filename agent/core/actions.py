from __future__ import annotations

import base64
import getpass
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
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


def _is_packaged_agent() -> bool:
    return bool(getattr(sys, "frozen", False))


def _looks_like_python_executable(path: str | Path | None) -> bool:
    if not path:
        return False
    name = Path(path).name.lower()
    if name in {"python", "python.exe", "python3", "python3.exe"}:
        return True
    if not name.startswith("python"):
        return False
    return name.endswith(".exe") or "." not in name


def _is_py_launcher(path: str | Path | None) -> bool:
    return Path(path).name.lower() in {"py", "py.exe"} if path else False


def _subprocess_creationflags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _run_python(args: list[str], timeout: int = 45) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_subprocess_creationflags(),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _find_base_python() -> str | None:
    candidates: list[str] = []
    if not _is_packaged_agent() and _looks_like_python_executable(sys.executable):
        candidates.append(sys.executable)
    candidates.extend([shutil.which("py"), shutil.which("python"), shutil.which("python3")])
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = str(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if not (_looks_like_python_executable(candidate) or _is_py_launcher(candidate)):
            continue
        cmd = [candidate, "-3", "-c", "import sys; print(sys.executable)"] if _is_py_launcher(candidate) else [candidate, "-c", "import sys; print(sys.executable)"]
        rc, out, _ = _run_python(cmd, timeout=5)
        path = out.splitlines()[0].strip() if out else candidate
        if rc == 0 and path and "WindowsApps" not in path and _looks_like_python_executable(path):
            return path
    return None


def _python_version(python_path: Path | str) -> str:
    rc, out, _ = _run_python([str(python_path), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], timeout=10)
    return out.splitlines()[0].strip() if rc == 0 and out else ""


def _pip_info(venv_python: Path) -> dict:
    rc, out, err = _run_python([str(venv_python), "-m", "pip", "--version"], timeout=10)
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
        rc, _, err = _run_python([str(venv_python), "-c", probe], timeout=10)
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


def _process_alive(pid: int | None) -> bool | None:
    if not pid:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def _windows_process_name(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""
    return ""


def _window_record(hwnd: int, *, title: str, class_name: str, pid: int, visible: bool, minimized: bool, foreground: bool, bounds: dict) -> dict:
    width = max(0, int(bounds["right"]) - int(bounds["left"]))
    height = max(0, int(bounds["bottom"]) - int(bounds["top"]))
    return {
        "handle": int(hwnd),
        "pid": int(pid),
        "title": title,
        "class_name": class_name,
        "process_name": _windows_process_name(pid) if os.name == "nt" and pid else "",
        "visible": bool(visible),
        "minimized": bool(minimized),
        "foreground": bool(foreground),
        "bounds": bounds,
        "width": width,
        "height": height,
    }


def _list_windows_internal(include_invisible: bool = False, include_minimized: bool = True, limit: int = 100) -> list[dict]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: list[dict] = []
    foreground = user32.GetForegroundWindow()

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def enum_proc(hwnd, _lparam):
        if len(windows) >= max(1, int(limit or 100)):
            return False
        visible = bool(user32.IsWindowVisible(hwnd))
        minimized = bool(user32.IsIconic(hwnd))
        if not include_invisible and not visible:
            return True
        if not include_minimized and minimized:
            return True
        title_buffer = ctypes.create_unicode_buffer(512)
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        title = title_buffer.value
        class_name = class_buffer.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if not title and not class_name:
            return True
        windows.append(_window_record(
            int(hwnd),
            title=title,
            class_name=class_name,
            pid=int(pid.value),
            visible=visible,
            minimized=minimized,
            foreground=int(hwnd) == int(foreground),
            bounds={"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
        ))
        return True

    user32.EnumWindows(enum_proc, 0)
    return windows


def window_list(include_invisible: bool = False, include_minimized: bool = True, limit: int = 100) -> dict:
    if os.name != "nt":
        return {"status": "failed", "error": "window tools are only implemented on Windows in v1", "windows": [], "collected_at": datetime.now(timezone.utc).isoformat()}
    return {
        "status": "ok",
        "windows": _list_windows_internal(include_invisible=include_invisible, include_minimized=include_minimized, limit=limit),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _match_window(window: dict, criteria: dict) -> bool:
    pid = criteria.get("pid")
    if pid is not None and str(window.get("pid")) != str(pid):
        return False
    title_contains = (criteria.get("title_contains") or "").strip().lower()
    if title_contains and title_contains not in (window.get("title") or "").lower():
        return False
    title_regex = (criteria.get("title_regex") or "").strip()
    if title_regex:
        try:
            if not re.search(title_regex, window.get("title") or "", re.IGNORECASE):
                return False
        except re.error:
            return False
    class_name = (criteria.get("class_name") or "").strip().lower()
    if class_name and class_name != (window.get("class_name") or "").lower():
        return False
    process_name = (criteria.get("process_name") or "").strip().lower()
    if process_name:
        actual = (window.get("process_name") or "").lower()
        if process_name != actual and process_name != Path(actual).stem:
            return False
    visible = criteria.get("visible")
    if visible is not None and bool(window.get("visible")) != bool(visible):
        return False
    return True


def _find_windows_once(criteria: dict) -> list[dict]:
    include_invisible = criteria.get("visible") is not True
    windows = _list_windows_internal(include_invisible=include_invisible, include_minimized=True, limit=200)
    matches = [window for window in windows if _match_window(window, criteria)]
    matches.sort(key=lambda item: (not item.get("foreground"), not item.get("visible"), item.get("minimized")))
    return matches


def window_find(
    pid: int | None = None,
    title_contains: str | None = None,
    title_regex: str | None = None,
    class_name: str | None = None,
    process_name: str | None = None,
    visible: bool | None = True,
    timeout_sec: float = 5,
) -> dict:
    if os.name != "nt":
        return {"status": "failed", "error": "window tools are only implemented on Windows in v1", "match": None, "matches": [], "criteria": {}, "checked_at": datetime.now(timezone.utc).isoformat()}
    deadline = time.monotonic() + max(0.0, min(float(timeout_sec or 0), 30.0))
    criteria = {
        "pid": pid,
        "title_contains": title_contains,
        "title_regex": title_regex,
        "class_name": class_name,
        "process_name": process_name,
        "visible": visible,
    }
    matches: list[dict] = []
    while True:
        matches = _find_windows_once(criteria)
        if matches or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    return {
        "status": "found" if matches else "not_found",
        "match": matches[0] if matches else None,
        "matches": matches,
        "criteria": criteria,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def window_verify(
    pid: int | None = None,
    title_contains: str | None = None,
    title_regex: str | None = None,
    class_name: str | None = None,
    process_name: str | None = None,
    require_visible: bool = True,
    require_not_minimized: bool = False,
    timeout_sec: float = 5,
) -> dict:
    visible = True if require_visible else None
    found = window_find(
        pid=pid,
        title_contains=title_contains,
        title_regex=title_regex,
        class_name=class_name,
        process_name=process_name,
        visible=visible,
        timeout_sec=timeout_sec,
    )
    process_alive = _process_alive(pid) if pid is not None else None
    window = found.get("match")
    status = "not_found"
    verified = False
    if found.get("status") == "failed":
        status = "failed"
    elif window:
        if require_not_minimized and window.get("minimized"):
            status = "window_minimized"
        elif require_visible and not window.get("visible"):
            status = "not_found"
        else:
            status = "verified"
            verified = True
    elif pid is not None:
        pid_windows = _find_windows_once({"pid": pid, "visible": None}) if os.name == "nt" else []
        if pid_windows and (title_contains or title_regex):
            status = "title_mismatch"
            window = pid_windows[0]
        elif process_alive is True:
            status = "process_alive_no_window"
        elif process_alive is False:
            status = "not_running"
    return {
        "status": status,
        "verified": verified,
        "window": window,
        "process_alive": process_alive,
        "window_visible": bool(window.get("visible")) if window else False,
        "window_title": window.get("title") if window else "",
        "criteria": {
            "pid": pid,
            "title_contains": title_contains,
            "title_regex": title_regex,
            "class_name": class_name,
            "process_name": process_name,
            "require_visible": require_visible,
            "require_not_minimized": require_not_minimized,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _has_window_criteria(handle=None, pid=None, title_contains=None, title_regex=None, class_name=None, process_name=None) -> bool:
    return any(value not in (None, "") for value in (handle, pid, title_contains, title_regex, class_name, process_name))


def window_focus(handle: int | None = None, pid: int | None = None, title_contains: str | None = None) -> dict:
    if os.name != "nt":
        return {"status": "failed", "error": "window tools are only implemented on Windows in v1", "window": None}
    if handle:
        matches = [window for window in _list_windows_internal(include_invisible=True, include_minimized=True, limit=200) if str(window.get("handle")) == str(handle)]
    else:
        matches = window_find(pid=pid, title_contains=title_contains, visible=None, timeout_sec=2).get("matches") or []
    if not matches:
        return {"status": "not_found", "window": None}
    window = matches[0]
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(window["handle"])
    SW_RESTORE = 9
    SW_SHOW = 5
    user32.ShowWindow(hwnd, SW_RESTORE if window.get("minimized") else SW_SHOW)
    ok = bool(user32.SetForegroundWindow(hwnd))
    return {"status": "focused" if ok else "failed", "window": window}


def window_close(
    handle: int | None = None,
    pid: int | None = None,
    title_contains: str | None = None,
    title_regex: str | None = None,
    class_name: str | None = None,
    process_name: str | None = None,
    force: bool = False,
) -> dict:
    if os.name != "nt":
        return {"status": "failed", "error": "window tools are only implemented on Windows in v1", "window": None, "pid": pid}
    if not _has_window_criteria(handle, pid, title_contains, title_regex, class_name, process_name):
        return {"status": "ambiguous", "error": "ambiguous_window_match", "window": None, "pid": pid}
    if handle:
        matches = [window for window in _list_windows_internal(include_invisible=True, include_minimized=True, limit=200) if str(window.get("handle")) == str(handle)]
    else:
        matches = window_find(pid=pid, title_contains=title_contains, title_regex=title_regex, class_name=class_name, process_name=process_name, visible=None, timeout_sec=2).get("matches") or []
    if not matches:
        if force and pid and _process_alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=10, creationflags=_subprocess_creationflags())
            return {"status": "closed" if not _process_alive(pid) else "still_running", "window": None, "pid": pid}
        return {"status": "not_found", "window": None, "pid": pid}
    if len(matches) > 1 and not handle:
        return {"status": "ambiguous", "error": "ambiguous_window_match", "matches": matches[:10], "window": None, "pid": pid}
    window = matches[0]
    import ctypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    user32.PostMessageW(int(window["handle"]), WM_CLOSE, 0, 0)
    time.sleep(0.5)
    target_pid = int(pid or window.get("pid") or 0)
    alive = _process_alive(target_pid)
    if force and alive:
        subprocess.run(["taskkill", "/PID", str(target_pid), "/F"], capture_output=True, text=True, timeout=10, creationflags=_subprocess_creationflags())
        alive = _process_alive(target_pid)
    return {"status": "still_running" if alive else "closed", "window": window, "pid": target_pid}


def _launch_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _strip_arg_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _process_argv_from_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return []
    argv = [_strip_arg_quotes(part) for part in parts]
    if argv and argv[0] == "&":
        argv = argv[1:]
    return argv


def _format_process_command(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def app_launch(
    command: str | None = None,
    executable: str | None = None,
    args: list | None = None,
    cwd: str | None = None,
    expected_title: str | None = None,
    expected_process: str | None = None,
    timeout_sec: float = 5,
    env: dict | None = None,
) -> dict:
    if executable:
        argv = [str(executable), *[str(arg) for arg in (args or [])]]
    elif command:
        argv = _process_argv_from_command(command)
    else:
        argv = []
    display_command = command or (_format_process_command(argv) if argv else "")
    if not argv:
        return {"status": "failed", "error": "missing command", "pid": None, "command": display_command, "cwd": cwd, "process_alive": False, "window": None, "next_actions": []}
    proc_env = os.environ.copy()
    if isinstance(env, dict):
        proc_env.update({str(key): str(value) for key, value in env.items()})
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd or None,
            env=proc_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=_launch_creationflags(),
        )
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "pid": None, "command": display_command, "cwd": cwd, "process_alive": False, "window": None, "next_actions": []}

    timeout = max(0.0, min(float(timeout_sec or 0), 30.0))
    criteria = {"pid": proc.pid}
    if expected_title:
        criteria["title_contains"] = expected_title
    if expected_process:
        criteria["process_name"] = expected_process
    found = window_find(timeout_sec=timeout, **criteria) if os.name == "nt" else {"status": "failed", "match": None}
    window = found.get("match")
    process_alive = _process_alive(proc.pid)
    status = "launched_verified" if window else ("launched" if process_alive is not False else "failed")
    return {
        "status": status,
        "pid": proc.pid,
        "command": display_command,
        "executable": argv[0],
        "args": argv[1:],
        "cwd": cwd or "",
        "process_alive": process_alive,
        "window": window,
        "next_actions": [] if window else ["window.verify"],
    }


def app_verify_launch(pid: int, expected_title: str | None = None, expected_process: str | None = None, timeout_sec: float = 5) -> dict:
    result = window_verify(pid=pid, title_contains=expected_title, process_name=expected_process, timeout_sec=timeout_sec)
    return {
        "status": "verified" if result.get("verified") else result.get("status", "failed"),
        "verified": bool(result.get("verified")),
        "pid": pid,
        "window": result.get("window"),
        "process_alive": result.get("process_alive"),
        "window_visible": result.get("window_visible"),
    }


def app_close(pid: int, force: bool = False) -> dict:
    return window_close(pid=pid, force=force)


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
    venv_path = _runtime_venv_path(home)
    venv_python = _runtime_venv_python(home)

    if mode == "check":
        if venv_python.exists():
            checked_packages = _check_packages(venv_python, packages or [])
            status = "ok"
            if checked_packages["missing"] or checked_packages["failed"]:
                warnings.append("requested packages are not fully available in managed venv")
                status = "degraded"
            receipt = _runtime_receipt_v1(
                home=home,
                mode=mode,
                device_id=device_id,
                status=status,
                base_python=None,
                packages=checked_packages,
                warnings=warnings,
                next_actions=next_actions,
                stage="completed",
            )
            _save_runtime_receipt(home, receipt)
            return receipt

        base_python = _find_base_python()
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
        base_python = _find_base_python()
        if not base_python:
            next_actions.append("install_python")
            receipt = _runtime_receipt_v1(
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
            _save_runtime_receipt(home, receipt)
            return receipt
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
    "window.list": lambda **params: window_list(**params),
    "window.find": lambda **params: window_find(**params),
    "window.verify": lambda **params: window_verify(**params),
    "window.focus": lambda **params: window_focus(**params),
    "window.close": lambda **params: window_close(**params),
    "app.launch": lambda **params: app_launch(**params),
    "app.verify_launch": lambda **params: app_verify_launch(**params),
    "app.close": lambda **params: app_close(**params),
}
