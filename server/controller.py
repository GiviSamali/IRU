"""
Controller router for IRU LLM flows.

This module is intentionally thin:
1. builds normalized runtime context for the current request;
2. selects an execution route and toolset;
3. delegates execution to specialized modules.

Execution details live outside this file:
- controller_non_pipeline.py for standard tool loops
- worker_scheduler.py for goal readiness and task admission
- controller_onboarding.py for no-device onboarding chat
- controller_prompts.py for prompt text
- controller_tools.py for tool schemas and toolset registries
"""

import json
import time
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

try:
    from .controller_non_pipeline import process_non_pipeline_command as _process_non_pipeline_command  # type: ignore
    from .controller_onboarding import process_onboarding_message as _process_onboarding_message  # type: ignore
    from .controller_prompts import (  # type: ignore
        SYSTEM_PROMPT_TEMPLATE,
        DYNAMIC_CONTEXT_RULES,
        WINDOWS_RULES,
        LINUX_RULES,
    )
    from .controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        data_only_context,
        build_recent_artifact_context,
        build_target_device_block,
        current_datetime_msk as _current_datetime_msk,
        format_recent_artifact_context_block,
        strip_markdown,
    )
    from .controller_tools import TOOLSET_REGISTRY  # type: ignore
    from .device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from .llm_usage import LLM_ENTITY, extract_usage, record_llm_usage_event  # type: ignore
    from .python_toolchain import build_python_toolchain_block, get_cached_python_toolchain  # type: ignore
except ImportError:
    from controller_non_pipeline import process_non_pipeline_command as _process_non_pipeline_command  # type: ignore
    from controller_onboarding import process_onboarding_message as _process_onboarding_message  # type: ignore
    from controller_prompts import (  # type: ignore
        SYSTEM_PROMPT_TEMPLATE,
        DYNAMIC_CONTEXT_RULES,
        WINDOWS_RULES,
        LINUX_RULES,
    )
    from controller_shared import (  # type: ignore
        ConfirmationRequired,
        build_device_profile_block,
        build_devices_block,
        build_memory_block,
        data_only_context,
        build_recent_artifact_context,
        build_target_device_block,
        current_datetime_msk as _current_datetime_msk,
        format_recent_artifact_context_block,
        strip_markdown,
    )
    from controller_tools import TOOLSET_REGISTRY  # type: ignore
    from device_context import build_minimal_llm_context, format_minimal_llm_context_block  # type: ignore
    from llm_usage import LLM_ENTITY, extract_usage, record_llm_usage_event  # type: ignore
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


try:
    from .run_journal import record_lifecycle_event
except ImportError:
    from run_journal import record_lifecycle_event

logger = logging.getLogger("iru.classify")

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
    recent_artifact_context_block: str = ""


@dataclass(frozen=True)
class LLMRouteSpec:
    name: str
    executor: Callable[..., Awaitable[dict]]
    toolset_name: str | None = None


def _pick_model(cfg: dict, modes: dict | None) -> str:
    """Choose the configured ordinary/autonomous model; PLAN is unsupported."""
    if (modes or {}).get("pipeline"):
        raise ValueError("execution_mode_unsupported")
    base = cfg.get("model", "deepseek-v4-flash")
    reasoner = cfg.get("model_reasoner", "deepseek-v4-pro")
    is_complex = bool(modes) and modes.get("autonomous")
    return reasoner if is_complex else base


def _thinking_request_fields(
    cfg: dict,
    model: str,
    *,
    usage_context: dict | None = None,
    phase: str | None = None,
) -> dict:
    """Return provider thinking fields for the selected DeepSeek V4 model."""
    request_phase = phase or (usage_context or {}).get("phase")
    if (request_phase or "").endswith((".truncation_recovery", ".structured_output")) and model.startswith("deepseek-"):
        # Documented Chat Completions toggle; do not combine disabled thinking with effort.
        return {"thinking": {"type": "disabled"}}
    if (request_phase or "").startswith(("window_control.", "browser_bridge.")):
        # Ordinary window/browser selection reserves output for structured calls.
        return {"thinking": {"type": "disabled"}}

    if (request_phase or "").endswith(("answer_auditor", ".auditor", "result_coverage", "goal_completion")):
        # The auditor has a small strict-JSON output budget, not a worker's CoT budget.
        return {"thinking": {"type": "disabled"}}
    base_model = cfg.get("model", "deepseek-v4-flash")
    reasoner_model = cfg.get("model_reasoner", "deepseek-v4-pro")

    if model == reasoner_model:
        reasoning_effort = cfg.get("reasoning_effort", "high")
        fields = {"thinking": {"type": "enabled"}}
        if reasoning_effort:
            fields["reasoning_effort"] = reasoning_effort
        return fields

    if LLM_ENTITY.get() == "worker" and model.startswith("deepseek-") and ((request_phase or "").startswith("non_pipeline.") or request_phase == "onboarding"):
        return {"thinking": {"type": "enabled"}, "reasoning_effort": cfg.get("reasoning_effort") or "low"}
    return {"thinking": {"type": "disabled"}}


