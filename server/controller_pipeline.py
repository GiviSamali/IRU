import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import httpx

try:
    from . import database as db  # type: ignore
    from .controller_budget import CommandBudget, budget_guard_entry  # type: ignore
    from .controller_trust import enforce_trusted_answer  # type: ignore
    from .device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from .python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from .python_toolchain import (  # type: ignore
        build_python_toolchain_block,
        get_cached_python_toolchain,
        python_toolchain_from_runtime_summary,
        resolve_python_toolchain,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )
    from .tool_registry import tool_log_fields  # type: ignore
except ImportError:
    import database as db  # type: ignore
    from controller_budget import CommandBudget, budget_guard_entry  # type: ignore
    from controller_trust import enforce_trusted_answer  # type: ignore
    from device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from python_env import classify_command_error, is_recoverable_command_error  # type: ignore
    from python_toolchain import (  # type: ignore
        build_python_toolchain_block,
        get_cached_python_toolchain,
        python_toolchain_from_runtime_summary,
        resolve_python_toolchain,
        rewrite_python_command,
        validate_toolchain_fact_against_receipt,
    )
    from tool_registry import tool_log_fields  # type: ignore

try:
    from .controller_shared import (
        ConfirmationRequired,
        build_chat_messages,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        build_target_device_block,
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
        build_target_device_block,
        collect_tasks,
        current_datetime_msk,
        push_tasks_view,
        set_current_step,
        strip_markdown,
    )


PIPELINE_WORKER_MAX_ITERATIONS = 10
PIPELINE_MAX_STEPS = 10
logger = logging.getLogger(__name__)

STEP_STATES = {"pending", "running", "done", "failed", "recovered", "skipped", "blocked"}
TASK_STATES = {"running", "completed", "completed_with_recovery", "failed", "cancelled", "blocked"}

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

