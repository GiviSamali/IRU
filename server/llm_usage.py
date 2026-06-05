from __future__ import annotations

from typing import Any

try:
    from . import database as db  # type: ignore
except ImportError:
    import database as db  # type: ignore


DEEPSEEK_PRICING_USD_PER_1M = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.003625,
        "input_cache_miss": 0.435,
        "output": 0.87,
    },
}

MODEL_PRICE_ALIASES = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def extract_usage(data: dict[str, Any] | None) -> dict[str, int]:
    usage = (data or {}).get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    prompt_tokens = _int_value(usage.get("prompt_tokens"))
    completion_tokens = _int_value(usage.get("completion_tokens"))
    total_tokens = _int_value(usage.get("total_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens

    cache_hit_tokens = _int_value(
        usage.get("prompt_cache_hit_tokens", usage.get("cache_hit_tokens"))
    )
    cache_miss_tokens = _int_value(
        usage.get("prompt_cache_miss_tokens", usage.get("cache_miss_tokens"))
    )
    if prompt_tokens and not cache_hit_tokens and not cache_miss_tokens:
        cache_miss_tokens = prompt_tokens

    completion_details = usage.get("completion_tokens_details") or {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning_tokens = _int_value(completion_details.get("reasoning_tokens"))

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _pricing_key(model: str | None) -> str:
    model_text = str(model or "").strip().lower()
    return MODEL_PRICE_ALIASES.get(model_text, "deepseek-v4-flash")


def estimate_deepseek_cost_usd(
    model: str | None,
    usage: dict[str, int],
    cfg: dict[str, Any] | None = None,
) -> float:
    cfg = cfg or {}
    pricing_cfg = cfg.get("deepseek_pricing_usd_per_1m") or {}
    key = _pricing_key(model)
    pricing = dict(DEEPSEEK_PRICING_USD_PER_1M.get(key, DEEPSEEK_PRICING_USD_PER_1M["deepseek-v4-flash"]))
    if isinstance(pricing_cfg, dict):
        pricing.update(pricing_cfg.get(key) or {})

    cache_hit = _int_value(usage.get("cache_hit_tokens"))
    cache_miss = _int_value(usage.get("cache_miss_tokens"))
    completion = _int_value(usage.get("completion_tokens"))
    cost = (
        cache_hit * float(pricing["input_cache_hit"])
        + cache_miss * float(pricing["input_cache_miss"])
        + completion * float(pricing["output"])
    ) / 1_000_000
    return round(cost, 8)


def record_llm_usage_event(
    *,
    usage_context: dict[str, Any] | None,
    model: str | None,
    usage: dict[str, int] | None = None,
    cfg: dict[str, Any] | None = None,
    request_ok: bool = True,
    error_type: str | None = None,
    error_message: str | None = None,
    phase: str | None = None,
) -> None:
    context = dict(usage_context or {})
    usage = usage or {}
    effective_phase = phase or context.get("phase")
    provider = context.get("provider") or "deepseek"
    metadata = context.get("metadata")
    estimated_cost = (
        estimate_deepseek_cost_usd(model, usage, cfg)
        if request_ok
        else 0.0
    )
    try:
        db.add_llm_usage_event(
            user_id=context.get("user_id"),
            chat_id=context.get("chat_id"),
            task_id=context.get("task_id"),
            poll_task_id=context.get("poll_task_id"),
            route=context.get("route"),
            phase=effective_phase,
            provider=provider,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cache_hit_tokens=usage.get("cache_hit_tokens", 0),
            cache_miss_tokens=usage.get("cache_miss_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            estimated_cost_usd=estimated_cost,
            request_ok=request_ok,
            error_type=error_type,
            error_message=(error_message or "")[:500] if error_message else None,
            metadata=metadata,
        )
    except Exception as exc:
        print(f"[llm-usage] warning: failed to record usage event: {exc}")
