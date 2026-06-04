import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

try:
    from . import database as db  # type: ignore
    from .answer_auditor import audit_answer_payload  # type: ignore
    from .answer_repair import run_answer_only_repair_turn  # type: ignore
    from .controller_budget import BUDGET_GUARD_ERROR, CommandBudget, budget_guard_entry  # type: ignore
    from .controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_chat_messages,
        set_current_step,
    )
    from .python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from .python_toolchain import (  # type: ignore
        python_toolchain_from_runtime_summary,
        resolve_python_toolchain,
        rewrite_python_app_launch_command,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )
    from .memory_tools import MEMORY_TOOL_NAMES, run_memory_tool  # type: ignore
    from .tool_registry import list_tools, tool_log_entry, tool_log_fields  # type: ignore
    from .tool_repeat_guard import (  # type: ignore
        duplicate_read_only_tool_message,
        find_prior_successful_read_only_tool_step,
        mark_read_only_tool_step,
    )
    from .run_journal import (  # type: ignore
        GROUNDED_CORRECTION,
        INSUFFICIENT_EVIDENCE_CORRECTION,
        ONE_TOOL_CORRECTION,
        RAW_CONTENT_CORRECTION,
        ProtocolValidationError,
        append_answer_step,
        append_tool_step,
        is_answer_clarification_tool,
        is_answer_confirmation_tool,
        is_answer_failure_tool,
        is_answer_text_tool,
        is_terminal_answer_tool,
        make_run_step,
        validate_answer_confirmation_payload,
        validate_answer_report_failure_payload,
        validate_answer_text_payload,
        validate_tool_call_batch,
        wrap_tool_result_for_llm,
    )
except ImportError:
    import database as db  # type: ignore
    from answer_auditor import audit_answer_payload  # type: ignore
    from answer_repair import run_answer_only_repair_turn  # type: ignore
    from controller_budget import BUDGET_GUARD_ERROR, CommandBudget, budget_guard_entry  # type: ignore
    from controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_chat_messages,
        set_current_step,
    )
    from python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from python_toolchain import (  # type: ignore
        python_toolchain_from_runtime_summary,
        resolve_python_toolchain,
        rewrite_python_app_launch_command,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )
    from memory_tools import MEMORY_TOOL_NAMES, run_memory_tool  # type: ignore
    from tool_registry import list_tools, tool_log_entry, tool_log_fields  # type: ignore
    from tool_repeat_guard import (  # type: ignore
        duplicate_read_only_tool_message,
        find_prior_successful_read_only_tool_step,
        mark_read_only_tool_step,
    )
    from run_journal import (  # type: ignore
        GROUNDED_CORRECTION,
        INSUFFICIENT_EVIDENCE_CORRECTION,
        ONE_TOOL_CORRECTION,
        RAW_CONTENT_CORRECTION,
        ProtocolValidationError,
        append_answer_step,
        append_tool_step,
        is_answer_clarification_tool,
        is_answer_confirmation_tool,
        is_answer_failure_tool,
        is_answer_text_tool,
        is_terminal_answer_tool,
        make_run_step,
        validate_answer_confirmation_payload,
        validate_answer_report_failure_payload,
        validate_answer_text_payload,
        validate_tool_call_batch,
        wrap_tool_result_for_llm,
    )


def _training_context(device_info: dict) -> dict:
    return {
        "os": device_info.get("os", ""),
        "hostname": device_info.get("hostname", ""),
        "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
    }


def _command_log_entry(action: str, command: str, target_device: str, device_info: dict, result: dict, iteration: int) -> dict:
    hostname = device_info.get("hostname") or target_device
    entry = {
        "action": action,
        "command": command,
        "device_id": target_device,
        "target_device_id": target_device,
        "hostname": hostname,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "iteration": iteration,
    }
    entry.update(tool_log_fields(action, result, command, target_device))
    return entry


APP_WINDOW_ACTIONS = {
    "window_list": "window.list",
    "window_find": "window.find",
    "window_verify": "window.verify",
    "window_focus": "window.focus",
    "window_close": "window.close",
    "app_launch": "app.launch",
    "app_verify_launch": "app.verify_launch",
    "app_close": "app.close",
}