Target device context:
{shared.get("device_context_block") or ""}
{shared.get("target_device_block") or "Нет расширенного target context."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Запрос пользователя:
{user_message}

{format_conversation_context_block(shared.get("conversation_context") or build_conversation_context(None, user_message))}
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

КОНТРАКТ ВЫПОЛНЕНИЯ КОМАНД ДЛЯ SUBAGENT:
1. Выполняй минимальный набор команд, достаточный для текущего шага.
2. Каждая команда должна иметь наблюдаемый результат: действие + короткий вывод с маркером OK, ERROR, EXISTS, CREATED, PY_COMPILE_OK или APP_STARTED.
3. Не доказывай результат бесконечными проверками. Если понятная проверка уже успешна, остановись и отчитайся.
4. Если py_compile успешен и нужные файлы созданы, этого достаточно для базовой проверки созданного GUI-проекта.
5. Не используй screenshot, SendKeys, PrintScreen или GetForegroundWindow без явного запроса пользователя.
6. Если GUI надо запустить, используй execute_cmd с long_running=true.

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

Target device context:
{shared.get("device_context_block") or ""}
{shared.get("target_device_block") or "Нет расширенного target context."}

Память:
{shared["device_memory_block"] or "Нет дополнительной памяти."}

Правила ОС:
{shared["os_rules"]}

Текущая дата и время: {shared["current_datetime_msk"]}.

{format_conversation_context_block(shared.get("conversation_context") or build_conversation_context(None, ""), redact_paths=True)}
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
    python_receipt = (
        python_toolchain_from_runtime_summary((device_profile or {}).get("python_runtime_summary"), device_id=device_id)
        or get_cached_python_toolchain({"device_id": device_id, "machine_guid": machine_guid})
    )
    manifest = build_minimal_llm_context(device_id, all_devices, device_profile)
    return {
        "devices_block": build_devices_block(all_devices),
        "current_device_id": device_id,
        "current_hostname": device_info.get("hostname", "unknown"),
        "current_os": os_info,
        "current_os_version": device_info.get("os_version", ""),
        "device_profile_block": build_device_profile_block(device_profile),
        "target_device_block": build_target_device_block(device_id, device_info, device_profile)
        + "\n"
        + build_python_toolchain_block(python_receipt),
        "device_context_block": format_minimal_llm_context_block(manifest),
        "python_toolchain_receipt": python_receipt.to_dict() if python_receipt else None,
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
    other_devices = {
        did: dev
        for did, dev in (all_devices or {}).items()
        if did != target_device_id
    }
    return build_devices_block(other_devices) if other_devices else "No other connected devices."


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
    python_receipt = (
        python_toolchain_from_runtime_summary((target_profile or {}).get("python_runtime_summary"), device_id=target_device_id)
        or get_cached_python_toolchain({"device_id": target_device_id, "machine_guid": target_machine_guid})
    )
    manifest = build_minimal_llm_context(target_device_id, all_devices, target_profile)
    return {
        "devices_block": other_devices_summary,
        "other_devices_summary": other_devices_summary,
        "target_device_id": target_device_id,
        "current_device_id": target_device_id,
        "current_hostname": target_info.get("hostname", "unknown"),
        "current_os": os_info,
        "current_os_version": target_info.get("os_version", ""),
        "device_profile_block": build_device_profile_block(target_profile),
        "target_device_block": build_target_device_block(target_device_id, target_info, target_profile)
        + "\n"
        + build_python_toolchain_block(python_receipt),
        "device_context_block": format_minimal_llm_context_block(manifest),
        "python_toolchain_receipt": python_receipt.to_dict() if python_receipt else None,
        "device_memory_block": build_memory_block(target_machine_guid, mem_user_id),
        "os_rules": linux_rules if "linux" in os_lower else windows_rules,
        "current_datetime_msk": current_datetime_msk(),
    }, target_machine_guid


def _pipeline_command_status(action: str, result: dict | None) -> str:
    if action == "budget_guard":
        return "blocked"
    if not isinstance(result, dict):
        return "success"
    if result.get("error"):
        return "error"
    returncode = result.get("returncode")
    if returncode not in (None, 0):
        return "error"
    return "success"


def build_conversation_context(chat_history: list[dict] | None, current_user_message: str) -> dict:
    """Return a compact, explicit history block for pipeline prompts."""
    if chat_history is None:
        return {
            "current_user_message": current_user_message,
            "previous_user_message": None,
            "previous_assistant_message": None,
            "recent_turns_count": 0,
            "history_available": False,
        }

    messages = list(chat_history or [])
    user_messages = [
        (msg.get("content") or "").strip()
        for msg in messages
        if msg.get("role") == "user" and (msg.get("content") or "").strip()
    ]
    assistant_messages = [
        (msg.get("content") or "").strip()
        for msg in messages
        if msg.get("role") == "assistant" and (msg.get("content") or "").strip()
    ]
    previous_user_message = None
    if len(user_messages) >= 2:
        previous_user_message = user_messages[-2]
    elif user_messages and user_messages[-1] != current_user_message:
        previous_user_message = user_messages[-1]

    return {
        "current_user_message": current_user_message,
        "previous_user_message": previous_user_message,
        "previous_assistant_message": assistant_messages[-1] if assistant_messages else None,
        "recent_turns_count": len(user_messages),
        "history_available": True,
    }


_WINDOWS_ABS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]+")


def _redact_history_text(value: str | None, limit: int = 700) -> str | None:
    if not value:
        return None
    redacted = _WINDOWS_ABS_PATH_RE.sub("[redacted_path]", value)
    return redacted[:limit]


def format_conversation_context_block(context: dict, *, redact_paths: bool = False) -> str:
    def _value(key: str) -> str:
        value = context.get(key)
        if redact_paths and isinstance(value, str):
            value = _redact_history_text(value)
        return str(value) if value else "null"

    return "\n".join([
        "Conversation context:",
        f"history_available: {bool(context.get('history_available'))}",
        f"recent_turns_count: {int(context.get('recent_turns_count') or 0)}",
        f"current_user_message: {_value('current_user_message')}",
        f"previous_user_message: {_value('previous_user_message')}",
        f"previous_assistant_message: {_value('previous_assistant_message')}",
        (
            "If history_available is false, say that history is unavailable in this execution mode. "
            "Do not claim this is the first request unless the server context proves it."
        ),
    ])


_FALSE_FIRST_REQUEST_PATTERNS = (
    "первый запрос",
    "первое сообщение",
    "предыдущих сообщений нет",
    "нет предыдущих сообщений",
    "first request",
    "first message",
    "no previous messages",
)


def enforce_conversation_context_answer(answer: str, context: dict) -> str:
    """Prevent false "first request" claims when server-side history disagrees."""
    text = answer or ""
    lower = text.lower()
    if not any(pattern in lower for pattern in _FALSE_FIRST_REQUEST_PATTERNS):
        return text
    if not context.get("history_available"):
        return "В этом режиме выполнения история недоступна."
    if int(context.get("recent_turns_count") or 0) <= 1:
        return text

    previous_user = context.get("previous_user_message")
    previous_assistant = context.get("previous_assistant_message")
    parts = []
    if previous_user:
        parts.append(f"Предыдущий вопрос пользователя: {previous_user}")
    if previous_assistant:
        parts.append(f"Мой предыдущий ответ: {previous_assistant}")
    return "\n".join(parts) if parts else "История доступна, но предыдущий вопрос не удалось извлечь из контекста."


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _collect_result_paths(result: dict, keys: tuple[str, ...]) -> list[str]:
    paths = []
    if not isinstance(result, dict):
        return paths
    for key in keys:
        for item in _as_list(result.get(key)):
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
    return paths


def _unique(items: list) -> list:
    seen = set()
    result = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _command_failed(command: dict) -> bool:
    return command.get("status") in {"error", "blocked"} or _pipeline_command_status(
        command.get("action") or "",
        command.get("result") if isinstance(command.get("result"), dict) else None,
    ) in {"error", "blocked"}


def _command_recoverable(command: dict) -> bool:
    result = command.get("result") or {}
    if not isinstance(result, dict):
        return False
    command_error = result.get("command_error") or {}
    if isinstance(command_error, dict) and command_error.get("recoverable"):
        return True
    return result.get("error_type") in {
        "dependency_missing",
        "command_missing",
        "path_missing",
        "shell_syntax_error",
        "python_runtime_error",
        "timeout",
    }


def _verification_command_succeeded(command: dict) -> bool:
    if _command_failed(command):
        return False
    result = command.get("result") or {}
    if not isinstance(result, dict):
        return False
    if _collect_result_paths(result, ("files_verified", "verified_files", "file_path")):
        return True
    if result.get("exists") is True or result.get("verified") is True:
        return True
    output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".upper()
    return any(marker in output for marker in ("IRU_VERIFIED", "IRU_CHECK_OK", "IRU_ARTIFACT_EXISTS"))


def _step_has_failed_command(commands: list[dict], step_index: int) -> bool:
    return any(command.get("step_index") == step_index and _command_failed(command) for command in commands)


def _step_has_recoverable_failure(commands: list[dict], step_index: int) -> bool:
    return any(command.get("step_index") == step_index and _command_failed(command) and _command_recoverable(command) for command in commands)


def _step_success_after_failure(commands: list[dict], step_index: int) -> bool:
    saw_failure = False
    for command in commands:
        if command.get("step_index") != step_index:
            continue
        if _command_failed(command):
            saw_failure = True
        elif saw_failure and _verification_command_succeeded(command):
            return True
    return False


_QUOTED_PYTHON_RE = re.compile(r'"([^"]*python(?:\.exe)?)"', re.IGNORECASE)
_WIN_PYTHON_PATH_RE = re.compile(r"\b([A-Za-z]:\\[^\n\r\"';&|]*?python(?:\.exe)?)\b", re.IGNORECASE)


def _extract_python_interpreters(commands: list[dict], receipt_dicts: list[dict | None]) -> list[dict]:
    interpreters = []
    for receipt in receipt_dicts:
        if not isinstance(receipt, dict):
            continue
        path = receipt.get("interpreter_path")
        if path:
            interpreters.append({
                "path": path,
                "version": receipt.get("version"),
                "source": "receipt",
            })

    for command in commands:
        command_text = command.get("command") or ""
        result = command.get("result") or {}
        version = result.get("python_version") if isinstance(result, dict) else None
        for match in _QUOTED_PYTHON_RE.findall(command_text):
            interpreters.append({"path": match, "version": version, "source": "command"})
        for match in _WIN_PYTHON_PATH_RE.findall(command_text):
            interpreters.append({"path": match, "version": version, "source": "command"})
        if re.search(r"(^|[;&|{]\s*)(python|py)\b", command_text, re.IGNORECASE):
            interpreters.append({"path": "python", "version": version, "source": "bare_command"})

    deduped = []
    seen = set()
    for item in interpreters:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_pipeline_task_receipt(
    *,
    task_status: str,
    commands: list[dict],
    step_results: list[dict],
    recovery_warnings: list[str],
    receipt_dicts: list[dict | None],
) -> dict:
    artifacts_created = []
    files_verified = []
    commands_failed = []
    for command in commands:
        result = command.get("result") or {}
        if isinstance(result, dict):
            artifacts_created.extend(_collect_result_paths(result, (
                "artifacts_created",
                "created_artifacts",
                "created_files",
                "files_created",
                "path",
            )))
            if command.get("action") == "write_content":
                artifacts_created.extend(_collect_result_paths(result, ("file_path",)))
            files_verified.extend(_collect_result_paths(result, (
                "files_verified",
                "verified_files",
                "file_path",
            )))
        if _command_failed(command):
            commands_failed.append({
                "step_index": command.get("step_index"),
                "step_title": command.get("step_title"),
                "action": command.get("action"),
                "command": command.get("command"),
                "status": command.get("status"),
                "error_type": result.get("error_type") if isinstance(result, dict) else None,
                "error": result.get("error") if isinstance(result, dict) else None,
                "returncode": result.get("returncode") if isinstance(result, dict) else None,
            })

    recoveries_applied = [
        {
            "step_index": step.get("idx"),
            "step_title": step.get("title"),
            "reason": step.get("recovery_reason") or "failed command recovered by later verification",
        }
        for step in step_results
        if step.get("status") == "recovered"
    ]
    python_interpreters = _extract_python_interpreters(commands, receipt_dicts)
    warnings = list(recovery_warnings)
    if len({item["path"].lower() for item in python_interpreters}) > 1:
        warnings.append("multiple_python_interpreters_used")
    final_verification_status = "verified" if files_verified or any(
        step.get("status") in {"done", "recovered"} for step in step_results[-1:]
    ) else "unverified"
    if any(step.get("status") in {"failed", "blocked"} for step in step_results):
        final_verification_status = "failed"

    return {
        "task_status": task_status,
        "artifacts_created": _unique(artifacts_created),
        "files_verified": _unique(files_verified),
        "commands_failed": _unique(commands_failed),
        "recoveries_applied": _unique(recoveries_applied),
        "final_verification_status": final_verification_status,
        "python_interpreters": python_interpreters,
        "warnings": _unique(warnings),
    }


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
    step_index: int = 0,
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
            "Device state grounding hard rule: every device state fact must include device_id/source. "
            "Do not copy CPU/RAM/disk/process/load from one device to another. "
            "If no fresh live snapshot exists for a device, say fresh state unavailable. "
            "Cached profile data must be labeled cached and must not be described as current. "
            "Absolute paths from user memory are hints only and must be verified on target_device before use. "
            "Never create missing C:\\Users\\<name> profile folders unless the user explicitly asked and confirmed. "
            "If a path is not found on target_device, report it instead of substituting a path from another device. "
            "Completed steps marked OTHER DEVICE are informational only; do not reuse their paths as target-device paths. "
            "Python environment contract: if Python is found and an import check returns ModuleNotFoundError / No module named, "
            "treat it as a missing dependency, not missing Python. Do not search for another interpreter after Python was found "
            "unless the user explicitly asked for a different interpreter. Stop and offer to install the dependency through confirmation. "
            "Command errors are observations; analyze stderr/stdout and continue if recoverable. "
            "Do not stop after ModuleNotFoundError; treat it as missing dependency. "
            "For package checks prefer one non-throwing JSON check using importlib.util.find_spec, for example: "
            "& \"<resolved_python_path>\" -c \"import importlib.util,json; names=['PyQt5','numpy','matplotlib']; "
            "print(json.dumps({n: bool(importlib.util.find_spec(n)) for n in names}))\". "
            "For PyQt5 version, first verify PyQt5 is present, then use from PyQt5.QtCore import PYQT_VERSION_STR. "
            "Do not chain many import checks as separate failing native commands if a structured check is possible."
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
    step_title = step.get("title") or step.get("instruction") or f"Step {step_index + 1}"
    step_id = step.get("id") or step.get("step_id")
    python_receipt = (
        python_toolchain_from_runtime_summary((db.get_device_profile(step_device_id) or {}).get("python_runtime_summary"), device_id=step_device_id)
        or resolve_python_toolchain(
            {"device_id": step_device_id, "python_toolchain_receipt": shared.get("python_toolchain_receipt")},
            commands_log,
        )
    )

    def append_step_command(action: str, command: str, device_id: str | None, result: dict | None, *, status: str | None = None) -> dict:
        entry = {
            "action": action,
            "command": command,
            "device_id": device_id,
            "target_device_id": device_id,
            "device_name": shared.get("current_hostname") or device_id,
            "hostname": shared.get("current_hostname") or device_id,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "iteration": iteration + 1,
            "step_index": step_index,
            "step_title": step_title,
            "status": status or _pipeline_command_status(action, result),
        }
        if step_id is not None:
            entry["step_id"] = step_id
        entry.update(tool_log_fields(action, result, command, device_id))
        commands_log.append(entry)
        return entry

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

            rewrite_error = None
            if fn_name == "execute_cmd":
                rewritten_command, rewrite_error = rewrite_python_command(fn_args.get("command", ""), python_receipt)
                fn_args["command"] = rewritten_command

            budget_error = command_budget.register(fn_name, fn_args.get("command", ""))
            if budget_error:
                guard_entry = budget_guard_entry(budget_error)
                append_step_command(
                    guard_entry.get("action", "budget_guard"),
                    guard_entry.get("command", "[budget_guard]"),
                    target_device,
                    guard_entry.get("result"),
                    status="blocked",
                )
                return {
                    "status": "error",
                    "answer": budget_error,
                    "commands": commands_log,
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

                append_step_command(fn_name, fn_args.get("command", ""), target_device, tool_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False)[:2000],
                })
                continue

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

                command_error = classify_command_error(tool_result, fn_args.get("command", ""))
                if command_error.get("error_type") != "none":
                    tool_result = dict(tool_result)
                    tool_result["command_error"] = command_error
                    tool_result["error_type"] = command_error.get("error_type")
                    if command_error.get("missing_packages"):
                        tool_result["missing_packages"] = command_error["missing_packages"]

                append_step_command(fn_name, fn_args.get("command", ""), target_device, tool_result)
                python_receipt = (
                    python_toolchain_from_runtime_summary((db.get_device_profile(target_device) or {}).get("python_runtime_summary"), device_id=target_device)
                    or resolve_python_toolchain({"device_id": target_device}, commands_log)
                )
                env_guard_error = command_budget.observe_execute_result(
                    fn_args.get("command", ""),
                    tool_result,
                )
                if env_guard_error:
                    if is_recoverable_command_error(command_error):
                        tool_result["command_error"]["guard_message"] = env_guard_error
                        commands_log[-1]["result"] = tool_result
                        commands_log[-1]["status"] = _pipeline_command_status(fn_name, tool_result)
                    else:
                        guard_entry = budget_guard_entry(env_guard_error)
                        append_step_command(
                            guard_entry.get("action", "budget_guard"),
                            guard_entry.get("command", "[budget_guard]"),
                            target_device,
                            guard_entry.get("result"),
                            status="blocked",
                        )
                        return {
                            "status": "error",
                            "answer": env_guard_error,
                            "commands": commands_log,
                        }
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
                append_step_command(
                    fn_name,
                    f"[{mode}] {fn_args.get('path', '')} | {preview}...",
                    target_device,
                    tool_result,
                )

            elif fn_name == "get_file_link":
                set_current_step(poll_task_id, f"Формирую ссылку: {step.get('title', '')[:50]}")
                try:
                    file_path = fn_args["file_path"]
                    url = get_file_link_fn(target_device, file_path)
                    tool_result = {"url": url, "file_path": file_path}
                except Exception as exc:
                    tool_result = {"error": str(exc)}

                append_step_command(
                    fn_name,
                    f"[скачать] {fn_args.get('file_path', '')}",
                    target_device,
                    tool_result,
                )

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

                append_step_command(
                    fn_name,
                    f"[web_search] {fn_args.get('query', '')[:80]}",
                    target_device,
                    tool_result if not isinstance(tool_result, dict) or "error" in tool_result else {"ok": True},
                )

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
                        tool_result = {"error": str(exc)}
                append_step_command(
                    fn_name,
                    "[memory] remember_fact",
                    target_device,
                    tool_result,
                )

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
                append_step_command(
                    fn_name,
                    "[memory] forget_fact",
                    target_device,
                    tool_result,
                )

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
    conversation_context = build_conversation_context(chat_history, user_message)
    shared["conversation_context"] = conversation_context

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
        recovery_warnings = []
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
            worker_shared["conversation_context"] = conversation_context
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
                    step_index=idx,
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

            step_commands = worker_result.get("commands", [])
            if worker_result.get("status") == "ok":
                step_status = "recovered" if (
                    _step_has_failed_command(step_commands, idx)
                    and (
                        _step_success_after_failure(step_commands, idx)
                        or any(_verification_command_succeeded(cmd) for cmd in step_commands)
                    )
                ) else "done"
            else:
                step_status = "failed"
                if _step_has_recoverable_failure(step_commands, idx):
                    recovery_warnings.append(f"step_{idx}_had_recoverable_failure")
            db.update_step(db_task_id, idx, step_status, summary=step_summary[:500])
            push_tasks_view(poll_task_id, created_task_ids)

            step_record = {
                "idx": idx,
                "title": step["title"],
                "instruction": step["instruction"],
                "device_id": step_device_id,
                "hostname": worker_shared.get("current_hostname", "unknown"),
                "status": step_status,
                "summary": step_summary,
            }
            if step_status == "recovered":
                step_record["recovery_reason"] = "failed command recovered by successful verification in the same step"
            step_results.append(step_record)

            if step_status not in {"done", "recovered"}:
                pipeline_failed = True
                failure_reason = step_summary
                if not _step_has_recoverable_failure(step_commands, idx):
                    break

        final_verification_ok = any(_verification_command_succeeded(command) for command in all_commands)
        if final_verification_ok:
            unrecovered_failure = False
            for step_record in step_results:
                if step_record.get("status") == "failed" and _step_has_recoverable_failure(all_commands, step_record["idx"]):
                    step_record["status"] = "recovered"
                    step_record["recovery_reason"] = "failed step recovered by later artifact verification"
                    db.update_step(
                        db_task_id,
                        step_record["idx"],
                        "recovered",
                        summary=(step_record.get("summary") or "")[:500],
                    )
                elif step_record.get("status") in {"failed", "blocked"}:
                    unrecovered_failure = True
            pipeline_failed = unrecovered_failure
            if not pipeline_failed:
                failure_reason = ""

        task_status = "failed" if pipeline_failed else (
            "completed_with_recovery"
            if any(step.get("status") == "recovered" for step in step_results)
            or any(_command_failed(command) for command in all_commands)
            else "completed"
        )
        receipt = build_pipeline_task_receipt(
            task_status=task_status,
            commands=all_commands,
            step_results=step_results,
            recovery_warnings=recovery_warnings,
            receipt_dicts=[shared.get("python_toolchain_receipt")],
        )

        db.finish_task(db_task_id, task_status)
        push_tasks_view(poll_task_id, created_task_ids)
        set_current_step(poll_task_id, "Оркестратор подводит итоги...")

        summary_payload = {
            "goal": normalized_plan["goal"],
            "pipeline_status": task_status,
            "failure_reason": failure_reason,
            "steps": step_results,
            "task_receipt": receipt,
            "conversation_context": conversation_context,
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
    final_answer = enforce_conversation_context_answer(final_answer, conversation_context)
    final_answer = enforce_trusted_answer(final_answer, all_commands)
    return {
        "answer": final_answer,
        "commands": all_commands,
        "tasks": collect_tasks(created_task_ids),
        "task_receipt": receipt,
        "training_context": {
            "os": device_info.get("os", ""),
            "hostname": device_info.get("hostname", ""),
            "method": "powershell" if "windows" in device_info.get("os", "").lower() else "bash",
        },
    }
