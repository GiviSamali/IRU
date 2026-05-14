import asyncio
import base64
import json
import logging
import time
import traceback
import uuid
import re as _re
from datetime import datetime, timezone

import httpx

try:
    from .api_support import is_command_safe, needs_confirmation
    from .controller import (
        ConfirmationRequired,
        classify_task_complexity,
        process_nl_command,
        process_onboarding_message,
        strip_markdown,
    )
    from .controller_trust import enforce_trusted_answer
    from .database import (
        add_message,
        add_training_record,
        add_user_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
        get_messages,
        get_plan_trial_used,
        get_training_count,
        get_user_facts,
        get_user_plan,
        get_user_device_profiles,
        set_plan_trial_used,
    )
    from .path_scope import PATH_SCOPE_ERROR, validate_execute_command_paths_for_device, validate_write_path_for_device
    from .python_toolchain import resolve_python_toolchain, validate_toolchain_fact_against_receipt
    from .runtime_state import (
        _dk,
        _short_did,
        create_download_link,
        devices,
        get_user_devices,
        is_plan_declined,
        is_suggested_fact_declined,
        tasks,
    )
except ImportError:
    from api_support import is_command_safe, needs_confirmation
    from controller import (
        ConfirmationRequired,
        classify_task_complexity,
        process_nl_command,
        process_onboarding_message,
        strip_markdown,
    )
    from controller_trust import enforce_trusted_answer
    from database import (
        add_message,
        add_training_record,
        add_user_fact,
        get_chat,
        get_device_profile,
        get_memory_stats,
        get_messages,
        get_plan_trial_used,
        get_training_count,
        get_user_facts,
        get_user_plan,
        get_user_device_profiles,
        set_plan_trial_used,
    )
    from path_scope import PATH_SCOPE_ERROR, validate_execute_command_paths_for_device, validate_write_path_for_device
    from python_toolchain import resolve_python_toolchain, validate_toolchain_fact_against_receipt
    from runtime_state import (
        _dk,
        _short_did,
        create_download_link,
        devices,
        get_user_devices,
        is_plan_declined,
        is_suggested_fact_declined,
        tasks,
    )


logger = logging.getLogger("iru.run_plan")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_device_state_request(message: str) -> bool:
    lowered = (message or "").lower()
    state_markers = (
        "состояни",
        "статус",
        "текущее",
        "сейчас происходит",
        "что происходит",
        "snapshot",
        "state",
        "status",
    )
    device_markers = (
        "устройств",
        "компьютер",
        "пк",
        "device",
        "devices",
    )
    return any(marker in lowered for marker in state_markers) and any(marker in lowered for marker in device_markers)


def _registered_identity(device_id: str, dev: dict | None, profile: dict | None = None) -> dict:
    info = (dev or {}).get("info", {}) if isinstance(dev, dict) else {}
    registered = (dev or {}).get("registered_identity", {}) if isinstance(dev, dict) else {}
    return {
        "target_device_id": _short_did(device_id),
        "registered_hostname": registered.get("registered_hostname") or info.get("hostname") or (profile or {}).get("hostname"),
        "registered_machine_guid": registered.get("registered_machine_guid") or info.get("machine_guid") or (profile or {}).get("machine_guid"),
        "registered_device_id": info.get("device_id") or _short_did(device_id),
    }


def _norm_identity_value(value) -> str:
    return str(value or "").strip().lower()