async def _run_web_search(cfg: dict, query: str, max_results: int) -> dict:
    tavily_key = cfg.get("tavily_api_key")
    if not tavily_key:
        return {"error": "tavily_api_key не настроен в llm_config.json на сервере"}
    if not query:
        return {"error": "Пустой запрос"}

    try:
        tavily_data = None
        async with httpx.AsyncClient(timeout=20.0) as tavily_client:
            for tavily_attempt in range(2):
                try:
                    tavily_resp = await tavily_client.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": tavily_key,
                            "query": query,
                            "max_results": max_results,
                            "search_depth": "basic",
                            "include_answer": True,
                        },
                    )
                    tavily_resp.raise_for_status()
                    tavily_data = tavily_resp.json()
                    break
                except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as tavily_exc:
                    is_5xx = isinstance(tavily_exc, httpx.HTTPStatusError) and tavily_exc.response.status_code >= 500
                    is_net = isinstance(tavily_exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout))
                    if (is_5xx or is_net) and tavily_attempt == 0:
                        print(f"[llm] tavily retry: {type(tavily_exc).__name__}")
                        await asyncio.sleep(2)
                        continue
                    raise
        if tavily_data is None:
            return {"error": "Поиск временно недоступен. Попробуйте позже."}

        return {
            "answer": tavily_data.get("answer"),
            "results": [
                {
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "content": (result.get("content") or "")[:800],
                }
                for result in (tavily_data.get("results") or [])[:max_results]
            ],
        }
    except Exception as exc:
        return {"error": f"Поиск временно недоступен: {exc}"}


