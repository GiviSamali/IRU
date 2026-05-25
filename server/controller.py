"""
Controller router for IRU LLM flows.

This module is intentionally thin:
1. builds normalized runtime context for the current request;
2. selects an execution route and toolset;
3. delegates execution to specialized modules.

Execution details live outside this file:
- controller_non_pipeline.py for standard tool loops
- controller_pipeline.py for pipeline/subagent orchestration
- controller_onboarding.py for no-device onboarding chat
- controller_prompts.py for prompt text
- controller_tools.py for tool schemas and toolset registries
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

try:
    from .controller_non_pipeline import process_non_pipeline_command as _process_non_pipeline_command  # type: ignore
    from .controller_onboarding import process_onboarding_message as _process_onboarding_message  # type: ignore
    from .controller_pipeline import process_pipeline_subagents as _process_pipeline_subagents  # type: ignore
    from .controller_prompts import (  # type: ignore
        SYSTEM_PROMPT_TEMPLATE,
        WINDOWS_RULES,
        LINUX_RULES,
        _CLASSIFY_SYSTEM,
    )
    from .controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        build_target_device_block,
        current_datetime_msk as _current_datetime_msk,
        strip_markdown,
    )
    from .controller_tools import TOOLSET_REGISTRY  # type: ignore
    from .device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from .python_toolchain import build_python_toolchain_block, get_cached_python_toolchain  # type: ignore
except ImportError:
    from controller_non_pipeline import process_non_pipeline_command as _process_non_pipeline_command  # type: ignore
    from controller_onboarding import process_onboarding_message as _process_onboarding_message  # type: ignore
    from controller_pipeline import process_pipeline_subagents as _process_pipeline_subagents  # type: ignore
    from controller_prompts import (  # type: ignore
        SYSTEM_PROMPT_TEMPLATE,
        WINDOWS_RULES,
        LINUX_RULES,
        _CLASSIFY_SYSTEM,
    )
    from controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        build_target_device_block,
        current_datetime_msk as _current_datetime_msk,
        strip_markdown,
    )
    from controller_tools import TOOLSET_REGISTRY  # type: ignore
    from device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from python_toolchain import build_python_toolchain_block, get_cached_python_toolchain  # type: ignore
import asyncio
import httpx
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "llm_config.json"


# ── Конфигурация LLM ────────────────────────────────────────────────────

def load_llm_config() -> dict:
    """Загрузить конфиг LLM из llm_config.json.
    API key берётся из переменной окружения DEEPSEEK_API_KEY (приоритет)
    или из поля api_key в llm_config.json (фоллбэк).
    """
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
    return cfg


logger = logging.getLogger("iru.classify")

# ── Быстрые слова-триггеры для PLAN ──────────────────────────────────────
_PLAN_KEYWORDS = ("план", "пошагово", "по шагам")

async def classify_task_complexity(message: str) -> tuple[str, str]:
    """Лёгкий LLM-вызов для классификации задачи: PLAN или SIMPLE.

    Возвращает (kind, plan_desc):
      - ("PLAN", "описание")  — сложная задача
      - ("SIMPLE", "")        — простая задача
    """
    # Fast-path: ключевые слова → сразу PLAN без LLM
    msg_lower = message.lower()
    for kw in _PLAN_KEYWORDS:
        if kw in msg_lower:
            logger.info("[classify] fast-path keyword=%r → PLAN, message=%r", kw, message[:100])
            return ("PLAN", "Запрошен пошаговый план")

    cfg = load_llm_config()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_SYSTEM},
                        {"role": "user", "content": message},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        answer = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:
        logger.warning("[classify] LLM error, fallback to SIMPLE: %s", exc)
        return ("SIMPLE", "")

    if answer.upper().startswith("PLAN:"):
        plan_desc = answer[5:].strip()
        logger.info("[classify] kind=PLAN plan_desc=%r message=%r", plan_desc[:80], message[:100])
        return ("PLAN", plan_desc)

    logger.info("[classify] kind=SIMPLE message=%r", message[:100])
    return ("SIMPLE", "")


# ── Системный промпт (шаблон) ───────────────────────────────────────────

MAX_ITERATIONS = 20


@dataclass(frozen=True)
class LLMRuntimeContext:
    cfg: dict
    os_info: str
    hostname: str
    os_version: str
    devices_block: str
    profile_block: str
    memory_block: str
    target_device_block: str
    os_rules: str
    current_datetime_msk: str
    machine_guid: str | None
    mem_user_id: str | None
    python_toolchain_block: str = ""
    device_context_block: str = ""


@dataclass(frozen=True)
class LLMRouteSpec:
    name: str
    executor: Callable[..., Awaitable[dict]]
    toolset_name: str | None = None


def _pick_model(cfg: dict, modes: dict | None) -> str:
    """Выбрать модель LLM: deepseek-reasoner для сложных режимов, deepseek-chat иначе."""
    base = cfg.get("model", "deepseek-chat")
    reasoner = cfg.get("model_reasoner", "deepseek-reasoner")
    is_complex = bool(modes) and (modes.get("pipeline") or modes.get("autonomous"))
    return reasoner if is_complex else base


async def _chat_completion_request(
    client: httpx.AsyncClient,
    cfg: dict,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
    tool_choice: str | dict | None = None,
) -> dict:
    """Единая обёртка для вызова chat/completions с ретраями."""
    request_json = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or cfg.get("max_tokens", 4096),
    }
    if tools is not None:
        request_json["tools"] = tools
        if tool_choice is not None and cfg.get("required_tool_choice_supported", True):
            request_json["tool_choice"] = tool_choice
        else:
            request_json["tool_choice"] = "auto"

    base_model = cfg.get("model", "deepseek-chat")
    if model == base_model:
        request_json["temperature"] = cfg.get("temperature", 0.0)

    resp = None
    for _attempt in range(2):
        try:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=request_json,
            )
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError as _he:
            if (
                _he.response.status_code == 400
                and request_json.get("tool_choice") == "required"
                and _attempt == 0
            ):
                fallback_json = dict(request_json)
                fallback_json["tool_choice"] = "auto"
                print(
                    "[llm] 400 with tool_choice=required; retrying with tool_choice=auto. "
                    f"body={_he.response.text[:500]}"
                )
                try:
                    resp = await client.post(
                        f"{cfg['base_url']}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {cfg['api_key']}",
                            "Content-Type": "application/json",
                        },
                        json=fallback_json,
                    )
                    resp.raise_for_status()
                    break
                except httpx.HTTPStatusError:
                    raise
            if _he.response.status_code >= 500 and _attempt == 0:
                print(f"[llm] 5xx retry: {_he.response.status_code}")
                await asyncio.sleep(2)
                continue
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as _ne:
            if _attempt == 0:
                print(f"[llm] network retry: {type(_ne).__name__}")
                await asyncio.sleep(2)
                continue
            raise

    return resp.json()


def _get_toolset(name: str | None) -> list[dict] | None:
    if not name:
        return None
    return TOOLSET_REGISTRY[name]


def _resolve_memory_user_id(user_id: int | None, machine_guid: str | None) -> str | None:
    if user_id:
        return str(user_id)
    if machine_guid:
        return f"anon_{machine_guid}"
    return None


def _resolve_os_rules(os_info: str) -> str:
    os_lower = (os_info or "").lower()
    return LINUX_RULES if "linux" in os_lower else WINDOWS_RULES


def _build_runtime_context(
    *,
    device_id: str,
    all_devices: dict,
    device_info: dict,
    device_profile: dict | None,
    user_id: int | None,
) -> LLMRuntimeContext:
    os_info = device_info.get("os", "Windows")
    hostname = device_info.get("hostname", "unknown")
    os_version = device_info.get("os_version", "")
    machine_guid = (device_profile or {}).get("machine_guid") or None
    mem_user_id = _resolve_memory_user_id(user_id, machine_guid)
    python_receipt = get_cached_python_toolchain({"device_id": device_id, "machine_guid": machine_guid})
    manifest = build_minimal_llm_context(device_id, all_devices, device_profile)

    return LLMRuntimeContext(
        cfg=load_llm_config(),
        os_info=os_info,
        hostname=hostname,
        os_version=os_version,
        devices_block=build_devices_block(all_devices),
        profile_block=build_device_profile_block(device_profile),
        memory_block=build_memory_block(machine_guid, mem_user_id),
        target_device_block=build_target_device_block("", device_info, device_profile),
        python_toolchain_block=build_python_toolchain_block(python_receipt),
        device_context_block=format_minimal_llm_context_block(manifest),
        os_rules=_resolve_os_rules(os_info),
        current_datetime_msk=_current_datetime_msk(),
        machine_guid=machine_guid,
        mem_user_id=mem_user_id,
    )


def _build_non_pipeline_system_prompt(
    *,
    runtime: LLMRuntimeContext,
    device_id: str,
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        devices_block=runtime.devices_block,
        current_device_id=device_id,
        current_hostname=runtime.hostname,
        current_os=runtime.os_info,
        current_os_version=runtime.os_version,
        device_profile_block=runtime.profile_block,
        device_memory_block=runtime.memory_block,
        device_context_block=runtime.device_context_block,
        target_device_block=(
            runtime.target_device_block.replace("device_id: ", f"device_id: {device_id}", 1)
            + "\n"
            + runtime.python_toolchain_block
        ),
        os_rules=runtime.os_rules,
        current_datetime_msk=runtime.current_datetime_msk,
    )


def _select_llm_route(modes: dict) -> LLMRouteSpec:
    route_name = "pipeline" if modes.get("pipeline") else "non_pipeline"
    return ROUTE_REGISTRY[route_name]


def _build_pipeline_route_kwargs(
    *,
    route: LLMRouteSpec,
    runtime: LLMRuntimeContext,
    user_message: str,
    device_id: str,
    device_info: dict,
    all_devices: dict,
    send_command_fn,
    get_file_link_fn,
    chat_history: list[dict] | None,
    user_id: int | None,
    chat_id: int | None,
    device_profile: dict | None,
    modes: dict,
    poll_task_id: str | None,
) -> dict:
    return {
        "user_message": user_message,
        "device_id": device_id,
        "device_info": device_info,
        "all_devices": all_devices,
        "send_command_fn": send_command_fn,
        "get_file_link_fn": get_file_link_fn,
        "chat_history": chat_history,
        "user_id": user_id,
        "chat_id": chat_id,
        "device_profile": device_profile,
        "modes": modes,
        "poll_task_id": poll_task_id,
        "load_llm_config_fn": load_llm_config,
        "pick_model_fn": _pick_model,
        "chat_completion_request_fn": _chat_completion_request,
        "worker_tools": _get_toolset(route.toolset_name),
        "windows_rules": WINDOWS_RULES,
        "linux_rules": LINUX_RULES,
    }


def _build_non_pipeline_route_kwargs(
    *,
    route: LLMRouteSpec,
    runtime: LLMRuntimeContext,
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
    system_msg: str,
    device_tool_fn=None,
) -> dict:
    return {
        "user_message": user_message,
        "device_id": device_id,
        "device_info": device_info,
        "send_command_fn": send_command_fn,
        "get_file_link_fn": get_file_link_fn,
        "chat_history": chat_history,
        "user_id": user_id,
        "chat_id": chat_id,
        "modes": modes,
        "poll_task_id": poll_task_id,
        "cfg": runtime.cfg,
        "system_msg": system_msg,
        "machine_guid": runtime.machine_guid,
        "mem_user_id": runtime.mem_user_id,
        "non_pipeline_tools": _get_toolset(route.toolset_name),
        "max_iterations": MAX_ITERATIONS,
        "pick_model_fn": _pick_model,
        "chat_completion_request_fn": _chat_completion_request,
        "device_tool_fn": device_tool_fn,
    }


def _build_route_kwargs(
    *,
    route: LLMRouteSpec,
    runtime: LLMRuntimeContext,
    user_message: str,
    device_id: str,
    device_info: dict,
    all_devices: dict,
    send_command_fn,
    get_file_link_fn,
    chat_history: list[dict] | None,
    user_id: int | None,
    chat_id: int | None,
    device_profile: dict | None,
    modes: dict,
    poll_task_id: str | None,
    device_tool_fn=None,
) -> dict:
    if route.name == "pipeline":
        return _build_pipeline_route_kwargs(
            route=route,
            runtime=runtime,
            user_message=user_message,
            device_id=device_id,
            device_info=device_info,
            all_devices=all_devices,
            send_command_fn=send_command_fn,
            get_file_link_fn=get_file_link_fn,
            chat_history=chat_history,
            user_id=user_id,
            chat_id=chat_id,
            device_profile=device_profile,
            modes=modes,
            poll_task_id=poll_task_id,
        )

    system_msg = _build_non_pipeline_system_prompt(runtime=runtime, device_id=device_id)
    if modes.get("autonomous"):
        system_msg = system_msg + "\n\n## Активные режимы\n" + (
            "АВТОНОМНЫЙ РЕЖИМ: Пользователь дал согласие на выполнение без дополнительных "
            "подтверждений. Действуй самостоятельно, не спрашивай перед каждой командой. "
            "Запрещённые системные команды всё равно не выполняй."
        )
    return _build_non_pipeline_route_kwargs(
        route=route,
        runtime=runtime,
        user_message=user_message,
        device_id=device_id,
        device_info=device_info,
        send_command_fn=send_command_fn,
        get_file_link_fn=get_file_link_fn,
        chat_history=chat_history,
        user_id=user_id,
        chat_id=chat_id,
        modes=modes,
        poll_task_id=poll_task_id,
        system_msg=system_msg,
        device_tool_fn=device_tool_fn,
    )


ROUTE_REGISTRY = {
    "pipeline": LLMRouteSpec(
        name="pipeline",
        executor=_process_pipeline_subagents,
        toolset_name="worker",
    ),
    "non_pipeline": LLMRouteSpec(
        name="non_pipeline",
        executor=_process_non_pipeline_command,
        toolset_name="non_pipeline",
    ),
}


async def process_nl_command(
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
    device_tool_fn=None,
) -> dict:
    """
    Обработка команды на естественном языке.

    Args:
        user_message: текст пользователя
        device_id: ID текущего выбранного устройства
        device_info: информация о текущем устройстве
        all_devices: словарь всех подключённых устройств пользователя
        send_command_fn: async fn(device_id, action, params) -> result
        get_file_link_fn: fn(device_id, file_path) -> url_string
        chat_history: история сообщений чата (для памяти)
        user_id: ID пользователя (для записи training data)
        chat_id: ID чата (для записи training data)

    Returns:
        {"answer": str, "commands": [...], "training_context": {...}}
    """
    modes = modes or {}
    runtime = _build_runtime_context(
        device_id=device_id,
        all_devices=all_devices,
        device_info=device_info,
        device_profile=device_profile,
        user_id=user_id,
    )
    route = _select_llm_route(modes)
    route_kwargs = _build_route_kwargs(
        route=route,
        runtime=runtime,
        user_message=user_message,
        device_id=device_id,
        device_info=device_info,
        all_devices=all_devices,
        send_command_fn=send_command_fn,
        get_file_link_fn=get_file_link_fn,
        chat_history=chat_history,
        user_id=user_id,
        chat_id=chat_id,
        device_profile=device_profile,
        modes=modes,
        poll_task_id=poll_task_id,
        device_tool_fn=device_tool_fn,
    )
    return await route.executor(**route_kwargs)


async def process_onboarding_message(
    user_message: str,
    chat_history: list[dict] | None = None,
) -> dict:
    return await _process_onboarding_message(
        user_message=user_message,
        chat_history=chat_history,
        load_llm_config_fn=load_llm_config,
        current_datetime_msk_fn=_current_datetime_msk,
    )