async def _chat_completion_request(
    client: httpx.AsyncClient,
    cfg: dict,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
    tool_choice: str | dict | None = None,
    usage_context: dict | None = None,
    phase: str | None = None,
) -> dict:
    """Единая обёртка для вызова chat/completions с ретраями."""
    request_started=time.monotonic()
    usage_context=dict(usage_context or {})
    usage_context["metadata"]=dict(usage_context.get("metadata") or {})
    request_json = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or cfg.get("max_tokens", 32768),
    }
    if tools is not None:
        request_json["tools"] = tools
        if tool_choice is not None and cfg.get("required_tool_choice_supported", True):
            request_json["tool_choice"] = tool_choice
        else:
            request_json["tool_choice"] = "auto"

    request_json.update(
        _thinking_request_fields(
            cfg,
            model,
            usage_context=usage_context,
            phase=phase,
        )
    )

    # DeepSeek thinking tool calls require preserved reasoning history and omit
    # tool_choice. Server-side cardinality/terminal validation remains mandatory.
    thinking = request_json.get("thinking", {}).get("type") == "enabled"
    if thinking and model.startswith("deepseek-"):
        request_json.pop("tool_choice", None)
        # Synthetic/history context has no prior model reasoning. Preserve actual
        # returned CoT unchanged, and explicitly mark absent context CoT empty.
        request_json["messages"] = [
            {**message, "reasoning_content": message.get("reasoning_content") or ""}
            if message.get("role") == "assistant" else message for message in messages]
    base_model = cfg.get("model", "deepseek-v4-flash")
    if model == base_model and not thinking:
        request_json["temperature"] = cfg.get("temperature", 0.0)

    try:from .context_budget import fit_request, calibrated_config
    except ImportError:from context_budget import fit_request, calibrated_config
    request_json, token_budget = fit_request(request_json, calibrated_config(cfg,model), usage_context.get('context_current_user_index'))
    usage_context['metadata']['context_budget'] = token_budget

    resp = None
    last_error: Exception | None = None
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
            last_error = _he
            if (
                _he.response.status_code == 400
                and request_json.get("tool_choice") == "required"
                and _attempt == 0
            ):
                fallback_json = dict(request_json)
                fallback_json["tool_choice"] = "auto"
                print(
                    "[llm] 400 with tool_choice=required; retrying with tool_choice=auto. "
                    "response body omitted"
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
                except httpx.HTTPStatusError as fallback_error:
                    last_error = fallback_error
                    usage_context["metadata"]["latency_ms"]=int((time.monotonic()-request_started)*1000)
                    record_llm_usage_event(
                        usage_context=usage_context,
                        model=model,
                        cfg=cfg,
                        request_ok=False,
                        error_type=type(fallback_error).__name__,
                        error_message=fallback_error.response.text,
                        phase=phase,
                    )
                    raise
            if _he.response.status_code >= 500 and _attempt == 0:
                print(f"[llm] 5xx retry: {_he.response.status_code}")
                await asyncio.sleep(2)
                continue
            usage_context["metadata"]["latency_ms"]=int((time.monotonic()-request_started)*1000)
            record_llm_usage_event(
                usage_context=usage_context,
                model=model,
                cfg=cfg,
                request_ok=False,
                error_type=type(_he).__name__,
                error_message=_he.response.text,
                phase=phase,
            )
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as _ne:
            last_error = _ne
            if _attempt == 0:
                print(f"[llm] network retry: {type(_ne).__name__}")
                await asyncio.sleep(2)
                continue
            usage_context["metadata"]["latency_ms"]=int((time.monotonic()-request_started)*1000)
            record_llm_usage_event(
                usage_context=usage_context,
                model=model,
                cfg=cfg,
                request_ok=False,
                error_type=type(_ne).__name__,
                error_message=str(_ne),
                phase=phase,
            )
            raise
        except Exception as exc:
            last_error = exc
            usage_context["metadata"]["latency_ms"]=int((time.monotonic()-request_started)*1000)
            record_llm_usage_event(
                usage_context=usage_context,
                model=model,
                cfg=cfg,
                request_ok=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                phase=phase,
            )
            raise

    try:
        data = resp.json()
        actual = (data.get('usage') or {}).get('prompt_tokens')
        if isinstance(actual, int):
            token_budget['actual_input_tokens'] = actual
            token_budget['estimate_underflow'] = actual > token_budget['estimated_input_tokens']
            token_budget['estimate_to_actual_ratio'] = token_budget['estimated_input_tokens']/max(1,actual)
        usage_context["metadata"]["latency_ms"]=int((time.monotonic()-request_started)*1000)
        record_llm_usage_event(
            usage_context=usage_context,
            model=model,
            usage=extract_usage(data),
            cfg=cfg,
            request_ok=True,
            phase=phase,
        )
        return data
    except Exception as exc:
        if isinstance(exc, (httpx.HTTPError, RuntimeError)):
            raise
        raise


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
    chat_history: list[dict] | None = None,
) -> LLMRuntimeContext:
    os_info = device_info.get("os", "Windows")
    hostname = device_info.get("hostname", "unknown")
    os_version = device_info.get("os_version", "")
    machine_guid = (device_profile or {}).get("machine_guid") or None
    mem_user_id = _resolve_memory_user_id(user_id, machine_guid)
    python_receipt = get_cached_python_toolchain({"user_id": user_id, "device_id": device_id, "machine_guid": machine_guid})
    manifest = build_minimal_llm_context(device_id, all_devices, device_profile)
    recent_artifact_context = build_recent_artifact_context(chat_history)

    return LLMRuntimeContext(
        cfg=load_llm_config(),
        os_info=os_info,
        hostname=hostname,
        os_version=os_version,
        devices_block=build_devices_block(all_devices),
        profile_block=build_device_profile_block(device_profile),
        memory_block=build_memory_block(machine_guid, mem_user_id, device_id),
        target_device_block=build_target_device_block("", device_info, device_profile),
        python_toolchain_block=build_python_toolchain_block(python_receipt),
        device_context_block=format_minimal_llm_context_block(manifest),
        recent_artifact_context_block=format_recent_artifact_context_block(recent_artifact_context),
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
        devices_block=data_only_context("device_inventory", runtime.devices_block),
        current_device_id=device_id,
        current_hostname=runtime.hostname,
        current_os=runtime.os_info,
        current_os_version=runtime.os_version,
        device_profile_block=data_only_context("device_profile", runtime.profile_block),
        device_memory_block=runtime.memory_block,
        device_context_block=data_only_context("device_context", runtime.device_context_block),
        recent_artifact_context_block=data_only_context("recent_artifacts", runtime.recent_artifact_context_block),
        target_device_block=data_only_context("target_device", (
            runtime.target_device_block.replace("device_id: ", f"device_id: {device_id}", 1)
            + "\n"
            + runtime.python_toolchain_block
        )),
        os_rules=runtime.os_rules,
        current_datetime_msk=runtime.current_datetime_msk,
    )


