from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from .config import AgentPaths
from .state import AgentState

_DOWNLOAD_CHUNK_SIZE = 512 * 1024


def version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.strip().split("."))
    except Exception:
        return (0, 0)


def check_for_update(
    server_url: str,
    agent_version: str,
    paths: AgentPaths,
    logger: logging.Logger,
    state: AgentState,
) -> bool:
    http_url = server_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")

    if platform_name() != "Windows":
        message = "Автообновление на этой платформе отключено; обновите агент вручную."
        logger.info("[update] %s", message)
        state.set_update_status(message, state="disabled", progress=-1, detail="")
        return False

    try:
        logger.info("[update] checking for updates (current=%s)", agent_version)
        state.set_update_status(
            "Проверка обновлений...",
            state="checking_update",
            progress=-1,
            detail="Проверка наличия новой версии...",
        )
        req = Request(
            f"{http_url}/api/agent/version",
            headers={"User-Agent": f"IRU-Agent/{agent_version}"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        message = f"Не удалось проверить обновления: {exc}"
        logger.warning("[update] %s", message)
        state.set_update_status(message, state="", progress=-1, detail="")
        return False

    server_version = str(data.get("version", "0.0"))
    download_url = str(data.get("download_url", ""))
    kind = str(data.get("kind", "exe"))

    if version_tuple(server_version) <= version_tuple(agent_version):
        message = f"Актуальная версия ({agent_version})"
        logger.info("[update] %s", message)
        state.set_update_status(message, state="", progress=-1, detail="")
        return False

    state.set_update_status(
        f"Доступно обновление {server_version}",
        state="update_available",
        progress=0,
        detail="Подготовка к загрузке...",
    )
    if not download_url:
        message = f"Найдена версия {server_version}, но нет ссылки на скачивание."
        logger.warning("[update] %s", message)
        state.set_update_status(message, state="update_available", progress=-1, detail="")
        return False

    if not getattr(sys, "frozen", False):
        message = f"Найдена версия {server_version}, но автообновление доступно только в собранном exe."
        logger.info("[update] %s", message)
        state.set_update_status(message, state="update_available", progress=-1, detail="")
        return False

    state.set_update_status(
        f"Устанавливается обновление {server_version}",
        state="updating",
        progress=0,
        detail="Подготовка загрузки...",
    )
    full_url = f"{http_url}{download_url}" if download_url.startswith("/") else download_url
    suffix = ".zip" if kind == "zip" else ".exe"
    fd, temp_name = tempfile.mkstemp(prefix=f"iru_agent_update_{server_version}_", suffix=suffix)
    os.close(fd)
    download_path = Path(temp_name)

    try:
        downloaded_size = _download_update(
            full_url=full_url,
            agent_version=agent_version,
            download_path=download_path,
            server_version=server_version,
            logger=logger,
            state=state,
        )
    except Exception as exc:
        message = f"Ошибка скачивания обновления: {exc}"
        logger.error("[update] %s", message)
        state.set_update_status(message, state="update_available", progress=-1, detail="")
        try:
            download_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    if downloaded_size < 1000:
        message = f"Скачанный файл слишком маленький ({downloaded_size} байт), обновление пропущено."
        logger.warning("[update] %s", message)
        state.set_update_status(message, state="update_available", progress=-1, detail="")
        try:
            download_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    state.set_update_status(
        f"Устанавливается обновление {server_version}",
        state="updating",
        progress=100,
        detail="Загрузка завершена, применяем обновление...",
    )
    if kind == "zip":
        return _update_zip(download_path, server_version, paths, logger)
    return _update_exe(download_path, server_version, paths, logger)


def _download_update(
    full_url: str,
    agent_version: str,
    download_path: Path,
    server_version: str,
    logger: logging.Logger,
    state: AgentState,
) -> int:
    req = Request(full_url, headers={"User-Agent": f"IRU-Agent/{agent_version}"})
    with urlopen(req, timeout=120) as resp, download_path.open("wb") as fh:
        total_size = _response_content_length(resp)
        downloaded = 0
        if total_size:
            state.set_update_status(
                f"Устанавливается обновление {server_version}",
                state="updating",
                progress=0,
                detail=f"Скачивание 0% ({_format_bytes(0)} из {_format_bytes(total_size)})",
            )
        else:
            state.set_update_status(
                f"Устанавливается обновление {server_version}",
                state="updating",
                progress=-1,
                detail="Скачивание обновления...",
            )

        while True:
            chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            fh.write(chunk)
            downloaded += len(chunk)
            if total_size:
                progress = min(100, int(downloaded * 100 / total_size))
                detail = (
                    f"Скачивание {progress}% "
                    f"({_format_bytes(downloaded)} из {_format_bytes(total_size)})"
                )
            else:
                progress = -1
                detail = f"Скачано {_format_bytes(downloaded)}"
            state.set_update_status(
                f"Устанавливается обновление {server_version}",
                state="updating",
                progress=progress,
                detail=detail,
            )
    logger.info("[update] downloaded update package: %s", download_path)
    return download_path.stat().st_size


def _update_exe(download_path: Path, server_version: str, paths: AgentPaths, logger: logging.Logger) -> bool:
    exe_path = Path(sys.executable)
    new_exe = exe_path.parent / "agent_new.exe"
    old_exe = exe_path.parent / "agent_old.exe"
    log_path = Path(tempfile.gettempdir()) / "iru_agent_update.log"
    updater_cwd = Path(tempfile.gettempdir())

    try:
        shutil.move(str(download_path), str(new_exe))
    except Exception as exc:
        logger.error("[update] failed to write downloaded exe: %s", exc)
        try:
            download_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    bat_path = exe_path.parent / "_update.bat"
    pid = os.getpid()
    bat_content = f"""@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "LOG={log_path}"
cd /d "{updater_cwd}"
echo [%date% %time%] Start EXE update v{server_version} >> "%LOG%"
echo [%date% %time%] Script CWD: %CD% >> "%LOG%"
echo [%date% %time%] Current exe: {exe_path} >> "%LOG%"
echo [%date% %time%] New exe: {new_exe} >> "%LOG%"
echo [%date% %time%] Backup exe: {old_exe} >> "%LOG%"
echo [%date% %time%] Waiting for PID {pid} to exit... >> "%LOG%"
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo [%date% %time%] PID {pid} exited, replacing executable >> "%LOG%"
if exist "{old_exe}" del /f "{old_exe}" >> "%LOG%" 2>&1
echo [%date% %time%] Cleanup previous backup errorlevel=%errorlevel% >> "%LOG%"
echo [%date% %time%] move "{exe_path}" -> "{old_exe}" >> "%LOG%"
move /y "{exe_path}" "{old_exe}" >> "%LOG%" 2>&1
echo [%date% %time%] move current->backup errorlevel=%errorlevel% >> "%LOG%"
echo [%date% %time%] move "{new_exe}" -> "{exe_path}" >> "%LOG%"
move /y "{new_exe}" "{exe_path}" >> "%LOG%" 2>&1
echo [%date% %time%] move new->current errorlevel=%errorlevel% >> "%LOG%"
echo [%date% %time%] start "{exe_path}" >> "%LOG%"
start "" "{exe_path}"
set "START_RC=%errorlevel%"
echo [%date% %time%] start new exe errorlevel=!START_RC! >> "%LOG%"
call :cleanup_file "{old_exe}" "old exe backup"
echo [%date% %time%] EXE update script finished >> "%LOG%"
del /f "%~f0"
:end
endlocal
exit /b 0

:cleanup_file
set "TARGET=%~1"
set "LABEL=%~2"
for /L %%I in (1,1,6) do (
    if not exist "!TARGET!" goto cleanup_file_done
    del /f /q "!TARGET!" >> "%LOG%" 2>&1
    if not exist "!TARGET!" goto cleanup_file_done
    echo [%date% %time%] cleanup !LABEL! attempt %%I failed, retrying... >> "%LOG%"
    timeout /t 2 /nobreak >nul
)
:cleanup_file_done
if exist "!TARGET!" (
    echo [%date% %time%] cleanup !LABEL! failed, target still exists: !TARGET! >> "%LOG%"
) else (
    echo [%date% %time%] cleanup !LABEL! done >> "%LOG%"
)
exit /b 0
"""
    try:
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            cwd=str(updater_cwd),
            creationflags=0x08000000,
            close_fds=True,
        )
        logger.info("[update] exe update started for %s (log=%s)", server_version, log_path)
        return True
    except Exception as exc:
        logger.error("[update] failed to launch exe updater: %s", exc)
        try:
            new_exe.unlink(missing_ok=True)
            bat_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _update_zip(download_path: Path, server_version: str, paths: AgentPaths, logger: logging.Logger) -> bool:
    parent_dir = paths.base_dir.parent
    zip_path = parent_dir / f"agent_update_{server_version}.zip"
    staging_dir = parent_dir / f"agent_new_{server_version}"
    backup_dir = parent_dir / f"agent_old_{int(time.time())}"
    bat_path = parent_dir / "_update_zip.bat"
    log_path = Path(tempfile.gettempdir()) / "iru_agent_update.log"
    updater_cwd = Path(tempfile.gettempdir())

    try:
        shutil.move(str(download_path), str(zip_path))
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            if zf.testzip() is not None:
                raise zipfile.BadZipFile("archive integrity check failed")
            zf.extractall(staging_dir)
    except Exception as exc:
        logger.error("[update] failed to prepare ZIP update: %s", exc)
        try:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception:
            pass
        return False

    staging_exe = staging_dir / "IruAgent.exe"
    nested_dir = staging_dir / "IruAgent"
    if not staging_exe.exists() and nested_dir.is_dir():
        nested_exe = nested_dir / "IruAgent.exe"
        if nested_exe.exists():
            for item in list(nested_dir.iterdir()):
                dest = staging_dir / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest, ignore_errors=True)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            nested_dir.rmdir()
            staging_exe = staging_dir / "IruAgent.exe"

    if not staging_exe.exists():
        logger.warning("[update] IruAgent.exe not found in the update ZIP")
        try:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception:
            pass
        return False

    pid = os.getpid()
    bat_content = f"""@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "LOG={log_path}"
cd /d "{updater_cwd}"
echo [%date% %time%] Start update v{server_version} >> "%LOG%"
echo [%date% %time%] Script CWD: %CD% >> "%LOG%"
echo [%date% %time%] Current dir: {paths.base_dir} >> "%LOG%"
echo [%date% %time%] Staging dir: {staging_dir} >> "%LOG%"
echo [%date% %time%] Backup dir: {backup_dir} >> "%LOG%"
echo [%date% %time%] ZIP path: {zip_path} >> "%LOG%"
echo [%date% %time%] Waiting for PID {pid} to exit... >> "%LOG%"
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo [%date% %time%] PID {pid} exited, starting directory swap >> "%LOG%"
call :move_dir_retry "{paths.base_dir}" "{backup_dir}" "current->backup"
if errorlevel 1 goto restore
call :move_dir_retry "{staging_dir}" "{paths.base_dir}" "staging->current"
if errorlevel 1 goto restore
echo [%date% %time%] start "{paths.base_dir}\\IruAgent.exe" >> "%LOG%"
start "" "{paths.base_dir}\\IruAgent.exe"
set "START_RC=%errorlevel%"
echo [%date% %time%] start new agent errorlevel=!START_RC! >> "%LOG%"
call :cleanup_file "{zip_path}" "zip package"
call :cleanup_dir "{backup_dir}" "backup directory"
echo [%date% %time%] Update applied successfully >> "%LOG%"
del /f "%~f0"
goto end
:restore
echo [%date% %time%] Enter restore branch >> "%LOG%"
if not exist "{paths.base_dir}" (
    echo [%date% %time%] Current dir missing, attempting restore from backup >> "%LOG%"
    if exist "{backup_dir}" (
        call :move_dir_retry "{backup_dir}" "{paths.base_dir}" "backup->current"
    )
)
echo [%date% %time%] restore branch errorlevel=%errorlevel% >> "%LOG%"
if exist "{paths.base_dir}\\IruAgent.exe" (
    echo [%date% %time%] start restored agent "{paths.base_dir}\\IruAgent.exe" >> "%LOG%"
    start "" "{paths.base_dir}\\IruAgent.exe"
    echo [%date% %time%] start restored agent errorlevel=!errorlevel! >> "%LOG%"
) else if exist "{backup_dir}\\IruAgent.exe" (
    echo [%date% %time%] start backup agent "{backup_dir}\\IruAgent.exe" >> "%LOG%"
    start "" "{backup_dir}\\IruAgent.exe"
    echo [%date% %time%] start backup agent errorlevel=!errorlevel! >> "%LOG%"
)
call :cleanup_file "{zip_path}" "zip package (restore)"
if exist "{paths.base_dir}" (
    call :cleanup_dir "{staging_dir}" "staging directory (restore)"
) else (
    echo [%date% %time%] Skipping staging cleanup because restore did not recreate current dir >> "%LOG%"
)
echo [%date% %time%] Restore branch finished >> "%LOG%"
del /f "%~f0"
:end
echo [%date% %time%] Update script finished >> "%LOG%"
endlocal
exit /b 0

:move_dir_retry
set "SRC=%~1"
set "DST=%~2"
set "LABEL=%~3"
for /L %%I in (1,1,10) do (
    if not exist "!SRC!" (
        echo [%date% %time%] move !LABEL! source missing: !SRC! >> "%LOG%"
        exit /b 1
    )
    echo [%date% %time%] move !LABEL! attempt %%I: "!SRC!" -> "!DST!" >> "%LOG%"
    move /y "!SRC!" "!DST!" >> "%LOG%" 2>&1
    set "MOVE_RC=!errorlevel!"
    echo [%date% %time%] move !LABEL! attempt %%I errorlevel=!MOVE_RC! >> "%LOG%"
    if "!MOVE_RC!"=="0" exit /b 0
    timeout /t 2 /nobreak >nul
)
echo [%date% %time%] move !LABEL! failed after retries >> "%LOG%"
exit /b 1

:cleanup_file
set "TARGET=%~1"
set "LABEL=%~2"
for /L %%I in (1,1,8) do (
    if not exist "!TARGET!" goto cleanup_file_done
    del /f /q "!TARGET!" >> "%LOG%" 2>&1
    if not exist "!TARGET!" goto cleanup_file_done
    echo [%date% %time%] cleanup !LABEL! attempt %%I failed, retrying... >> "%LOG%"
    timeout /t 2 /nobreak >nul
)
:cleanup_file_done
if exist "!TARGET!" (
    echo [%date% %time%] cleanup !LABEL! failed, target still exists: !TARGET! >> "%LOG%"
) else (
    echo [%date% %time%] cleanup !LABEL! done >> "%LOG%"
)
exit /b 0

:cleanup_dir
set "TARGET=%~1"
set "LABEL=%~2"
for /L %%I in (1,1,8) do (
    if not exist "!TARGET!" goto cleanup_dir_done
    rmdir /s /q "!TARGET!" >> "%LOG%" 2>&1
    if not exist "!TARGET!" goto cleanup_dir_done
    echo [%date% %time%] cleanup !LABEL! attempt %%I failed, retrying... >> "%LOG%"
    timeout /t 2 /nobreak >nul
)
:cleanup_dir_done
if exist "!TARGET!" (
    echo [%date% %time%] cleanup !LABEL! failed, target still exists: !TARGET! >> "%LOG%"
) else (
    echo [%date% %time%] cleanup !LABEL! done >> "%LOG%"
)
exit /b 0
"""
    try:
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            cwd=str(updater_cwd),
            creationflags=0x08000000,
            close_fds=True,
        )
        logger.info("[update] zip update started for %s (log=%s)", server_version, log_path)
        return True
    except Exception as exc:
        logger.error("[update] failed to launch ZIP updater: %s", exc)
        try:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            bat_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def platform_name() -> str:
    import platform

    return platform.system()


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    units = ["B", "KB", "MB", "GB"]
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024.0
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _response_content_length(response) -> int | None:
    try:
        raw_value = response.headers.get("Content-Length")
        if not raw_value:
            return None
        total = int(raw_value)
        return total if total > 0 else None
    except Exception:
        return None
