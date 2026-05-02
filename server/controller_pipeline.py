import asyncio
import json
import logging

import httpx

try:
    from . import database as db  # type: ignore
    from .controller_budget import CommandBudget, budget_guard_entry  # type: ignore
    from .controller_trust import enforce_trusted_answer  # type: ignore
except ImportError:
    import database as db  # type: ignore
    from controller_budget import CommandBudget, budget_guard_entry  # type: ignore
    from controller_trust import enforce_trusted_answer  # type: ignore

try:
    from .controller_shared import (
        ConfirmationRequired,
        build_chat_messages,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        collect_tasks,
        current_datetime_msk,
        push_tasks_view,
        set_current_step,
        strip_markdown,
    )
except ImportError:
    from controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_chat_messages,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        collect_tasks,
        current_datetime_msk,
        push_tasks_view,
        set_current_step,
        strip_markdown,
    )


PIPELINE_WORKER_MAX_ITERATIONS = 10
PIPELINE_MAX_STEPS = 10
logger = logging.getLogger(__name__)

_PIPELINE_MULTI_STEP_MARKERS = (
    " и ",
    " затем ",
    " потом ",
    " после ",
    " чтобы ",
    " сначала ",
    " проверь ",
    " создать ",
    " создай ",
    " сохранить ",
    " сохрани ",
    " запустить ",
    " запусти ",
    " установить ",
    " установи ",
    " скачать ",
    " скачай ",
    " открыть ",
    " открой ",
    " показать ",
    " покажи ",
    " дай ссыл",
    " not ",
    " but ",
)


