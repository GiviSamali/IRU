import asyncio
import json
import re

import httpx

try:
    from . import database as db  # type: ignore
    from .controller_budget import BUDGET_GUARD_ERROR, CommandBudget, budget_guard_entry  # type: ignore
    from .controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_chat_messages,
        set_current_step,
    )
    from .controller_trust import enforce_trusted_answer  # type: ignore
    from .python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from .python_toolchain import (  # type: ignore
        resolve_python_toolchain,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )
except ImportError:
    import database as db  # type: ignore
    from controller_budget import BUDGET_GUARD_ERROR, CommandBudget, budget_guard_entry  # type: ignore
    from controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_chat_messages,
        set_current_step,
    )
    from controller_trust import enforce_trusted_answer  # type: ignore
    from python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from python_toolchain import (  # type: ignore
        resolve_python_toolchain,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )


def _training_context(device_info: dict) -> dict:
    return {
        "os": device_info.get("os", ""),
        "hostname": device_info.get("hostname", ""),
        "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
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
) -> dict:
    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})

    commands_log = []
    command_budget = CommandBudget()
    python_receipt = resolve_python_toolchain({"device_id": device_id, "machine_guid": machine_guid}, commands_log)
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

                truncated_text = assistant_msg.get("content", "") or ""
                return {
                    "answer": truncated_text + "\n\n[Ответ был обрезан из-за ограничения длины. Попробуй задать более конкретный вопрос.]",
                    "commands": commands_log,
                    "tasks": [],
                    "training_context": _training_context(device_info),
                }

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

            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                final_answer = enforce_trusted_answer(
                    assistant_msg.get("content", "Готово."),
                    commands_log,
                )
                return {
                    "answer": final_answer,
                    "commands": commands_log,
                    "tasks": [],
                    "training_context": _training_context(device_info),
                }

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

                fn_args.pop("device_id", None)
                target_device = device_id
                print(
                    f"[llm] tool_call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:250]}) "
                    f"-> device={target_device}"
                )

                rewrite_error = None
                if fn_name == "execute_cmd":
                    rewritten_command, rewrite_error = rewrite_python_command(fn_args.get("command", ""), python_receipt)
                    fn_args["command"] = rewritten_command

                budget_error = command_budget.register(fn_name, fn_args.get("command", ""))
                if budget_error:
                    commands_log.append(budget_guard_entry(budget_error))
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

                    commands_log.append({
                        "action": fn_name,
                        "command": fn_args.get("command", ""),
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
                    })
                    continue

                if fn_name == "web_search":
                    query = fn_args.get("query", "")[:60]
                    set_current_step(poll_task_id, f"Ищу в интернете: {query}")
                elif fn_name == "write_content":
                    name = fn_args.get("path", "")[:60]
                    set_current_step(poll_task_id, f"Создаю файл {name}")
                elif fn_name == "execute_cmd":
                    set_current_step(poll_task_id, "Выполняю команду на устройстве")
                elif fn_name == "get_file_link":
                    set_current_step(poll_task_id, "Формирую ссылку на файл")

                if fn_name == "execute_cmd":
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

                    commands_log.append({
                        "action": fn_name,
                        "command": fn_args.get("command", ""),
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })
                    python_receipt = resolve_python_toolchain(
                        {"device_id": target_device, "machine_guid": machine_guid},
                        commands_log,
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
                            commands_log.append(budget_guard_entry(env_guard_error))
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
                    commands_log.append({
                        "action": fn_name,
                        "command": f"[{mode}] {fn_args.get('path', '')} | {preview}...",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                elif fn_name == "get_file_link":
                    try:
                        file_path = fn_args["file_path"]
                        url = get_file_link_fn(target_device, file_path)
                        tool_result = {"url": url, "file_path": file_path}
                    except Exception as exc:
                        tool_result = {"error": str(exc)}

                    commands_log.append({
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
                    commands_log.append({
                        "action": fn_name,
                        "command": f"[web_search] {fn_args.get('query', '')[:80]}",
                        "device_id": target_device,
                        "result": tool_result if "error" in tool_result else {"ok": True},
                        "iteration": iteration + 1,
                    })

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
                    commands_log.append({
                        "action": fn_name,
                        "command": "[memory] remember_fact",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

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
                    commands_log.append({
                        "action": fn_name,
                        "command": "[memory] forget_fact",
                        "device_id": target_device,
                        "result": tool_result,
                        "iteration": iteration + 1,
                    })

                else:
                    tool_result = {"error": f"Неизвестная функция: {fn_name}"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
                })

    return {
        "answer": "Достигнут лимит итераций. Последние результаты в логе.",
        "commands": commands_log,
        "training_context": _training_context(device_info),
        "tasks": [],
    }