async def process_non_pipeline_command(
    *,
    user_message: str,
    device_id: str,
    device_info: dict,
    send_command_fn,
    get_file_link_fn,
    chat_history: list[dict] | None,
    user_id: int | None,
    chat_id: int | None,
    modes: dict,
    poll_task_id: str | None,
    cfg: dict,
    system_msg: str,
    machine_guid: str | None,
    mem_user_id: str | None,
    non_pipeline_tools: list[dict],
    max_iterations: int,
    pick_model_fn,
    chat_completion_request_fn,
    device_tool_fn=None,
) -> dict:
    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    commands_log = []

    def add_correction(correction: str):
        messages.append({"role": "user", "content": correction})

    def append_entry(entry: dict) -> dict:
        return append_tool_step(commands_log, entry)

    def append_tool_message(tool_call_id: str, entry: dict):
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(wrap_tool_result_for_llm(entry), ensure_ascii=False)[:4000],
        })

    command_budget = CommandBudget()
    device_profile = db.get_device_profile(device_id)
    python_receipt = (
        python_toolchain_from_runtime_summary((device_profile or {}).get("python_runtime_summary"), device_id=device_id)
        or resolve_python_toolchain({"device_id": device_id, "machine_guid": machine_guid}, commands_log)
    )
    model = pick_model_fn(cfg, modes)
    base_model = cfg.get("model", "deepseek-chat")
    print(f"[llm] выбрана модель: {model} (base={base_model}, autonomous={bool(modes.get('autonomous'))})")

    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for iteration in range(max_iterations):
            set_current_step(poll_task_id, "ИРУ думает...")
            print(f"[llm] iteration {iteration + 1}/{max_iterations}, messages={len(messages)}")
            try:
                data = await chat_completion_request_fn(
                    client=client,
                    cfg=cfg,
                    model=model,
                    messages=messages,
                    tools=non_pipeline_tools,
                    max_tokens=cfg.get("max_tokens", 4096),
                    tool_choice="required",
                )
            except httpx.HTTPStatusError as exc:
                print(f"[llm] HTTP error: {exc.response.status_code} {exc.response.text[:500]}")
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                print(f"[llm] network error: {type(exc).__name__}: {exc}")
                raise RuntimeError("Сервис ИИ временно недоступен. Попробуйте через минуту.")
            except Exception as exc:
                print(f"[llm] request error: {type(exc).__name__}: {exc}")
                raise

            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason", "?")
            assistant_msg = choice["message"]
            content_preview = (assistant_msg.get("content") or "")[:200]
            tool_calls = assistant_msg.get("tool_calls")
            print(
                f"[llm] response: finish_reason={finish_reason}, "
                f"has_content={'yes' if content_preview else 'no'}, "
                f"tool_calls={len(tool_calls) if tool_calls else 0}, "
                f"content_preview={content_preview[:100]!r}"
            )

            if finish_reason == "length":
                print("[llm] WARNING: response truncated (finish_reason=length)")
                if tool_calls:
                    if iteration < max_iterations - 1:
                        messages.append({
                            "role": "user",
                            "content": "Предыдущий ответ был обрезан. Используй более короткие команды. Попробуй снова.",
                        })
                        print(f"[llm] retrying after truncation (iteration {iteration + 1})")
                        continue
                    return {
                        "answer": "Не удалось выполнить задачу: ответ ИИ слишком длинный и был обрезан. Попробуй сформулировать задачу проще или разбить на несколько шагов.",
                        "commands": commands_log,
                        "tasks": [],
                        "training_context": _training_context(device_info),
                    }

                add_correction(RAW_CONTENT_CORRECTION)
                continue

            content_text = assistant_msg.get("content") or ""
            suggest_plan_match = re.search(r"\[\[SUGGEST_PLAN:\s*[^\[\]]+?\s*\]\]", content_text)
            if suggest_plan_match and assistant_msg.get("tool_calls"):
                dropped = len(assistant_msg["tool_calls"])
                cmds_preview = ", ".join(tc["function"]["name"] for tc in assistant_msg["tool_calls"][:5])
                print(
                    f"[llm] SUGGEST_PLAN guard: маркер найден в content, "
                    f"ОТМЕНЯЮ {dropped} tool_calls [{cmds_preview}] "
                    f"(user_id={user_id}, chat_id={chat_id})"
                )
                assistant_msg = {
                    "role": assistant_msg.get("role", "assistant"),
                    "content": content_text,
                }

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                add_correction(RAW_CONTENT_CORRECTION)
                continue

            try:
                tool_call = validate_tool_call_batch(tool_calls)
            except ProtocolValidationError as exc:
                print(f"[tool-only] invalid assistant output: {exc.message}")
                add_correction(exc.correction)
                continue

            fn_name = tool_call["function"]["name"]
            try:
                fn_args_preview = json.loads(tool_call["function"].get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                print(f"[llm] BAD JSON in tool args: {exc}, raw={tool_call['function'].get('arguments', '')[:300]}")
                add_correction(f"Tool arguments must be valid JSON. {ONE_TOOL_CORRECTION}")
                continue

            if is_terminal_answer_tool(fn_name):
                target_device = device_id
                try:
                    if is_answer_text_tool(fn_name):
                        answer_payload = validate_answer_text_payload(fn_args_preview, commands_log)
                        audit_ok, audit_reason, audit_infra_error = await audit_answer_payload(
                            client=client,
                            cfg=cfg,
                            chat_completion_request_fn=chat_completion_request_fn,
                            user_request=user_message,
                            current_run_journal=commands_log,
                            answer_payload=answer_payload,
                        )
                        if audit_infra_error:
                            auditor_entry = make_run_step(
                                journal=commands_log,
                                tool_name="answer_auditor",
                                result={"error": audit_reason},
                                command="[system] answer_auditor",
                                target_device_id=target_device,
                                hostname=device_info.get("hostname") or target_device,
                                iteration=iteration + 1,
                                tool_type="system",
                                status="failed",
                                summary="auditor_error",
                            )
                            commands_log.append(auditor_entry)
                            return {
                                "answer": "Не удалось безопасно проверить корректность финального ответа. Повтори запрос.",
                                "commands": commands_log,
                                "tasks": [],
                                "training_context": _training_context(device_info),
                            }
                        if not audit_ok:
                            print(f"[answer-auditor] rejected answer_text: {audit_reason}")
                            add_correction(GROUNDED_CORRECTION)
                            continue
                        append_answer_step(
                            commands_log,
                            fn_name,
                            answer_payload,
                            target_device_id=target_device,
                            hostname=device_info.get("hostname") or target_device,
                            iteration=iteration + 1,
                        )
                        return {
                            "answer": answer_payload["text"],
                            "commands": commands_log,
                            "tasks": [],
                            "training_context": _training_context(device_info),
                        }
                    if is_answer_clarification_tool(fn_name):
                        question = str(fn_args_preview.get("question") or "").strip()
                        reason = str(fn_args_preview.get("reason") or "").strip()
                        if not question or not reason:
                            raise ProtocolValidationError("answer_ask_clarification requires question and reason", GROUNDED_CORRECTION)
                        append_answer_step(
                            commands_log,
                            fn_name,
                            fn_args_preview,
                            target_device_id=target_device,
                            hostname=device_info.get("hostname") or target_device,
                            iteration=iteration + 1,
                        )
                        return {
                            "answer": question,
                            "commands": commands_log,
                            "tasks": [],
                            "training_context": _training_context(device_info),
                        }
                    if is_answer_failure_tool(fn_name):
                        payload = validate_answer_report_failure_payload(fn_args_preview, commands_log)
                        append_answer_step(
                            commands_log,
                            fn_name,
                            payload,
                            target_device_id=target_device,
                            hostname=device_info.get("hostname") or target_device,
                            iteration=iteration + 1,
                        )
                        return {
                            "answer": str(payload.get("message") or ""),
                            "commands": commands_log,
                            "tasks": [],
                            "training_context": _training_context(device_info),
                        }
                    if is_answer_confirmation_tool(fn_name):
                        payload = validate_answer_confirmation_payload(fn_args_preview, commands_log)
                        append_answer_step(
                            commands_log,
                            fn_name,
                            payload,
                            target_device_id=target_device,
                            hostname=device_info.get("hostname") or target_device,
                            iteration=iteration + 1,
                        )
                        raise ConfirmationRequired(
                            command=payload.get("command_preview") or payload.get("action") or "",
                            device_id=target_device,
                            params={
                                "action": payload.get("action"),
                                "risk": payload.get("risk"),
                                "command_preview": payload.get("command_preview"),
                                "basis": payload.get("basis") or [],
                            },
                            answer=payload.get("message") or "Confirmation required",
                            commands_log=commands_log,
                        )
                except ProtocolValidationError as exc:
                    print(f"[tool-only] invalid answer tool: {exc.message}")
                    add_correction(exc.correction)
                    continue

            assistant_msg["tool_calls"] = [tool_call]
            messages.append(assistant_msg)
            tool_calls = [tool_call]

            for tool_call in tool_calls:
                fn_name = tool_call["function"]["name"]
                try:
                    fn_args = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    print(f"[llm] BAD JSON in tool args: {exc}, raw={tool_call['function']['arguments'][:300]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps({"error": f"Ошибка парсинга аргументов: {exc}"}, ensure_ascii=False),
                    })
                    continue

                requested_device_id = fn_args.pop("device_id", None)
                target_device = requested_device_id or device_id
                repeat_guard_args = dict(fn_args)
                if requested_device_id:
                    repeat_guard_args["device_id"] = requested_device_id
                print(
                    f"[llm] tool_call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:250]}) "
                    f"-> device={target_device}"
                )

                prior_read_only_step = find_prior_successful_read_only_tool_step(commands_log, fn_name, repeat_guard_args)
                if prior_read_only_step:
                    duplicate_message = duplicate_read_only_tool_message(fn_name, prior_read_only_step)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(duplicate_message, ensure_ascii=False)[:4000],
                    })
                    previous_step_id = duplicate_message["previous_step_id"]
                    add_correction(
                        f"You already have current-run evidence from {previous_step_id}. "
                        "Do not call the same read-only tool again. Call answer_text."
                    )
                    continue

                rewrite_error = None
                if fn_name == "execute_cmd":
                    rewritten_command, rewrite_error = rewrite_python_command(fn_args.get("command", ""), python_receipt)
                    fn_args["command"] = rewritten_command
                elif fn_name == "app_launch":
                    rewritten_launch, rewrite_error = rewrite_python_app_launch_command(fn_args.get("command", ""), python_receipt)
                    fn_args.update(rewritten_launch)

                budget_error = command_budget.register(fn_name, fn_args.get("command", ""))
                if budget_error:
                    append_entry(budget_guard_entry(budget_error))
                    return {
                        "answer": budget_error,
                        "commands": commands_log,
                        "tasks": [],
                        "training_context": _training_context(device_info),
                    }

                if rewrite_error:
                    tool_result = {"error": rewrite_error}
                    command_error = classify_command_error(tool_result, fn_args.get("command", ""))
                    if command_error.get("error_type") != "none":
                        tool_result = dict(tool_result)
                        tool_result["command_error"] = command_error
                        tool_result["error_type"] = command_error.get("error_type")
                        if command_error.get("missing_packages"):
                            tool_result["missing_packages"] = command_error["missing_packages"]

                    entry = append_entry(_command_log_entry(
                        fn_name,
                        fn_args.get("command", ""),
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))
                    append_tool_message(tool_call["id"], entry)
                    continue

                if fn_name == "web_search":
                    query = fn_args.get("query", "")[:60]
                    set_current_step(poll_task_id, f"Ищу в интернете: {query}")
                elif fn_name == "system_list_tools":
                    set_current_step(poll_task_id, "Checking tool registry")
                elif fn_name in MEMORY_TOOL_NAMES:
                    set_current_step(poll_task_id, "Checking memory")
                elif fn_name.startswith("device_"):
                    set_current_step(poll_task_id, "Running device tool")
                elif fn_name.startswith("window_"):
                    set_current_step(poll_task_id, "Checking windows")
                elif fn_name.startswith("app_"):
                    set_current_step(poll_task_id, "Running app tool")
                elif fn_name == "write_content":
                    name = fn_args.get("path", "")[:60]
                    set_current_step(poll_task_id, f"Создаю файл {name}")
                elif fn_name == "execute_cmd":
                    set_current_step(poll_task_id, "Выполняю команду на устройстве")
                elif fn_name == "get_file_link":
                    set_current_step(poll_task_id, "Формирую ссылку на файл")

                if fn_name == "system_list_tools":
                    tool_result = list_tools(fn_args.get("category", "all"))
                    append_entry(tool_log_entry(
                        fn_name,
                        tool_result,
                        command="[tool] system.list_tools",
                        target_device_id=target_device,
                        hostname=device_info.get("hostname") or target_device,
                        iteration=iteration + 1,
                    ))

                elif fn_name in MEMORY_TOOL_NAMES:
                    memory_args = dict(fn_args)
                    if requested_device_id:
                        memory_args["device_id"] = requested_device_id
                    tool_result = run_memory_tool(
                        fn_name,
                        memory_args,
                        user_id=mem_user_id or user_id,
                    )
                    append_entry(tool_log_entry(
                        fn_name,
                        tool_result,
                        command=f"[tool] {fn_name}",
                        target_device_id=None,
                        hostname=None,
                        iteration=iteration + 1,
                    ))

                elif fn_name.startswith("device_"):
                    if device_tool_fn is None:
                        tool_result = {"error": "device tools are unavailable in this route"}
                    else:
                        try:
                            tool_result = await device_tool_fn(fn_name, {**fn_args, "device_id": target_device})
                        except Exception as exc:
                            tool_result = {"error": str(exc)}
                    if fn_name in {"device_check_runtime", "device_prepare_runtime", "device_repair_runtime"} and isinstance(tool_result, dict) and not tool_result.get("error"):
                        runtime_summary = tool_result.get("runtime_summary") or tool_result.get("summary")
                        refreshed = python_toolchain_from_runtime_summary(runtime_summary, device_id=target_device)
                        if refreshed:
                            python_receipt = refreshed
                    append_entry(tool_log_entry(
                        fn_name,
                        tool_result,
                        command=f"[tool] {fn_name}",
                        target_device_id=target_device,
                        hostname=device_info.get("hostname") or target_device,
                        iteration=iteration + 1,
                    ))

                elif fn_name in APP_WINDOW_ACTIONS:
                    agent_action = APP_WINDOW_ACTIONS[fn_name]
                    try:
                        tool_result = await send_command_fn(target_device, agent_action, fn_args)
                    except Exception as exc:
                        err_str = str(exc)
                        print(f"[llm] {fn_name} EXCEPTION: {type(exc).__name__}: {err_str[:200]}")
                        if "CONFIRM_REQUIRED" in err_str:
                            raise ConfirmationRequired(
                                command=f"{agent_action}: {fn_args.get('command') or fn_args.get('pid') or fn_args.get('title_contains') or ''}",
                                device_id=target_device,
                                params=fn_args,
                                answer="РРЅСЃС‚СЂСѓРјРµРЅС‚ РѕРєРЅР°/РїСЂРёР»РѕР¶РµРЅРёСЏ С‚СЂРµР±СѓРµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ",
                                commands_log=commands_log,
                            )
                        tool_result = {"error": err_str}
                    append_entry(tool_log_entry(
                        fn_name,
                        tool_result,
                        command=fn_args.get("command") or f"[tool] {agent_action}",
                        target_device_id=target_device,
                        hostname=device_info.get("hostname") or target_device,
                        iteration=iteration + 1,
                    ))

                elif fn_name == "execute_cmd":
                    is_long_running = fn_args.pop("long_running", False)
                    try:
                        if is_long_running:
                            fn_args["timeout"] = 5
                            try:
                                tool_result = await send_command_fn(target_device, "execute_cmd", fn_args)
                            except Exception as long_running_exc:
                                if "Таймаут" in str(long_running_exc):
                                    tool_result = {
                                        "stdout": "Приложение запущено (long_running)",
                                        "stderr": "",
                                        "returncode": 0,
                                        "error": None,
                                    }
                                else:
                                    raise
                        else:
                            tool_result = await send_command_fn(target_device, "execute_cmd", fn_args)
                        print(
                            f"[llm] cmd result: returncode={tool_result.get('returncode')}, "
                            f"stdout={tool_result.get('stdout', '')[:100]!r}, "
                            f"stderr={tool_result.get('stderr', '')[:100]!r}"
                        )
                    except Exception as exc:
                        err_str = str(exc)
                        print(f"[llm] cmd EXCEPTION: {type(exc).__name__}: {err_str[:200]}")
                        if "CONFIRM_REQUIRED" in err_str:
                            raise ConfirmationRequired(
                                command=fn_args.get("command", ""),
                                device_id=target_device,
                                params=fn_args,
                                answer="Команда требует подтверждения",
                                commands_log=commands_log,
                            )
                        tool_result = {"error": err_str}

                    command_error = classify_command_error(tool_result, fn_args.get("command", ""))
                    if command_error.get("error_type") != "none":
                        tool_result = dict(tool_result)
                        tool_result["command_error"] = command_error
                        tool_result["error_type"] = command_error.get("error_type")
                        if command_error.get("missing_packages"):
                            tool_result["missing_packages"] = command_error["missing_packages"]

                    append_entry(_command_log_entry(
                        fn_name,
                        fn_args.get("command", ""),
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))
                    target_profile = db.get_device_profile(target_device)
                    python_receipt = (
                        python_toolchain_from_runtime_summary((target_profile or {}).get("python_runtime_summary"), device_id=target_device)
                        or resolve_python_toolchain(
                            {"device_id": target_device, "machine_guid": machine_guid},
                            commands_log,
                        )
                    )
                    env_guard_error = command_budget.observe_execute_result(
                        fn_args.get("command", ""),
                        tool_result,
                    )
                    if env_guard_error:
                        if is_recoverable_command_error(command_error):
                            tool_result["command_error"]["guard_message"] = env_guard_error
                            commands_log[-1]["result"] = tool_result
                        else:
                            append_entry(budget_guard_entry(env_guard_error))
                            return {
                                "answer": env_guard_error,
                                "commands": commands_log,
                                "tasks": [],
                                "training_context": _training_context(device_info),
                            }
                    if machine_guid and "error" not in tool_result:
                        try:
                            db.add_command_memory(
                                machine_guid=machine_guid,
                                device_id=target_device,
                                command=fn_args.get("command", ""),
                                intent=None,
                                exit_code=int(tool_result.get("returncode", -1)),
                                stdout=tool_result.get("stdout"),
                                stderr=tool_result.get("stderr"),
                                user_id=mem_user_id,
                            )
                        except Exception:
                            print("[llm] Failed to write command memory")

                elif fn_name == "write_content":
                    try:
                        tool_result = await send_command_fn(target_device, "write_content", fn_args)
                        print(f"[llm] write_content result: {str(tool_result)[:150]}")
                    except Exception as exc:
                        err_str = str(exc)
                        print(f"[llm] write_content EXCEPTION: {type(exc).__name__}: {err_str[:200]}")
                        if "CONFIRM_REQUIRED" in err_str:
                            raise ConfirmationRequired(
                                command=f"write_content: {fn_args.get('path', '')}",
                                device_id=target_device,
                                params=fn_args,
                                answer="Запись в файл требует подтверждения",
                                commands_log=commands_log,
                            )
                        tool_result = {"error": err_str}

                    preview = fn_args.get("content", "")[:60]
                    mode = "append" if fn_args.get("append") else "write"
                    append_entry(_command_log_entry(
                        fn_name,
                        f"[{mode}] {fn_args.get('path', '')} | {preview}...",
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))

                elif fn_name == "get_file_link":
                    try:
                        file_path = fn_args["file_path"]
                        url = get_file_link_fn(target_device, file_path)
                        tool_result = {"url": url, "file_path": file_path}
                    except Exception as exc:
                        tool_result = {"error": str(exc)}

                    append_entry({
                        "action": fn_name,
                        "command": f"[скачать] {fn_args.get('file_path', '')}",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                elif fn_name == "web_search":
                    query = fn_args.get("query", "").strip()
                    max_results = min(int(fn_args.get("max_results", 5) or 5), 10)
                    tool_result = await _run_web_search(cfg, query, max_results)
                    append_entry(_command_log_entry(
                        fn_name,
                        f"[web_search] {fn_args.get('query', '')[:80]}",
                        target_device,
                        device_info,
                        tool_result if "error" in tool_result else {"ok": True},
                        iteration + 1,
                    ))

                elif fn_name == "remember_fact":
                    if not mem_user_id:
                        tool_result = {"error": "Не удалось сохранить факт: пользователь не идентифицирован"}
                    else:
                        try:
                            allowed, corrected_fact = validate_toolchain_fact_against_receipt(
                                fn_args.get("text", ""),
                                python_receipt,
                            )
                            if not allowed:
                                raise ValueError("Toolchain memory fact requires a verified PythonToolchainReceipt")
                            fact_id = db.add_user_fact(
                                user_id=mem_user_id,
                                text=corrected_fact or fn_args.get("text", ""),
                                category=fn_args.get("category"),
                            )
                            tool_result = {"status": "ok", "fact_id": fact_id, "result": f"Запомнил факт о тебе (id={fact_id})"}
                        except Exception as exc:
                            print(f"[llm] remember_fact EXCEPTION: {exc}")
                            tool_result = {"error": str(exc)}
                    append_entry(_command_log_entry(
                        fn_name,
                        "[memory] remember_fact",
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))

                elif fn_name == "forget_fact":
                    if not mem_user_id:
                        tool_result = {"error": "Факт не найден"}
                    else:
                        try:
                            source = (fn_args.get("source") or "user").strip().lower()
                            ok = db.delete_memory_fact(mem_user_id, int(fn_args.get("fact_id", 0)), source, machine_guid)
                            tool_result = {"status": "ok", "result": "Факт удалён"} if ok else {"error": "Факт не найден"}
                        except Exception as exc:
                            print(f"[llm] forget_fact EXCEPTION: {exc}")
                            tool_result = {"error": str(exc)}
                    append_entry(_command_log_entry(
                        fn_name,
                        "[memory] forget_fact",
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))

                else:
                    tool_result = {"error": f"Неизвестная функция: {fn_name}"}

                if (
                    not commands_log
                    or commands_log[-1].get("iteration") != iteration + 1
                    or commands_log[-1].get("action") != fn_name
                ):
                    append_entry(_command_log_entry(
                        fn_name,
                        f"[tool] {fn_name}",
                        target_device,
                        device_info,
                        tool_result,
                        iteration + 1,
                    ))
                mark_read_only_tool_step(commands_log[-1], fn_name, repeat_guard_args)
                append_tool_message(tool_call["id"], commands_log[-1])

    print("[tool-only] max_iterations reached; attempting answer_text-only repair turn")
    repair_result = await run_answer_only_repair_turn(
        client=client,
        cfg=cfg,
        model=model,
        messages=messages,
        user_request=user_message,
        journal=commands_log,
        chat_completion_request_fn=chat_completion_request_fn,
        target_device_id=device_id,
        hostname=device_info.get("hostname") or device_id,
        iteration=max_iterations + 1,
    )
    if repair_result.get("ok"):
        return {
            "answer": repair_result["answer"],
            "commands": commands_log,
            "training_context": _training_context(device_info),
            "tasks": [],
        }

    append_entry(make_run_step(
        journal=commands_log,
        tool_name="tool_only_protocol",
        result={
            "error": "model did not choose an answer tool before max_iterations",
            "repair_reason": repair_result.get("reason"),
        },
        command="[system] tool_only_protocol",
        target_device_id=device_id,
        hostname=device_info.get("hostname") or device_id,
        tool_type="system",
        status="failed",
        summary="model did not choose answer tool",
    ))
    return {
        "answer": "Не удалось завершить задачу: модель не выбрала инструмент ответа.",
        "commands": commands_log,
        "training_context": _training_context(device_info),
        "tasks": [],
    }