def extract_json_payload(text: str):
    """Достать JSON-объект или массив из ответа модели."""
    if not text:
        return None

    raw = text.strip()
    candidates = [raw]

    start_obj = raw.find("{")
    end_obj = raw.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        candidates.append(raw[start_obj:end_obj + 1])

    start_arr = raw.find("[")
    end_arr = raw.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        candidates.append(raw[start_arr:end_arr + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def normalize_pipeline_plan(raw_plan, fallback_goal: str, default_device_id: str) -> dict:
    """Нормализовать план оркестратора к единому виду."""
    goal = fallback_goal.strip() or "Выполнить задачу"
    steps_raw = []
    if isinstance(raw_plan, dict):
        goal = str(raw_plan.get("goal") or raw_plan.get("title") or goal).strip() or goal
        steps_raw = raw_plan.get("steps") or []
    elif isinstance(raw_plan, list):
        steps_raw = raw_plan

    steps = []
    for idx, item in enumerate(steps_raw[:PIPELINE_MAX_STEPS]):
        if isinstance(item, str):
            title = item.strip()
            instruction = title
            success_criteria = ""
            step_device_id = default_device_id
        elif isinstance(item, dict):
            title = str(
                item.get("title")
                or item.get("step")
                or item.get("name")
                or item.get("instruction")
                or f"Шаг {idx + 1}"
            ).strip()
            instruction = str(
                item.get("instruction")
                or item.get("details")
                or item.get("objective")
                or item.get("task")
                or title
            ).strip()
            success_criteria = str(
                item.get("success_criteria")
                or item.get("success")
                or item.get("done_when")
                or ""
            ).strip()
            step_device_id = str(item.get("device_id") or default_device_id).strip() or default_device_id
        else:
            continue

        if not title:
            continue
        if not instruction:
            instruction = title

        steps.append({
            "title": title[:160],
            "instruction": instruction[:1400],
            "success_criteria": success_criteria[:400],
            "device_id": step_device_id,
        })

    if not steps:
        steps = [{
            "title": goal[:160],
            "instruction": fallback_goal.strip() or goal,
            "success_criteria": "",
            "device_id": default_device_id,
        }]

    return {
        "goal": goal[:200],
        "steps": steps,
    }


def should_force_multi_step_pipeline(plan: dict, user_message: str) -> bool:
    """Decide whether a one-step pipeline plan is too collapsed and needs expansion."""
    steps = plan.get("steps") or []
    if len(steps) != 1:
        return False

    msg = f" {strip_markdown(user_message or '').strip().lower()} "
    if not msg.strip():
        return False

    if any(marker in msg for marker in _PIPELINE_MULTI_STEP_MARKERS):
        return True

    if len(msg) >= 80 or len(msg.split()) >= 10:
        return True

    only_step = steps[0]
    instruction = str(only_step.get("instruction") or only_step.get("title") or "").strip().lower()
    if instruction and len(instruction) >= 60:
        overlap = sum(1 for token in instruction.split() if token in msg)
        if overlap >= min(8, max(3, len(instruction.split()) // 2)):
            return True

    return False


def pipeline_single_step_refine_prompt(shared: dict, user_message: str, plan: dict) -> str:
    """Prompt the orchestrator to re-split an over-collapsed one-step plan."""
    return f"""\
Ты — ОРКЕСТРАТОР Pipeline Mode ИРУ.

Предыдущий план оказался СЛИШКОМ СЖАТЫМ: вся задача collapsed в один шаг. Это плохо, потому что subagent-исполнитель
получает слишком широкую задачу и снова начинает сам планировать.

Нужно переразбить исходный запрос на 2-5 последовательных шагов.
Разрешено оставить 1 шаг ТОЛЬКО если запрос действительно атомарный уровня "создай одну папку" или "покажи версию Python".
Для текущего запроса нужно сделать именно МНОГОШАГОВЫЙ план.

Требования к новому плану:
1. Не сворачивай всю задачу в шаг вроде "Сделать всё целиком".
2. Отдельно выделяй подготовку/проверку среды, основную реализацию и проверку результата, если это уместно.
3. Если создаётся файл, приложение, виджет, проект или артефакт — отдельным шагом должна идти проверка результата и,
   если возможно, подготовка ссылки или пути к артефакту.
4. Каждый шаг должен быть достаточно узким, чтобы subagent мог выполнить его без нового перепланирования.

Верни ТОЛЬКО JSON без Markdown и без пояснений:
{{
  "goal": "краткая цель",
  "steps": [
    {{
      "title": "короткое название шага",
      "instruction": "подробное задание для subagent-исполнителя",
      "success_criteria": "как понять, что шаг завершён",
      "device_id": "ID устройства при необходимости"
    }}
  ]
}}

Текущая дата и время: {shared["current_datetime_msk"]}.

Подключённые устройства:
{shared["devices_block"]}

Текущее устройство:
ID: {shared["current_device_id"]}
Hostname: {shared["current_hostname"]}
ОС: {shared["current_os"]} ({shared["current_os_version"]})

Исходный запрос пользователя:
{user_message}

Слишком сжатый предыдущий план:
{json.dumps(plan, ensure_ascii=False, indent=2)}
"""


def expand_single_step_pipeline_fallback(plan: dict, user_message: str, default_device_id: str) -> dict:
    """Fallback split when the planner still collapses a non-trivial task to one step."""
    goal = str(plan.get("goal") or user_message or "Выполнить задачу").strip() or "Выполнить задачу"
    source_step = (plan.get("steps") or [{}])[0] or {}
    source_title = str(source_step.get("title") or goal).strip() or goal
    source_instruction = str(source_step.get("instruction") or user_message or source_title).strip() or source_title
    source_success = str(source_step.get("success_criteria") or "").strip()
    step_device_id = str(source_step.get("device_id") or default_device_id).strip() or default_device_id

    return {
        "goal": goal[:200],
        "steps": [
            {
                "title": "Уточнить среду и ограничения"[:160],
                "instruction": (
                    f"Перед основной реализацией коротко проверь рабочую среду, пути, зависимости, права и ограничения, "
                    f"которые критичны для задачи: {goal}. Не выполняй всю задачу целиком на этом шаге."
                )[:1400],
                "success_criteria": (
                    "Понятно, какой стек, пути, зависимости и системные ограничения нужны для следующего шага."
                )[:400],
                "device_id": step_device_id,
            },
            {
                "title": source_title[:160],
                "instruction": source_instruction[:1400],
                "success_criteria": (source_success or "Основной результат задачи создан или запущен.")[:400],
                "device_id": step_device_id,
            },
            {
                "title": "Проверить результат и подготовить итог"[:160],
                "instruction": (
                    "Проверь, что результат реально существует и работает как задумано. Если создан файл, проект, скрипт "
                    "или другой артефакт — по возможности получи ссылку через get_file_link или явно укажи путь. "
                    "Кратко зафиксируй, что получилось и какие ограничения остались."
                )[:1400],
                "success_criteria": (
                    "Есть подтверждение результата и, если применимо, ссылка или путь к созданному артефакту."
                )[:400],
                "device_id": step_device_id,
            },
        ],
    }


def pipeline_plan_prompt(shared: dict, user_message: str) -> str:
    """Промпт для оркестратора: разбить задачу на subagent-шаги."""
    return f"""\
Ты — ОРКЕСТРАТОР конвейерного режима ИРУ.

Твоя роль: НЕ выполнять команды самостоятельно, а разбить общий запрос на понятные subagent-шаги.
Каждый шаг потом пойдёт отдельному LLM-исполнителю. Поэтому шаги должны быть:
1. Непересекающимися.
2. Последовательными.
3. Достаточно конкретными, чтобы исполнитель мог сделать шаг без нового планирования.
4. В количестве от 2 до {PIPELINE_MAX_STEPS}, если только задача не совсем точечная.
5. НЕ сворачивай многосоставную задачу в один шаг вроде "Сделать всё целиком", "Реализовать запрос" или
   "Создать X и проверить X" — такие планы считаются плохими.

СНАЧАЛА уясни обстановку: что именно просит пользователь, на каких устройствах это лучше делать,
какие ограничения видны из профиля устройства и памяти, и какие промежуточные результаты вообще нужны.
Только после этого строй план шагов.

Верни ТОЛЬКО JSON без Markdown и без пояснений в таком формате:
{{
  "goal": "краткая цель",
  "steps": [
    {{
      "title": "короткое название шага",
      "instruction": "подробное задание для subagent-исполнителя",
      "success_criteria": "как понять, что шаг завершён",
      "device_id": "ID устройства, если шаг лучше делать не на текущем устройстве"
    }}
  ]
}}

Поле device_id можно опускать, если подходит текущее устройство.
Не создавай лишних микро-шагов. Не используй маркеры [[SUGGEST_PLAN]].

Текущая дата и время: {shared["current_datetime_msk"]}.

Подключённые устройства:
{shared["devices_block"]}

Текущее устройство:
ID: {shared["current_device_id"]}
Hostname: {shared["current_hostname"]}
ОС: {shared["current_os"]} ({shared["current_os_version"]})

Профиль устройства:
{shared["device_profile_block"] or "Нет расширенного профиля."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Запрос пользователя:
{user_message}
"""


def pipeline_worker_prompt(shared: dict, overall_goal: str, step: dict, completed_steps: list[dict]) -> str:
    """Промпт для subagent-исполнителя одного шага."""
    completed_block = "Нет завершённых шагов."
    if completed_steps:
        target_device_id = shared.get("target_device_id") or shared["current_device_id"]
        completed_lines = []
        for item in completed_steps[-6:]:
            item_device_id = item.get("device_id") or "unknown"
            hostname = item.get("hostname") or "unknown"
            if item_device_id == target_device_id:
                prefix = f"[target device_id={item_device_id} hostname={hostname}]"
            else:
                prefix = (
                    f"[OTHER DEVICE device_id={item_device_id} hostname={hostname}; "
                    "informational only, do not reuse paths as target-device paths]"
                )
            completed_lines.append(f"- {prefix} {item['title']}: {item['summary']}")
        completed_block = "\n".join(completed_lines)

    step_device_id = shared.get("target_device_id") or step.get("device_id") or shared["current_device_id"]
    return f"""\
Ты — SUBAGENT-ИСПОЛНИТЕЛЬ внутри Pipeline Mode ИРУ.

Ты выполняешь ТОЛЬКО ОДИН назначенный шаг. Ты не главный ассистент и не оркестратор.
Твоя задача: довести текущий шаг до результата с помощью инструментов и затем коротко отчитаться.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. Не создавай новый план.
2. Не используй create_plan и mark_step — их нет в твоих инструментах.
3. Действуй только в рамках текущего шага.
4. Если шаг завершён — верни короткий итог простым текстом без Markdown.
5. Если шаг не удаётся — верни краткое описание проблемы и на чём остановился.
6. Для длинных текстов и файлов используй write_content.
7. Для актуальной информации используй только web_search.

Общая цель:
{overall_goal}

Текущий шаг:
Название: {step.get("title", "")}
Задание: {step.get("instruction", "")}
Критерий успеха: {step.get("success_criteria", "Не задан явно")}
Предпочтительное устройство: {step_device_id}

Что уже сделано:
{completed_block}

Подключённые устройства:
{shared["devices_block"]}

Текущее устройство:
ID: {shared["current_device_id"]}
Hostname: {shared["current_hostname"]}
ОС: {shared["current_os"]} ({shared["current_os_version"]})

Профиль устройства:
{shared["device_profile_block"] or "Нет расширенного профиля."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Текущая дата и время: {shared["current_datetime_msk"]}.
"""


def pipeline_summary_prompt() -> str:
    """Финальный промпт оркестратора для сборки общего ответа."""
    return """\
Ты — ОРКЕСТРАТОР Pipeline Mode ИРУ.

Тебе дали результат работы subagent-исполнителей по шагам. Сформируй финальный ответ пользователю:
1. Кратко скажи, что сделано.
2. Если выполнение остановилось — честно укажи на каком шаге и почему.
3. Если есть полезный итоговый артефакт или ссылка на скачивание — упомяни это явно.
4. Пиши только чистым текстом без Markdown.
5. Если стоит запомнить важный факт о конфигурации или предпочтении пользователя — можешь в САМОМ КОНЦЕ добавить маркер:
[[SUGGEST_REMEMBER: текст факта | категория]]
Категории: preference, config, warning, layout, software.
"""


def build_pipeline_shared_context(
    device_id: str,
    device_info: dict,
    all_devices: dict,
    device_profile: dict | None,
    machine_guid: str | None,
    mem_user_id: str | None,
    *,
    windows_rules: str,
    linux_rules: str,
) -> dict:
    """Контекст окружения для оркестратора и subagent-исполнителей."""
    os_info = device_info.get("os", "Windows")
    os_lower = (os_info or "").lower()
    return {
        "devices_block": build_devices_block(all_devices),
        "current_device_id": device_id,
        "current_hostname": device_info.get("hostname", "unknown"),
        "current_os": os_info,
        "current_os_version": device_info.get("os_version", ""),
        "device_profile_block": build_device_profile_block(device_profile),
        "device_memory_block": build_memory_block(machine_guid, mem_user_id),
        "os_rules": linux_rules if "linux" in os_lower else windows_rules,
        "current_datetime_msk": current_datetime_msk(),
    }


def _pipeline_device_info(all_devices: dict, device_id: str, fallback_info: dict | None = None) -> dict:
    dev = (all_devices or {}).get(device_id)
    if isinstance(dev, dict) and isinstance(dev.get("info"), dict):
        return dev["info"]
    return fallback_info or {}


def build_pipeline_other_devices_summary(all_devices: dict, target_device_id: str) -> str:
    """Return a path-free summary of non-target devices for worker prompts."""
    lines = []
    for did, dev in (all_devices or {}).items():
        if did == target_device_id:
            continue
        info = dev.get("info", {}) if isinstance(dev, dict) else {}
        hostname = info.get("hostname", "?")
        os_name = info.get("os", "?")
        os_ver = info.get("os_version", "")
        status = (info.get("status") or dev.get("status")) if isinstance(dev, dict) else None
        status_part = f", status={status}" if status else ""
        lines.append(f"- {did}: hostname={hostname}, OS={os_name} ({os_ver}){status_part}")
    return "\n".join(lines)


def validate_pipeline_step_device(step: dict, current_device_id: str, all_devices: dict) -> tuple[dict, str]:
    """Validate planner-selected device id; fall back loudly when it is unknown."""
    requested = str(step.get("device_id") or "").strip()
    known_device_ids = set((all_devices or {}).keys())
    if current_device_id:
        known_device_ids.add(current_device_id)
    target_device_id = requested or current_device_id
    if target_device_id not in known_device_ids:
        logger.warning(
            "Invalid pipeline step.device_id=%s; falling back to current_device_id=%s",
            target_device_id,
            current_device_id,
        )
        target_device_id = current_device_id
    scoped_step = dict(step)
    scoped_step["device_id"] = target_device_id
    return scoped_step, target_device_id


def build_pipeline_worker_context(
    *,
    target_device_id: str,
    current_device_id: str,
    current_device_info: dict,
    all_devices: dict,
    current_device_profile: dict | None,
    mem_user_id: str | None,
    windows_rules: str,
    linux_rules: str,
) -> tuple[dict, str | None]:
    """Build target-device-only context for a single pipeline worker."""
    target_info = _pipeline_device_info(
        all_devices,
        target_device_id,
        current_device_info if target_device_id == current_device_id else None,
    )
    target_profile = current_device_profile if target_device_id == current_device_id else db.get_device_profile(target_device_id)
    target_machine_guid = (target_profile or {}).get("machine_guid") or target_info.get("machine_guid") or None
    os_info = target_info.get("os", "Windows")
    os_lower = (os_info or "").lower()
    other_devices_summary = build_pipeline_other_devices_summary(all_devices, target_device_id)
    return {
        "devices_block": other_devices_summary,
        "other_devices_summary": other_devices_summary,
        "target_device_id": target_device_id,
        "current_device_id": target_device_id,
        "current_hostname": target_info.get("hostname", "unknown"),
        "current_os": os_info,
        "current_os_version": target_info.get("os_version", ""),
        "device_profile_block": build_device_profile_block(target_profile),
        "device_memory_block": build_memory_block(target_machine_guid, mem_user_id),
        "os_rules": linux_rules if "linux" in os_lower else windows_rules,
        "current_datetime_msk": current_datetime_msk(),
    }, target_machine_guid


async def run_pipeline_worker(
    client: httpx.AsyncClient,
    cfg: dict,
    model: str,
    shared: dict,
    overall_goal: str,
    step: dict,
    completed_steps: list[dict],
    chat_history: list[dict] | None,
    send_command_fn,
    get_file_link_fn,
    machine_guid: str | None,
    mem_user_id: str | None,
    poll_task_id: str | None,
    *,
    chat_completion_request_fn,
    worker_tools: list[dict],
) -> dict:
    """Subagent-исполнитель одного шага pipeline."""
    worker_prompt = pipeline_worker_prompt(shared, overall_goal, step, completed_steps)
    messages = [{"role": "system", "content": worker_prompt}]
    messages.append({
        "role": "system",
        "content": (
            f"Device scope hard rule: target_device={shared.get('target_device_id') or shared['current_device_id']}. "
            "Execute this step only on target_device. Do not use paths from another device. "
            "Absolute paths from user memory are hints only and must be verified on target_device before use. "
            "If a path is not found on target_device, report it instead of substituting a path from another device. "
            "Completed steps marked OTHER DEVICE are informational only; do not reuse their paths as target-device paths."
        ),
    })
    messages.append({
        "role": "user",
        "content": (
            f"Выполни шаг: {step.get('title', '')}\n"
            f"Инструкция: {step.get('instruction', '')}\n"
            f"Критерий успеха: {step.get('success_criteria', 'не задан')}"
        ),
    })

    commands_log = []
    command_budget = CommandBudget()
    step_device_id = step.get("device_id") or shared["current_device_id"]

    for iteration in range(PIPELINE_WORKER_MAX_ITERATIONS):
        print(
            f"[pipeline/worker] iteration {iteration + 1}/{PIPELINE_WORKER_MAX_ITERATIONS}, "
            f"step={step.get('title', '')[:60]!r}"
        )
        data = await chat_completion_request_fn(
            client=client,
            cfg=cfg,
            model=model,
            messages=messages,
            tools=worker_tools,
        )
        choice = data["choices"][0]
        assistant_msg = choice["message"]
        finish_reason = choice.get("finish_reason", "?")
        content_preview = (assistant_msg.get("content") or "")[:120]
        tool_calls = assistant_msg.get("tool_calls")
        print(
            f"[pipeline/worker] response: finish_reason={finish_reason}, "
            f"tool_calls={len(tool_calls) if tool_calls else 0}, "
            f"content_preview={content_preview!r}"
        )

        if finish_reason == "length":
            if tool_calls and iteration < PIPELINE_WORKER_MAX_ITERATIONS - 1:
                messages.append({
                    "role": "user",
                    "content": "Предыдущий ответ был обрезан. Продолжи короче и точнее.",
                })
                continue

        messages.append(assistant_msg)

        if not tool_calls:
            final_text = strip_markdown(assistant_msg.get("content") or "").strip()
            if final_text:
                final_text = enforce_trusted_answer(final_text, commands_log)
                return {
                    "status": "ok",
                    "answer": final_text,
                    "commands": commands_log,
                }
            return {
                "status": "error",
                "answer": "Subagent завершил шаг без содержательного ответа.",
                "commands": commands_log,
            }

        for tool_call in tool_calls:
            fn_name = tool_call["function"]["name"]
            fn_args = json.loads(tool_call["function"]["arguments"] or "{}")
            fn_args.pop("device_id", None)
            target_device = step_device_id
            print(
                f"[pipeline/worker] tool_call: {fn_name}"
                f"({json.dumps(fn_args, ensure_ascii=False)[:250]}) -> device={target_device}"
            )

            budget_error = command_budget.register(fn_name, fn_args.get("command", ""))
            if budget_error:
                commands_log.append(budget_guard_entry(budget_error))
                return {
                    "status": "error",
                    "answer": budget_error,
                    "commands": commands_log,
                }

            if fn_name == "execute_cmd":
                set_current_step(poll_task_id, f"Исполняю шаг: {step.get('title', '')[:60]}")
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
                except Exception as exc:
                    err_str = str(exc)
                    if "CONFIRM_REQUIRED" in err_str:
                        raise ConfirmationRequired(
                            command=fn_args.get("command", ""),
                            device_id=target_device,
                            params=fn_args,
                            answer=f"Для шага «{step.get('title', '')}» требуется подтверждение команды",
                            commands_log=commands_log,
                        )
                    tool_result = {"error": err_str}

                commands_log.append({
                    "action": fn_name,
                    "command": fn_args.get("command", ""),
                    "device_id": target_device,
                    "result": tool_result,
                    "iteration": iteration + 1,
                })
                if machine_guid and "error" not in tool_result:
                    try:
                        db.add_command_memory(
                            machine_guid=machine_guid,
                            device_id=target_device,
                            command=fn_args.get("command", ""),
                            intent=step.get("title"),
                            exit_code=int(tool_result.get("returncode", -1)),
                            stdout=tool_result.get("stdout"),
                            stderr=tool_result.get("stderr"),
                            user_id=mem_user_id,
                        )
                    except Exception:
                        print("[pipeline/worker] Failed to write command memory")

            elif fn_name == "write_content":
                set_current_step(poll_task_id, f"Создаю файл для шага: {step.get('title', '')[:50]}")
                try:
                    tool_result = await send_command_fn(target_device, "write_content", fn_args)
                except Exception as exc:
                    err_str = str(exc)
                    if "CONFIRM_REQUIRED" in err_str:
                        raise ConfirmationRequired(
                            command=f"write_content: {fn_args.get('path', '')}",
                            device_id=target_device,
                            params=fn_args,
                            answer=f"Для шага «{step.get('title', '')}» требуется подтверждение записи в файл",
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
                set_current_step(poll_task_id, f"Формирую ссылку: {step.get('title', '')[:50]}")
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
                set_current_step(poll_task_id, f"Ищу данные для шага: {step.get('title', '')[:50]}")
                tavily_key = cfg.get("tavily_api_key")
                if not tavily_key:
                    tool_result = {"error": "tavily_api_key не настроен в llm_config.json на сервере"}
                else:
                    query = fn_args.get("query", "").strip()
                    max_results = min(int(fn_args.get("max_results", 5) or 5), 10)
                    if not query:
                        tool_result = {"error": "Пустой запрос"}
                    else:
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
                                            print(f"[pipeline/worker] tavily retry: {type(tavily_exc).__name__}")
                                            await asyncio.sleep(2)
                                            continue
                                        raise
                            if tavily_data is None:
                                tool_result = {"error": "Поиск временно недоступен. Попробуйте позже."}
                            else:
                                tool_result = {
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
                            tool_result = {"error": f"Поиск временно недоступен: {exc}"}

                commands_log.append({
                    "action": fn_name,
                    "command": f"[web_search] {fn_args.get('query', '')[:80]}",
                    "device_id": target_device,
                    "result": tool_result if not isinstance(tool_result, dict) or "error" in tool_result else {"ok": True},
                    "iteration": iteration + 1,
                })

            elif fn_name == "remember_fact":
                if not mem_user_id:
                    tool_result = {"error": "Не удалось сохранить факт: пользователь не идентифицирован"}
                else:
                    try:
                        fact_id = db.add_user_fact(
                            user_id=mem_user_id,
                            text=fn_args.get("text", ""),
                            category=fn_args.get("category"),
                        )
                        tool_result = {"status": "ok", "fact_id": fact_id, "result": f"Запомнил факт о тебе (id={fact_id})"}
                    except Exception as exc:
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
        "status": "error",
        "answer": "Subagent достиг лимита итераций и остановил шаг.",
        "commands": commands_log,
    }


async def process_pipeline_subagents(
    user_message: str,
    device_id: str,
    device_info: dict,
    all_devices: dict,
    send_command_fn,
    get_file_link_fn,
    chat_history: list[dict] | None = None,
    user_id: int = None,
    chat_id: int = None,
    device_profile: dict | None = None,
    modes: dict | None = None,
    poll_task_id: str | None = None,
    *,
    load_llm_config_fn,
    pick_model_fn,
    chat_completion_request_fn,
    worker_tools: list[dict],
    windows_rules: str,
    linux_rules: str,
) -> dict:
    """Pipeline Mode с субагентностью: orchestrator -> step workers -> final synthesis."""
    cfg = load_llm_config_fn()
    modes = modes or {}
    model = pick_model_fn(cfg, {"pipeline": True, "autonomous": bool(modes.get("autonomous"))})
    machine_guid = (device_profile or {}).get("machine_guid") or None
    mem_user_id = str(user_id) if user_id else (f"anon_{machine_guid}" if machine_guid else None)
    shared = build_pipeline_shared_context(
        device_id=device_id,
        device_info=device_info,
        all_devices=all_devices,
        device_profile=device_profile,
        machine_guid=machine_guid,
        mem_user_id=mem_user_id,
        windows_rules=windows_rules,
        linux_rules=linux_rules,
    )

    set_current_step(poll_task_id, "Оркестратор строит план...")
    history_msgs = build_chat_messages(chat_history[:-1], filter_onboarding=True)[-8:] if chat_history else []
    plan_messages = [{"role": "system", "content": pipeline_plan_prompt(shared, user_message)}]
    plan_messages.extend(history_msgs)
    plan_messages.append({"role": "user", "content": user_message})

    step_results = []
    all_commands = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        planner_data = await chat_completion_request_fn(
            client=client,
            cfg=cfg,
            model=model,
            messages=plan_messages,
            tools=None,
            max_tokens=min(cfg.get("max_tokens", 4096), 2500),
        )
        planner_text = (planner_data["choices"][0]["message"].get("content") or "").strip()
        normalized_plan = normalize_pipeline_plan(
            extract_json_payload(planner_text),
            fallback_goal=user_message,
            default_device_id=device_id,
        )
        if should_force_multi_step_pipeline(normalized_plan, user_message):
            print("[pipeline] planner collapsed non-trivial task to one step; requesting refined plan")
            refine_messages = [{
                "role": "system",
                "content": pipeline_single_step_refine_prompt(shared, user_message, normalized_plan),
            }]
            try:
                refine_data = await chat_completion_request_fn(
                    client=client,
                    cfg=cfg,
                    model=model,
                    messages=refine_messages,
                    tools=None,
                    max_tokens=min(cfg.get("max_tokens", 4096), 2500),
                )
                refine_text = (refine_data["choices"][0]["message"].get("content") or "").strip()
                refined_plan = normalize_pipeline_plan(
                    extract_json_payload(refine_text),
                    fallback_goal=user_message,
                    default_device_id=device_id,
                )
                if len(refined_plan.get("steps") or []) > 1:
                    normalized_plan = refined_plan
                else:
                    print("[pipeline] refined planner still returned one step; using fallback expansion")
                    normalized_plan = expand_single_step_pipeline_fallback(
                        normalized_plan,
                        user_message=user_message,
                        default_device_id=device_id,
                    )
            except Exception as exc:
                print(f"[pipeline] refine single-step plan failed: {exc}; using fallback expansion")
                normalized_plan = expand_single_step_pipeline_fallback(
                    normalized_plan,
                    user_message=user_message,
                    default_device_id=device_id,
                )

        db_task_id = db.create_task(
            user_id=user_id,
            chat_id=chat_id,
            device_id=device_id,
            goal=normalized_plan["goal"],
            steps=[step["title"] for step in normalized_plan["steps"]],
        )
        created_task_ids = [db_task_id]
        push_tasks_view(poll_task_id, created_task_ids)

        pipeline_failed = False
        failure_reason = ""
        for idx, step in enumerate(normalized_plan["steps"]):
            step, step_device_id = validate_pipeline_step_device(step, device_id, all_devices)
            worker_shared, worker_machine_guid = build_pipeline_worker_context(
                target_device_id=step_device_id,
                current_device_id=device_id,
                current_device_info=device_info,
                all_devices=all_devices,
                current_device_profile=device_profile,
                mem_user_id=mem_user_id,
                windows_rules=windows_rules,
                linux_rules=linux_rules,
            )
            db.update_step(db_task_id, idx, "running", summary="Подзадача передана subagent-исполнителю")
            push_tasks_view(poll_task_id, created_task_ids)
            set_current_step(
                poll_task_id,
                f"Шаг {idx + 1}/{len(normalized_plan['steps'])}: {step['title'][:80]}",
            )
            try:
                worker_result = await run_pipeline_worker(
                    client=client,
                    cfg=cfg,
                    model=model,
                    shared=worker_shared,
                    overall_goal=normalized_plan["goal"],
                    step=step,
                    completed_steps=step_results,
                    chat_history=chat_history,
                    send_command_fn=send_command_fn,
                    get_file_link_fn=get_file_link_fn,
                    machine_guid=worker_machine_guid,
                    mem_user_id=mem_user_id,
                    poll_task_id=poll_task_id,
                    chat_completion_request_fn=chat_completion_request_fn,
                    worker_tools=worker_tools,
                )
            except ConfirmationRequired:
                raise
            except Exception as exc:
                worker_result = {
                    "status": "error",
                    "answer": f"Ошибка subagent-исполнителя: {exc}",
                    "commands": [],
                }

            worker_result["answer"] = enforce_trusted_answer(
                worker_result.get("answer", ""),
                worker_result.get("commands", []),
            )
            all_commands.extend(worker_result.get("commands", []))
            step_summary = strip_markdown(worker_result.get("answer", "")).strip() or "Шаг завершён."
            urls = [
                cmd.get("result", {}).get("url")
                for cmd in worker_result.get("commands", [])
                if isinstance(cmd.get("result"), dict) and cmd["result"].get("url")
            ]
            if urls:
                step_summary = f"{step_summary} Ссылки: {'; '.join(urls)}"

            step_status = "done" if worker_result.get("status") == "ok" else "failed"
            db.update_step(db_task_id, idx, step_status, summary=step_summary[:500])
            push_tasks_view(poll_task_id, created_task_ids)

            step_results.append({
                "idx": idx,
                "title": step["title"],
                "instruction": step["instruction"],
                "device_id": step_device_id,
                "hostname": worker_shared.get("current_hostname", "unknown"),
                "status": step_status,
                "summary": step_summary,
            })

            if step_status != "done":
                pipeline_failed = True
                failure_reason = step_summary
                break

        db.finish_task(db_task_id, "failed" if pipeline_failed else "completed")
        push_tasks_view(poll_task_id, created_task_ids)
        set_current_step(poll_task_id, "Оркестратор подводит итоги...")

        summary_payload = {
            "goal": normalized_plan["goal"],
            "pipeline_status": "failed" if pipeline_failed else "completed",
            "failure_reason": failure_reason,
            "steps": step_results,
        }
        summary_messages = [
            {"role": "system", "content": pipeline_summary_prompt()},
            {"role": "user", "content": json.dumps(summary_payload, ensure_ascii=False, indent=2)},
        ]
        try:
            summary_data = await chat_completion_request_fn(
                client=client,
                cfg=cfg,
                model=cfg.get("model", "deepseek-chat"),
                messages=summary_messages,
                tools=None,
                max_tokens=min(cfg.get("max_tokens", 4096), 1200),
            )
            final_answer = (summary_data["choices"][0]["message"].get("content") or "").strip()
        except Exception as exc:
            print(f"[pipeline] summary error: {exc}")
            final_answer = "План выполнен не полностью." if pipeline_failed else "План выполнен."
            if step_results:
                final_answer += " " + " ".join(
                    f"{step_result['title']}: {step_result['summary']}"
                    for step_result in step_results[-3:]
                )

    final_answer = strip_markdown(final_answer)
    final_answer = enforce_trusted_answer(final_answer, all_commands)
    return {
        "answer": final_answer,
        "commands": all_commands,
        "tasks": collect_tasks(created_task_ids),
        "training_context": {
            "os": device_info.get("os", ""),
            "hostname": device_info.get("hostname", ""),
            "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
        },
    }