def _build_identity_receipt(
    *,
    device_id: str,
    dev: dict | None,
    observed: dict | None = None,
    profile: dict | None = None,
) -> dict:
    observed = observed or {}
    receipt = {
        **_registered_identity(device_id, dev, profile),
        "observed_hostname": observed.get("observed_hostname") or observed.get("hostname"),
        "observed_computer_name": observed.get("observed_computer_name") or observed.get("computer_name"),
        "observed_machine_guid": observed.get("observed_machine_guid") or observed.get("machine_uuid") or observed.get("uuid"),
        "observed_username": observed.get("observed_username") or observed.get("username"),
        "collected_at": observed.get("collected_at") or _utc_now_iso(),
        "identity_status": "unknown",
    }
    registered_hostname = _norm_identity_value(receipt.get("registered_hostname"))
    observed_names = [
        _norm_identity_value(receipt.get("observed_hostname")),
        _norm_identity_value(receipt.get("observed_computer_name")),
    ]
    observed_names = [name for name in observed_names if name]
    if observed_names:
        receipt["identity_status"] = "ok"
        if registered_hostname and registered_hostname not in observed_names:
            receipt["identity_status"] = "mismatch"

    for stable_key in ("bios_serial", "system_uuid"):
        registered_value = _norm_identity_value((dev or {}).get("info", {}).get(stable_key) if isinstance(dev, dict) else "")
        observed_value = _norm_identity_value(observed.get(stable_key))
        if registered_value and observed_value:
            receipt["identity_status"] = "ok" if registered_value == observed_value else "mismatch"
    return receipt


def _attach_identity_receipt(result: dict, *, device_id: str, dev: dict | None) -> dict:
    if not isinstance(result, dict):
        return result
    if "identity_receipt" not in result:
        profile = get_device_profile(_short_did(device_id))
        observed = result.get("observed_identity") or result.get("identity") or {}
        if not observed:
            observed = {key: result.get(key) for key in (
                "observed_hostname",
                "observed_computer_name",
                "observed_machine_guid",
                "observed_username",
                "bios_serial",
                "system_uuid",
            ) if result.get(key)}
        result = dict(result)
        result["identity_receipt"] = _build_identity_receipt(device_id=device_id, dev=dev, observed=observed, profile=profile)
    return result


def _should_probe_python_toolchain(message: str, device_info: dict) -> bool:
    if "windows" not in (device_info.get("os", "") or "").lower():
        return False
    lowered = (message or "").lower()
    return any(marker in lowered for marker in ("python", "pyqt", "pip", "numpy", "matplotlib", ".py"))


async def _probe_python_toolchain_if_needed(
    *,
    message: str,
    device_id: str,
    device_info: dict,
    dev: dict,
    user_id: int,
    send_fn,
) -> None:
    if not _should_probe_python_toolchain(message, device_info):
        return
    if not dev.get("ws"):
        return
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$paths=New-Object System.Collections.Generic.List[string]; "
        "try { $pyout = py -3 -c \"import sys; print(sys.executable)\" 2>$null; "
        "if ($LASTEXITCODE -eq 0 -and $pyout) { $paths.Add([string]$pyout[0]) } } catch {}; "
        "$patterns=@('C:\\Program Files\\Python*\\python.exe', \"$env:LOCALAPPDATA\\Programs\\Python\\Python*\\python.exe\"); "
        "foreach($pattern in $patterns) { Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | "
        "Sort-Object FullName -Descending | ForEach-Object { $paths.Add($_.FullName) } }; "
        "foreach($alias in @('python','python3')) { $cmd=Get-Command $alias -ErrorAction SilentlyContinue; "
        "if($cmd) { Write-Output (\"ALIAS {0} {1} Source version {2}\" -f $alias,$cmd.Source,$cmd.Version) } }; "
        "foreach($p in ($paths | Select-Object -Unique)) { "
        "if($p -and $p -notlike '*\\Microsoft\\WindowsApps\\*' -and (Test-Path $p)) { "
        "& $p -c \"import sys,site; print(sys.executable); print('Python '+sys.version.split()[0]); print(';'.join(site.getsitepackages()))\"; "
        "& $p -m pip --version; break } }"
    )
    try:
        result = await send_fn(_short_did(device_id), "execute_cmd", {"command": command, "timeout": 20})
        profile = get_device_profile(_short_did(device_id))
        resolve_python_toolchain(
            {"device_id": _short_did(device_id), "machine_guid": (profile or {}).get("machine_guid")},
            [{"command": command, "result": result}],
        )
    except Exception as exc:
        logger.info("[python_toolchain] probe skipped/failed for %s user=%s: %s", device_id, user_id, exc)


