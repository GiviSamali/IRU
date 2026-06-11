from __future__ import annotations

import re
from typing import Any


TERMINAL_CORRECTION = (
    "You have sufficient current-run evidence. Call answer_text now. "
    "Do not call more tools unless the user explicitly requested deeper verification."
)


_EXECUTE_CMD_OUTCOME_RE = re.compile(r"(?im)^\s*(OK|NO|ERROR):")


def execute_cmd_outcome_marker(result: dict[str, Any] | None) -> str | None:
    """Return the first machine-readable execute_cmd marker from stdout."""
    if not isinstance(result, dict):
        return None
    stdout = str(result.get("stdout") or "")
    match = _EXECUTE_CMD_OUTCOME_RE.search(stdout)
    return match.group(1).upper() if match else None


def execute_cmd_result_is_ok(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    return (
        result.get("returncode") in (0, "0")
        and not result.get("error")
        and execute_cmd_outcome_marker(result) == "OK"
    )


def execute_cmd_result_is_negative(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return True
    if result.get("returncode") not in (None, 0, "0"):
        return True
    return execute_cmd_outcome_marker(result) in {"NO", "ERROR"}


def tool_result_terminal_sufficient(entry: dict[str, Any] | None) -> bool:
    result = (entry or {}).get("result")
    if not isinstance(result, dict):
        return False
    if result.get("terminal_sufficient"):
        return True
    tool_name = (entry or {}).get("tool_name") or (entry or {}).get("action")
    if tool_name == "execute_cmd":
        return execute_cmd_result_is_ok(result)
    if tool_name in {"app.open_url", "app_open_url"}:
        return bool(result.get("launched")) and str(result.get("status") or "") in {
            "opened_verified",
            "opened_visible_focus_failed",
            "opened_unverified",
            "opened_browser_visible",
        }
    return False


def synthesize_terminal_answer_payload(entry: dict[str, Any]) -> dict[str, Any]:
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    tool_name = entry.get("tool_name") or entry.get("action") or "tool"
    step_id = entry.get("step_id")
    status = str(result.get("status") or "").strip()

    if tool_name == "execute_cmd":
        stdout = str(result.get("stdout") or "").strip()
        text = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
        if not text:
            text = str(entry.get("summary") or "Command completed.")
    elif tool_name == "app.open_url":
        url = str(result.get("url") or "").strip()
        if status == "opened_visible_focus_failed":
            text = "Ссылка открыта, окно найдено, но сфокусировать окно не удалось."
        elif status == "opened_unverified":
            text = (
                "Ссылку отправил в браузер. Точно подтвердить активную вкладку не удалось, "
                "но команда открытия URL выполнена."
            )
        else:
            text = "Ссылка открыта."
        if url:
            text = f"{text} URL: {url}"
    else:
        text = str(result.get("summary") or entry.get("summary") or "Действие выполнено.")

    completion_state = result.get("completion_state")
    if tool_name == "execute_cmd" and not completion_state:
        completion_state = "success" if execute_cmd_result_is_ok(result) else None
    elif tool_name == "app.open_url" and not completion_state:
        completion_state = "success" if status == "opened_verified" else "partial_success"
    answer_type = "grounded_report" if completion_state == "success" else "partial_report"
    return {
        "answer_type": answer_type,
        "text": text,
        "basis": [step_id] if step_id else [],
        "self_check": {
            "depends_on_current_external_state": True,
            "claims_completed_action": completion_state == "success",
            "has_sufficient_evidence": bool(step_id),
            "missing_evidence_question": "" if step_id else "No current run step_id was available.",
        },
    }