def _select_llm_route(modes: dict) -> LLMRouteSpec:
    if modes.get("pipeline"):
        raise ValueError("execution_mode_unsupported")
    return ROUTE_REGISTRY["non_pipeline"]


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
    try:
        from .controller_shared import worker_window_scope
        from .controller_prompts import BROWSER_BRIDGE_RULES
    except ImportError:
        from controller_shared import worker_window_scope
        from controller_prompts import BROWSER_BRIDGE_RULES
    window_only, effects = worker_window_scope(poll_task_id)
    if window_only:
        inventory = [{"device_id":did,"hostname":(dev.get("info") or {}).get("hostname",did)} for did,dev in all_devices.items()]
        system_msg = DYNAMIC_CONTEXT_RULES + ("\nThis task is limited to existing windows. Use window_control, never shell, "
            "synthetic input or legacy window tools. Resolve relative references from observed opaque window IDs; "
            "do not substitute another device or window. Virtual desktops are not physical monitors and are unsupported; "
            "report the limitation without a shell workaround. Unsaved content and dangerous operations retain runtime confirmation.\n"
            + data_only_context("requested_window_effects", effects)
            + data_only_context("window_device_inventory", {"current_device":device_id,"devices":inventory}))
    else:
        system_msg = _build_non_pipeline_system_prompt(runtime=runtime, device_id=device_id) + "\n" + BROWSER_BRIDGE_RULES
    if modes.get("autonomous") and not window_only:
        system_msg = system_msg + "\n\n## Активные режимы\n" + (
            "АВТОНОМНЫЙ РЕЖИМ: Выполняй обычную работу самостоятельно без лишних "
            "подтверждений. Распознанные опасные действия подтверждаются runtime отдельно. "
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
        chat_history=chat_history,
    )
    route = _select_llm_route(modes)
    usage_context = {
        "user_id": user_id,
        "chat_id": chat_id,
        "poll_task_id": poll_task_id,
        "route": route.name,
    }
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
    route_kwargs["usage_context"] = usage_context
    return await route.executor(**route_kwargs)


async def process_onboarding_message(
    user_message: str,
    chat_history: list[dict] | None = None,
    usage_context: dict | None = None,
) -> dict:
    return await _process_onboarding_message(
        user_message=user_message,
        chat_history=chat_history,
        usage_context=usage_context,
        load_llm_config_fn=load_llm_config,
        current_datetime_msk_fn=_current_datetime_msk,
    )
