import asyncio
import base64
import json
import logging
import time
import traceback
import uuid
import re as _re

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

    return result


def get_file_link_fn(device_id: str, file_path: str, user_id: int = 0) -> str:
    """Create a download link for a file (for LLM use)."""
    return create_download_link(device_id, file_path, user_id=user_id)


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