async def send_command_to_agent(
    device_id: str,
    action: str,
    params: dict,
    user_id: int | None = None,
    skip_confirm: bool = False,
) -> dict:
    """Send a command to a конкретный agent and wait for the response."""
    dev = devices.get(device_id)
    if action == "execute_cmd":
        cmd_text = params.get("command", "")
        profile = get_device_profile(_short_did(device_id))
        try:
            validate_execute_command_paths_for_device(cmd_text, (dev or {}).get("info", {}), profile)
        except ValueError:
            raise RuntimeError(f"BLOCKED: {PATH_SCOPE_ERROR}")
        if len(cmd_text) > 2000:
            raise RuntimeError(
                "Команда слишком длинная (>2000 символов). "
                "Используй write_content для создания текстовых файлов, "
                "а не PowerShell-строки."
            )
        low = cmd_text.lower()
        if "word.application" in low and ("typetext" in low or "typeparagraph" in low):
            raise RuntimeError(
                "Создание текстовых файлов через Word.Application/TypeText запрещено. "
                "Используй инструмент write_content — он создаёт файл напрямую и безопасно."
            )
        if "invoke-webrequest" in low or "iwr " in low or "curl " in low or "wget " in low:
            search_hosts = ("duckduckgo.com", "google.com/search", "bing.com/search", "yandex.ru/search")
            if any(host in low for host in search_hosts):
                raise RuntimeError(
                    "Поиск в интернете через Invoke-WebRequest/curl/wget запрещён. "
                    "Используй инструмент web_search."
                )
        if not is_command_safe(cmd_text):
            raise RuntimeError(
                "BLOCKED: Команда запрещена на этапе бета-тестирования. "
                "Сообщи пользователю, что эта команда недоступна в бета-версии."
            )
        if needs_confirmation(cmd_text):
            if skip_confirm:
                logger.info("[security] skip_confirm=True, команда пропущена без плашки: %s", cmd_text[:80])
            else:
                raise RuntimeError("CONFIRM_REQUIRED: Команда требует подтверждения пользователя.")

    elif action == "write_content":
        path = str(params.get("path", "")).strip()
        if not path:
            raise RuntimeError("BLOCKED: путь не указан")
        norm = path.replace("\\", "/").lower()
        forbidden_prefixes = (
            "c:/windows/",
            "c:/program files/",
            "c:/program files (x86)/",
            "c:/programdata/",
            "c:/system volume information/",
            "/etc/",
            "/bin/",
            "/sbin/",
            "/usr/bin/",
            "/usr/sbin/",
            "/usr/lib/",
            "/boot/",
            "/dev/",
            "/proc/",
            "/sys/",
            "/var/log/",
            "/root/",
        )
        if any(norm.startswith(prefix) for prefix in forbidden_prefixes):
            raise RuntimeError(
                f"BLOCKED: Запись в системные каталоги запрещена на этапе бета-тестирования: {path}"
            )
        profile = get_device_profile(_short_did(device_id))
        try:
            validate_write_path_for_device(path, (dev or {}).get("info", {}), profile)
        except ValueError:
            raise RuntimeError(f"BLOCKED: {PATH_SCOPE_ERROR}")

    if not dev:
        raise RuntimeError(f"Устройство '{device_id}' не подключено")

    cmd_id = str(uuid.uuid4())[:8]
    future = asyncio.get_event_loop().create_future()
    dev["pending"][cmd_id] = future

    msg = json.dumps({
        "type": "command",
        "payload": {"id": cmd_id, "action": action, "params": params},
    })
    await dev["ws"].send_text(msg)

    wait_timeout = 60.0
    if action == "execute_cmd":
        try:
            cmd_timeout = int(params.get("timeout", 30) or 30)
        except Exception:
            cmd_timeout = 30
        wait_timeout = max(60.0, float(cmd_timeout) + 15.0)
    elif action in {"write_content", "get_file_content"}:
        wait_timeout = 90.0

    try:
        result = await asyncio.wait_for(future, timeout=wait_timeout)
    except asyncio.TimeoutError:
        dev["pending"].pop(cmd_id, None)
        raise RuntimeError("Таймаут ожидания ответа от агента")

    return _attach_identity_receipt(result, device_id=device_id, dev=dev)


