from __future__ import annotations

import json
import os
from typing import Any

try:
    from .run_journal import wrap_tool_result_for_llm
except ImportError:
    from run_journal import wrap_tool_result_for_llm  # type: ignore


AUDITOR_SYSTEM_PROMPT = """\
Decide whether this answer is valid under the Tool-Only Agent Protocol.
A valid answer either:
1. is pure conceptual/conversational text that does not claim current external state or completed action;
2. or is grounded in current-run journal steps via basis.
Previous chat history is not evidence.
Do not use keyword matching. Judge semantically.
Return strict JSON only:
{ "valid": true|false, "reason": "string" }
"""


def answer_auditor_enabled(cfg: dict[str, Any] | None) -> bool:
    cfg = cfg or {}
    env_value = os.environ.get("IRU_ANSWER_AUDITOR_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() not in {"0", "false", "no", "off"}
    if cfg.get("answer_auditor_enabled") is not None:
        return bool(cfg.get("answer_auditor_enabled"))
    model = str(cfg.get("model") or "")
    if model.startswith("mock"):
        return False
    return True


def _strict_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _compact_journal(journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for entry in journal:
        if not entry.get("step_id"):
            continue
        compact.append(wrap_tool_result_for_llm(entry))
    return compact


async def audit_answer_payload(
    *,
    client,
    cfg: dict[str, Any],
    chat_completion_request_fn,
    user_request: str,
    current_run_journal: list[dict[str, Any]],
    answer_payload: dict[str, Any],
) -> tuple[bool, str, bool]:
    if not answer_auditor_enabled(cfg):
        return True, "auditor disabled", False

    payload = {
        "user_request": user_request,
        "current_run_journal": _compact_journal(current_run_journal),
        "answer_payload": answer_payload,
    }
    messages = [
        {"role": "system", "content": AUDITOR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    model = cfg.get("answer_auditor_model") or cfg.get("model", "deepseek-chat")
    last_reason = "auditor returned invalid JSON"
    for attempt in range(2):
        try:
            data = await chat_completion_request_fn(
                client=client,
                cfg=cfg,
                model=model,
                messages=messages,
                tools=None,
                max_tokens=min(int(cfg.get("answer_auditor_max_tokens", 300) or 300), 500),
            )
            content = (data["choices"][0]["message"].get("content") or "").strip()
        except Exception as exc:
            return False, f"auditor_error: {type(exc).__name__}: {exc}", True

        parsed = _strict_json_object(content)
        if parsed is None:
            last_reason = "auditor returned invalid JSON"
            messages.append({
                "role": "user",
                "content": 'Return strict JSON only: { "valid": true|false, "reason": "string" }',
            })
            continue
        valid = parsed.get("valid")
        reason = str(parsed.get("reason") or "").strip()
        if isinstance(valid, bool):
            return valid, reason or ("valid" if valid else "invalid"), False
        last_reason = "auditor JSON missing boolean valid"
        if attempt == 0:
            messages.append({
                "role": "user",
                "content": 'The "valid" field must be boolean. Return strict JSON only.',
            })
    return False, last_reason, False
