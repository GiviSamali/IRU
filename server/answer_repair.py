from __future__ import annotations

import json
from typing import Any

try:
    from .answer_auditor import audit_answer_payload  # type: ignore
    from .run_journal import (  # type: ignore
        ProtocolValidationError,
        append_answer_step,
        build_terminal_answer_repair_prompt,
        is_answer_text_tool,
        make_run_step,
        validate_answer_text_payload,
        validate_tool_call_batch,
    )
    from .tool_registry import DEVICE_TOOL_SCHEMAS  # type: ignore
except ImportError:
    from answer_auditor import audit_answer_payload  # type: ignore
    from run_journal import (  # type: ignore
        ProtocolValidationError,
        append_answer_step,
        build_terminal_answer_repair_prompt,
        is_answer_text_tool,
        make_run_step,
        validate_answer_text_payload,
        validate_tool_call_batch,
    )
    from tool_registry import DEVICE_TOOL_SCHEMAS  # type: ignore


ANSWER_TEXT_ONLY_TOOLS = [
    tool
    for tool in DEVICE_TOOL_SCHEMAS
    if tool.get("function", {}).get("name") == "answer_text"
]


async def run_answer_only_repair_turn(
    *,
    client,
    cfg: dict[str, Any],
    model: str,
    messages: list[dict[str, Any]],
    user_request: str,
    journal: list[dict[str, Any]],
    chat_completion_request_fn,
    target_device_id: str | None = None,
    hostname: str | None = None,
    iteration: int | None = None,
) -> dict[str, Any]:
    repair_prompt = build_terminal_answer_repair_prompt(user_request, journal)
    repair_messages = list(messages) + [{"role": "user", "content": repair_prompt}]
    try:
        data = await chat_completion_request_fn(
            client=client,
            cfg=cfg,
            model=model,
            messages=repair_messages,
            tools=ANSWER_TEXT_ONLY_TOOLS,
            max_tokens=cfg.get("max_tokens", 4096),
            tool_choice="required",
        )
    except Exception as exc:
        return {"ok": False, "reason": f"repair request failed: {type(exc).__name__}: {exc}"}

    choice = data["choices"][0]
    assistant_msg = choice.get("message") or {}
    tool_calls = assistant_msg.get("tool_calls")
    try:
        tool_call = validate_tool_call_batch(tool_calls)
        fn_name = tool_call["function"]["name"]
        if not is_answer_text_tool(fn_name):
            raise ProtocolValidationError("repair turn must call answer_text only")
        fn_args = json.loads(tool_call["function"].get("arguments") or "{}")
        answer_payload = validate_answer_text_payload(fn_args, journal)
    except (ProtocolValidationError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"repair answer invalid: {exc}"}

    audit_ok, audit_reason, audit_infra_error = await audit_answer_payload(
        client=client,
        cfg=cfg,
        chat_completion_request_fn=chat_completion_request_fn,
        user_request=user_request,
        current_run_journal=journal,
        answer_payload=answer_payload,
    )
    if audit_infra_error:
        journal.append(make_run_step(
            journal=journal,
            tool_name="answer_auditor",
            result={"error": audit_reason},
            command="[system] answer_auditor",
            target_device_id=target_device_id,
            hostname=hostname,
            iteration=iteration,
            tool_type="system",
            status="failed",
            summary="auditor_error",
        ))
        return {"ok": False, "reason": audit_reason}
    if not audit_ok:
        return {"ok": False, "reason": f"repair answer rejected by auditor: {audit_reason}"}

    entry = append_answer_step(
        journal,
        "answer_text",
        answer_payload,
        target_device_id=target_device_id,
        hostname=hostname,
        iteration=iteration,
    )
    return {"ok": True, "answer": answer_payload["text"], "entry": entry}
