try:
    from .controller_prompts import DYNAMIC_CONTEXT_RULES, WORKER_EXECUTION_RULES, INSTRUCTION_TEXT, ONBOARDING_PROMPT
    from .controller_shared import build_memory_block
except ImportError:
    from controller_prompts import DYNAMIC_CONTEXT_RULES, WORKER_EXECUTION_RULES, INSTRUCTION_TEXT, ONBOARDING_PROMPT
    from controller_shared import build_memory_block


async def process_onboarding_message(
    user_message: str,
    chat_history: list[dict] | None = None,
    *,
    usage_context: dict | None = None,
    load_llm_config_fn,
    current_datetime_msk_fn,
) -> dict:
    """
    Чат без устройств с серверным web_search.
    Помогает пользователю подключить первое устройство.
    """
    cfg = load_llm_config_fn()
    try:
        from .controller_non_pipeline import process_non_pipeline_command
        from .controller import _chat_completion_request
        from .controller_tools import NON_PIPELINE_TOOLS
    except ImportError:
        from controller_non_pipeline import process_non_pipeline_command
        from controller import _chat_completion_request
        from controller_tools import NON_PIPELINE_TOOLS
    user_id = (usage_context or {}).get("user_id")
    system_msg = DYNAMIC_CONTEXT_RULES + WORKER_EXECUTION_RULES + ONBOARDING_PROMPT.format(
        instruction_text=INSTRUCTION_TEXT, current_datetime_msk=current_datetime_msk_fn())
    if user_id is not None:
        system_msg += "\n" + build_memory_block(None, str(user_id))

    async def unavailable(*args,**kwargs):
        return {'status':'blocked','dispatched':False,'error':'device_unavailable_for_server_assignment'}
    return await process_non_pipeline_command(user_message=user_message,device_id='server',
        device_info={'os':'server','connected':False},send_command_fn=unavailable,
        get_file_link_fn=lambda *args:None,chat_history=chat_history,user_id=user_id,
        chat_id=(usage_context or {}).get('chat_id'),modes={},
        poll_task_id=(usage_context or {}).get('poll_task_id'),cfg=cfg,
        system_msg=system_msg,
        machine_guid=None,mem_user_id=str(user_id) if user_id is not None else None,
        non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=20,
        pick_model_fn=lambda cfg,modes:cfg['model'],chat_completion_request_fn=_chat_completion_request,
        usage_context=usage_context)
