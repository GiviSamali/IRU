import httpx

try:
    from .controller_prompts import INSTRUCTION_TEXT, ONBOARDING_PROMPT  # type: ignore
    from .controller_shared import build_chat_messages  # type: ignore
    from .llm_usage import extract_usage, record_llm_usage_event  # type: ignore
except ImportError:
    from controller_prompts import INSTRUCTION_TEXT, ONBOARDING_PROMPT  # type: ignore
    from controller_shared import build_chat_messages  # type: ignore
    from llm_usage import extract_usage, record_llm_usage_event  # type: ignore


async def process_onboarding_message(
    user_message: str,
    chat_history: list[dict] | None = None,
    *,
    usage_context: dict | None = None,
    load_llm_config_fn,
    current_datetime_msk_fn,
) -> dict:
    """
    Режим без устройств: простой чат с LLM без tools.
    Помогает пользователю подключить первое устройство.
    """
    cfg = load_llm_config_fn()

    system_msg = ONBOARDING_PROMPT.format(
        instruction_text=INSTRUCTION_TEXT,
        current_datetime_msk=current_datetime_msk_fn(),
    )

    messages = [{"role": "system", "content": system_msg}]

    if chat_history:
        history_msgs = build_chat_messages(chat_history[:-1])
        messages.extend(history_msgs)

    messages.append({"role": "user", "content": user_message})
    usage_ctx = {
        **(usage_context or {}),
        "route": (usage_context or {}).get("route") or "onboarding",
        "phase": (usage_context or {}).get("phase") or "onboarding",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg["model"],
                    "messages": messages,
                    "max_tokens": cfg.get("max_tokens", 4096),
                    "temperature": cfg.get("temperature", 0.0),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            record_llm_usage_event(
                usage_context=usage_ctx,
                model=cfg.get("model"),
                usage=extract_usage(data),
                cfg=cfg,
                request_ok=True,
                phase="onboarding",
            )
    except Exception as exc:
        record_llm_usage_event(
            usage_context=usage_ctx,
            model=cfg.get("model"),
            cfg=cfg,
            request_ok=False,
            error_type=type(exc).__name__,
            error_message=str(exc),
            phase="onboarding",
        )
        raise

    answer = data["choices"][0]["message"].get("content", "")
    return {"answer": answer, "commands": []}