def get_file_link_fn(device_id: str, file_path: str, user_id: int = 0) -> str:
    """Create a download link for a file (for LLM use)."""
    return create_download_link(device_id, file_path, user_id=user_id)


def _snapshot_command_for_device(device_info: dict) -> str:
    if "windows" not in (device_info.get("os", "") or "").lower():
        return (
            "python3 - <<'PY'\n"
            "import json, os, platform, getpass, subprocess\n"
            "def run(cmd):\n"
            "    try: return subprocess.check_output(cmd, text=True).strip()\n"
            "    except Exception: return ''\n"
            "print(json.dumps({'observed_hostname': platform.node(), 'observed_computer_name': platform.node(), "
            "'observed_machine_guid': run(['cat','/etc/machine-id']), 'observed_username': getpass.getuser(), "
            "'os_caption': platform.platform(), 'os_version': platform.version(), 'cpu': platform.processor(), "
            "'process_count': len([p for p in os.listdir('/proc') if p.isdigit()])}))\n"
            "PY"
        )
    return (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$cs=Get-CimInstance Win32_ComputerSystem; $os=Get-CimInstance Win32_OperatingSystem; "
        "$prod=Get-CimInstance Win32_ComputerSystemProduct; $bios=Get-CimInstance Win32_BIOS; "
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$disks=Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | ForEach-Object { "
        "[pscustomobject]@{drive=$_.DeviceID; total_gb=[math]::Round($_.Size/1GB,2); free_gb=[math]::Round($_.FreeSpace/1GB,2)} }; "
        "[pscustomobject]@{observed_hostname=[System.Net.Dns]::GetHostName(); observed_computer_name=$env:COMPUTERNAME; "
        "observed_machine_guid=$prod.UUID; bios_serial=$bios.SerialNumber; observed_username=[Environment]::UserName; "
        "os_caption=$os.Caption; os_version=$os.Version; os_build=$os.BuildNumber; cpu=$cpu.Name; "
        "ram_total_gb=[math]::Round($cs.TotalPhysicalMemory/1GB,2); ram_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,2); "
        "disks=$disks; process_count=@(Get-Process).Count; uptime=((Get-Date)-$os.LastBootUpTime).ToString()} | ConvertTo-Json -Depth 5 -Compress"
    )


def _parse_snapshot_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return {}


async def collect_device_live_snapshot(device_id: str, user_id: int | None = None) -> dict:
    dev = devices.get(device_id)
    if not dev and user_id is not None:
        dev = devices.get(_dk(user_id, device_id))
        if dev:
            device_id = _dk(user_id, device_id)
    target_device_id = _short_did(device_id)
    collected_at = _utc_now_iso()
    if not dev or not dev.get("ws"):
        receipt = _build_identity_receipt(device_id=device_id, dev=dev, observed={"collected_at": collected_at})
        return {
            "device_id": device_id,
            "target_device_id": target_device_id,
            "status": "unavailable",
            "snapshot": None,
            "identity_receipt": receipt,
            "answer": f"target_device_id={target_device_id}: fresh state unavailable",
            "commands": [],
        }

    command = _snapshot_command_for_device(dev.get("info", {}))
    command_entry = {
        "action": "collect_live_snapshot",
        "command": command,
        "device_id": target_device_id,
        "target_device_id": target_device_id,
        "hostname": dev.get("info", {}).get("hostname") or target_device_id,
        "collected_at": collected_at,
        "result": None,
    }
    try:
        result = await send_command_to_agent(device_id, "execute_cmd", {"command": command, "timeout": 30}, user_id=user_id)
        observed = _parse_snapshot_stdout(str(result.get("stdout") or ""))
        observed["collected_at"] = collected_at
        receipt = _build_identity_receipt(
            device_id=device_id,
            dev=dev,
            observed=observed,
            profile=get_device_profile(target_device_id),
        )
        result = dict(result)
        result["identity_receipt"] = receipt
        command_entry["result"] = result
        if result.get("returncode") not in (None, 0, "0") or result.get("error"):
            return {
                "device_id": device_id,
                "target_device_id": target_device_id,
                "status": "unavailable",
                "snapshot": None,
                "identity_receipt": receipt,
                "answer": f"target_device_id={target_device_id}: fresh state unavailable",
                "commands": [command_entry],
            }
        if receipt.get("identity_status") == "mismatch":
            return {
                "device_id": device_id,
                "target_device_id": target_device_id,
                "status": "routing_mismatch",
                "snapshot": None,
                "identity_receipt": receipt,
                "answer": _format_identity_mismatch(receipt),
                "commands": [command_entry],
            }
        return {
            "device_id": device_id,
            "target_device_id": target_device_id,
            "status": "ok",
            "snapshot": observed,
            "identity_receipt": receipt,
            "answer": _format_snapshot_answer(target_device_id, receipt, observed),
            "commands": [command_entry],
        }
    except Exception as exc:
        receipt = _build_identity_receipt(device_id=device_id, dev=dev, observed={"collected_at": collected_at})
        command_entry["result"] = {"error": str(exc), "identity_receipt": receipt}
        return {
            "device_id": device_id,
            "target_device_id": target_device_id,
            "status": "unavailable",
            "snapshot": None,
            "identity_receipt": receipt,
            "answer": f"target_device_id={target_device_id}: fresh state unavailable ({exc})",
            "commands": [command_entry],
        }


def _format_identity_mismatch(receipt: dict) -> str:
    return (
        f"target_device_id={receipt.get('target_device_id')} returned "
        f"observed_hostname={receipt.get('observed_hostname') or receipt.get('observed_computer_name')}; "
        "requires agent registration/device_id check"
    )


def _format_snapshot_answer(target_device_id: str, receipt: dict, snapshot: dict) -> str:
    fields = [
        f"target_device_id={target_device_id}",
        f"observed_hostname={receipt.get('observed_hostname') or receipt.get('observed_computer_name')}",
        f"identity_status={receipt.get('identity_status')}",
        f"os={snapshot.get('os_caption') or snapshot.get('os_version')}",
        f"cpu={snapshot.get('cpu')}",
        f"ram_total_gb={snapshot.get('ram_total_gb')}",
        f"ram_free_gb={snapshot.get('ram_free_gb')}",
        f"process_count={snapshot.get('process_count')}",
        f"uptime={snapshot.get('uptime')}",
    ]
    disks = snapshot.get("disks")
    if disks:
        fields.append(f"disks={json.dumps(disks, ensure_ascii=False)}")
    return "; ".join(field for field in fields if not field.endswith("=None"))


def _format_live_snapshot_summary(results: list[dict]) -> str:
    if not results:
        return "fresh state unavailable"
    return "\n".join(result.get("answer") or f"target_device_id={result.get('target_device_id')}: fresh state unavailable" for result in results)


async def run_nl_task(task_id: str, user_id: int, message: str, device_ids: list[str], chat_id: int):
    """
    Execute an NL task in the background.
    Single device => standard LLM cycle.
    Multiple devices => plan on first device, replay commands on the rest.
    """
    task = tasks[task_id]
    task["current_step"] = "ИРУ думает..."
    is_broadcast = len(device_ids) > 1
    task_modes = task.get("modes") or {}
    plan_declined_for_request = bool(task_modes.get("plan_declined")) or is_plan_declined(chat_id, message)
    print(f"[run_nl_task] START task={task_id[:8]}, user={user_id}, devices={device_ids}")

    async def run_on_device(device_id: str):
        dev = devices.get(device_id)
        if not dev or dev.get("user_id") != user_id:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Устройство '{device_id}' не найдено или нет доступа",
                "commands": [],
            }

        device_info = dev.get("info", {})
        user_devs = get_user_devices(user_id)
        all_devices_info = {did: {"info": value.get("info", {})} for did, value in user_devs.items()}
        chat_history = get_messages(chat_id, limit=50)
        device_profile = get_device_profile(_short_did(device_id))
        autonomous_flag = bool(task_modes.get("autonomous"))

        async def send_fn(target_device_id, action, params):
            target_dk = _dk(user_id, target_device_id) if ":" not in target_device_id else target_device_id
            target_dev = devices.get(target_dk)
            if not target_dev or target_dev.get("user_id") != user_id:
                raise RuntimeError(f"Нет доступа к устройству '{target_device_id}'")
            return await send_command_to_agent(
                target_dk,
                action,
                params,
                user_id=user_id,
                skip_confirm=autonomous_flag,
            )

        def file_link(dev_id: str, path: str) -> str:
            return get_file_link_fn(dev_id, path, user_id=user_id)

        try:
            await _probe_python_toolchain_if_needed(
                message=message,
                device_id=device_id,
                device_info=device_info,
                dev=dev,
                user_id=user_id,
                send_fn=send_fn,
            )
            result = await process_nl_command(
                user_message=message,
                device_id=_short_did(device_id),
                device_info=device_info,
                all_devices={_short_did(k): v for k, v in all_devices_info.items()},
                send_command_fn=send_fn,
                get_file_link_fn=file_link,
                chat_history=chat_history,
                device_profile=device_profile,
                modes=task_modes,
                user_id=user_id,
                chat_id=chat_id,
                poll_task_id=task_id,
            )
            return {
                "device_id": device_id,
                "status": "ok",
                "answer": result.get("answer", ""),
                "commands": result.get("commands", []),
                "tasks": result.get("tasks", []),
            }
        except ConfirmationRequired as confirm:
            return {
                "device_id": device_id,
                "status": "confirm",
                "answer": confirm.answer,
                "commands": confirm.commands_log,
                "confirm_data": {
                    "command": confirm.command,
                    "device_id": confirm.device_id,
                    "params": confirm.params,
                },
            }
        except httpx.HTTPStatusError as exc:
            print(f"[run_nl_task] LLM HTTP error on device={device_id}: {exc.response.status_code}")
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка LLM API: {exc.response.status_code}",
                "commands": [],
            }
        except Exception as exc:
            print(f"[run_nl_task] ERROR on device={device_id}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            error_text = str(exc).strip() or type(exc).__name__
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Ошибка: {error_text}" if error_text else "Произошла внутренняя ошибка. Попробуйте ещё раз.",
                "commands": [],
            }

    async def replay_commands_on_device(device_id: str, commands: list):
        dev = devices.get(device_id)
        if not dev or dev.get("user_id") != user_id:
            return {
                "device_id": device_id,
                "status": "error",
                "answer": f"Устройство '{device_id}' не найдено",
                "commands": [],
            }

        results = []
        for cmd in commands:
            cmd_text = cmd.get("command", "")
            if cmd_text.startswith("["):
                continue
            try:
                result = await send_command_to_agent(
                    device_id,
                    "execute_cmd",
                    {"command": cmd_text, "timeout": 30},
                    user_id=user_id,
                )
                results.append({"command": cmd_text, "device_id": device_id, "result": result})
            except Exception as exc:
                results.append({"command": cmd_text, "device_id": device_id, "result": {"error": str(exc)}})

        hostname = dev.get("info", {}).get("hostname", device_id)
        return {
            "device_id": device_id,
            "status": "ok",
            "answer": f"Команды выполнены на {hostname}",
            "commands": results,
        }

    if _is_device_state_request(message):
        snapshot_results = await asyncio.gather(
            *[collect_device_live_snapshot(device_id, user_id=user_id) for device_id in device_ids],
            return_exceptions=True,
        )
        combined_results = []
        combined_commands = []
        for device_id, item in zip(device_ids, snapshot_results):
            if isinstance(item, Exception):
                item = {
                    "device_id": device_id,
                    "target_device_id": _short_did(device_id),
                    "status": "unavailable",
                    "answer": f"target_device_id={_short_did(device_id)}: fresh state unavailable ({item})",
                    "commands": [],
                }
            task["results"][device_id] = item
            combined_results.append(item)
            combined_commands.extend(item.get("commands") or [])

        combined_answer = _format_live_snapshot_summary(combined_results)
        add_message(chat_id, "assistant", combined_answer, combined_commands)
        task["status"] = "done"
        task["answer"] = combined_answer
        task["commands"] = combined_commands
        task["tasks"] = []
        return

    is_pipeline = bool((task.get("modes") or {}).get("pipeline"))
    if not is_pipeline:
        if not plan_declined_for_request:
            kind, plan_desc = await classify_task_complexity(message)
            logger.info(
                "[classify] kind=%s plan_desc=%r user_id=%s message=%r",
                kind,
                plan_desc[:80] if plan_desc else "",
                user_id,
                message[:100],
            )
            if kind == "PLAN":
                task["plan_suggestion"] = plan_desc
                task["plan_original_request"] = message
                user_plan = get_user_plan(user_id)
                trial_used = get_plan_trial_used(user_id)

                if user_plan in ("pro", "business"):
                    task["auto_plan"] = True
                elif trial_used:
                    logger.info("[classify] FREE TRIAL EXHAUSTED – показываем upsell, user_id=%s", user_id)

                add_message(chat_id, "assistant", "Это сложная задача, нужен план.")
                task["status"] = "done"
                task["answer"] = ""
                task["commands"] = []
                return

    try:
        if is_broadcast:
            primary_result = await run_on_device(device_ids[0])
            task["results"][device_ids[0]] = primary_result

            all_commands = primary_result.get("commands", [])
            answers = []

            dev0 = devices.get(device_ids[0])
            hostname0 = dev0["info"].get("hostname", device_ids[0]) if dev0 else device_ids[0]
            answers.append(f"[{hostname0}] {primary_result.get('answer', '')}")

            if all_commands and len(device_ids) > 1:
                replay_results = await asyncio.gather(
                    *[replay_commands_on_device(did, all_commands) for did in device_ids[1:]],
                    return_exceptions=True,
                )
                for item in replay_results:
                    if isinstance(item, Exception):
                        answers.append(f"Ошибка: {str(item)}")
                    else:
                        task["results"][item["device_id"]] = item
                        if item.get("commands"):
                            all_commands.extend(item["commands"])
                        dev = devices.get(item["device_id"])
                        hostname = dev["info"].get("hostname", item["device_id"]) if dev else item["device_id"]
                        answers.append(f"[{hostname}] {item.get('answer', '')}")

            combined_answer = "\n\n".join(answers) if answers else "Готово."
            combined_commands = all_commands
            combined_tasks = primary_result.get("tasks", [])
        else:
            result = await run_on_device(device_ids[0])
            task["results"][device_ids[0]] = result
            if result.get("status") == "confirm":
                task["status"] = "confirm"
                task["answer"] = result.get("answer", "")
                task["commands"] = result.get("commands", [])
                task["confirm_data"] = result.get("confirm_data", {})
                task["confirm_data"]["chat_id"] = chat_id
                task["confirm_data"]["user_id"] = user_id
                return

            combined_answer = result.get("answer", "")
            combined_commands = result.get("commands", [])
            combined_tasks = result.get("tasks", [])

        suggest_match = _re.search(r"\[\[SUGGEST_REMEMBER:\s*(.+?)\s*\|\s*(\w+)\s*\]\]", combined_answer)
        if suggest_match:
            fact_text = suggest_match.group(1).strip()
            fact_category = suggest_match.group(2).strip()
            first_dev_id = device_ids[0] if device_ids else None
            first_profile = get_device_profile(_short_did(first_dev_id)) if first_dev_id else None
            python_receipt = resolve_python_toolchain(
                {
                    "device_id": _short_did(first_dev_id) if first_dev_id else None,
                    "machine_guid": (first_profile or {}).get("machine_guid"),
                },
                combined_commands,
            )
            allowed_fact, corrected_fact = validate_toolchain_fact_against_receipt(fact_text, python_receipt)
            if allowed_fact and not is_suggested_fact_declined(user_id, chat_id, corrected_fact or fact_text, fact_category):
                task["suggested_fact"] = {
                    "text": corrected_fact or fact_text,
                    "category": fact_category,
                }
                task["python_toolchain_receipt"] = python_receipt.to_dict()
            combined_answer = (
                combined_answer[:suggest_match.start()].rstrip() + combined_answer[suggest_match.end():]
            ).strip()

        plan_match = _re.search(r"\[\[SUGGEST_PLAN:\s*([^\[\]]+?)\s*\]\]", combined_answer)
        if plan_match:
            logger.warning(
                "[suggest_plan] остаточный маркер в ответе LLM — вырезаю, user_id=%s, chat_id=%s",
                user_id,
                chat_id,
            )
            combined_answer = (combined_answer[:plan_match.start()].rstrip() + combined_answer[plan_match.end():]).strip()

        if plan_declined_for_request and not combined_commands:
            normalized_answer = (combined_answer or "").strip().lower().rstrip(".!")
            if plan_match or normalized_answer in {"", "готово"}:
                combined_answer = "План отключён для этого запроса. Продолжите без режима плана или уточните команду."

        combined_answer = strip_markdown(combined_answer)
        combined_answer = enforce_trusted_answer(combined_answer, combined_commands)
        add_message(chat_id, "assistant", combined_answer, combined_commands)

        try:
            from .database import get_db
        except ImportError:
            from database import get_db

        try:
            with get_db() as conn:
                row = conn.execute("SELECT data_consent FROM users WHERE id = ?", (user_id,)).fetchone()
            if row and row["data_consent"]:
                first_dev = devices.get(device_ids[0]) if device_ids else None
                dev_info = first_dev.get("info", {}) if first_dev else {}
                os_info = dev_info.get("os", "")
                hostname_info = dev_info.get("hostname", "")
                method_info = "powershell" if "windows" in os_info.lower() else "bash"
                add_training_record(
                    user_id=user_id,
                    chat_id=chat_id,
                    input_text=message,
                    os_info=os_info,
                    hostname=hostname_info,
                    method=method_info,
                    running_processes=[],
                    commands=combined_commands,
                    success=True,
                )
        except Exception as exc:
            print(f"[training] Ошибка записи: {exc}")

        task["status"] = "done"
        task["answer"] = combined_answer
        task["commands"] = combined_commands
        task["tasks"] = combined_tasks
    except Exception as exc:
        print(f"[run_nl_task] FATAL task={task_id[:8]}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        error_text = str(exc).strip() or type(exc).__name__
        task["status"] = "error"
        task["answer"] = f"Ошибка: {error_text}" if error_text else "Произошла внутренняя ошибка. Попробуйте ещё раз."
        task["commands"] = []


async def run_onboarding_task(task_id: str, user_id: int, message: str, chat_id: int):
    """Background task for onboarding mode when no devices are connected."""
    task = tasks[task_id]
    task["current_step"] = "ИРУ думает..."
    try:
        chat_history = get_messages(chat_id, limit=50)
        result = await process_onboarding_message(user_message=message, chat_history=chat_history)
        answer = result.get("answer", "")
        task["status"] = "done"
        task["answer"] = answer
        task["commands"] = []
        add_message(chat_id, "assistant", answer)
    except Exception as exc:
        task["status"] = "error"
        task["answer"] = f"Ошибка: {str(exc)}"
        task["commands"] = []
