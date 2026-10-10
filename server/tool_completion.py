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


def execute_cmd_returncode_is_zero(code: Any) -> bool:
    """Keep the existing legacy string zero; bool/float are not exit codes."""
    return (type(code) is int and code == 0) or (type(code) is str and code == "0")


def execute_cmd_result_is_complete(result: dict[str, Any] | None) -> bool:
    """A completed receipt/observation, never proof of the user's whole goal."""
    if not isinstance(result, dict) or not execute_cmd_returncode_is_zero(result.get("returncode")) or result.get("error"):
        return False
    if result.get("status") not in (None, "", "ok", "success", "done", "completed", "executed", "finished"):
        return False
    if result.get("completion_state") not in (None, "", "success"):
        return False
    stdout = result.get("stdout")
    return isinstance(stdout, str)


def execute_cmd_result_is_ok(result: dict[str, Any] | None) -> bool:
    return execute_cmd_result_is_complete(result)


def execute_cmd_result_is_negative(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return True
    if result.get("returncode") not in (None, 0, "0"):
        return True
    return result.get("status") in {"failed", "error", "blocked", "cancelled"} or result.get("completion_state") in {"failed", "error"}


def write_content_result_is_ok(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("status") in {"failed", "error"}:
        return False
    return (result.get("status") in (None, "", "ok", "success")
            and bool(result.get("path"))
            and type(result.get("bytes_written")) is int and result["bytes_written"] >= 0)


def write_content_result_is_negative(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("status") in {"failed", "error"}:
        return True
    return result.get("status") in {"missing", "not_found", "blocked", "cancelled"}


def tool_result_terminal_sufficient(entry: dict[str, Any] | None) -> bool:
    result = (entry or {}).get("result")
    if not isinstance(result, dict):
        return False
    tool_name = (entry or {}).get("tool_name") or (entry or {}).get("action")
    # Operation receipts cannot end a potentially multi-step user goal.
    if tool_name in {"execute_cmd", "write_content"}:
        return False
    if result.get("terminal_sufficient"):
        return True
    if tool_name in {"window_control", "window.control"}:
        return result.get("status") == "success" and result.get("completion_state") == "success"
    if tool_name == "transfer_file":
        return result.get("status") == "success" and result.get("sha256_verified") is True
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
        if status == "launch_requested":
            text = "Запуск запрошен. Результат выполнения пока не подтверждён."
    elif tool_name == "write_content":
        text = str(result.get("summary") or entry.get("summary") or "OK: file_written")
    elif tool_name in {"app_launch", "app.launch"}:
        text = "Приложение открыто, окно подтверждено." if status == "launched_verified" else "Запуск приложения не подтверждён."
    elif tool_name in {"app.open_url", "app_open_url"}:
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

    if tool_name == "transfer_file":
        text = (f"Файл {result.get('filename', '')} передан с {result.get('source_device', '')} на {result.get('target_device', '')}. "
                f"Целевая копия проверена. Путь: {result.get('target_path', '')}")
    if tool_name in {"web_fill", "web.fill", "web_activate", "web.activate"}:
        text = ("Черновик заполнен; отправка не выполнялась." if tool_name in {"web_fill", "web.fill"}
                else "Элемент активирован; выполнение подтверждено браузером.")
    if tool_name in {"web_focus", "web.focus"}:
        text = "Вкладка выбрана, окно браузера в фокусе." if result.get("focused") is True else "Переключение вкладки не подтверждено."
    completion_state = result.get("completion_state")
    if tool_name in {"web_focus", "web.focus"}:
        completion_state = "success" if status == "success" and result.get("focused") is True else None
    if tool_name in {"web_fill", "web.fill", "web_activate", "web.activate"}:
        completion_state = "success" if status == "success" else None
    if tool_name == "transfer_file":
        completion_state = "success" if status == "success" and result.get("sha256_verified") is True else None
    if tool_name == "execute_cmd" and not completion_state:
        completion_state = "success" if execute_cmd_result_is_ok(result) else None
    elif tool_name == "write_content" and not completion_state:
        completion_state = "success" if write_content_result_is_ok(result) else None
    elif tool_name in {"app_launch", "app.launch"} and not completion_state:
        completion_state = "success" if status == "launched_verified" else "partial_success"
    elif tool_name in {"app.open_url", "app_open_url"} and not completion_state:
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


def synthesize_device_terminal_report(journal: list[dict], terminal_entry: dict) -> dict:
    """Keep confirmed results from all devices in an ordinary multi-device task."""
    window_steps = [entry for entry in journal if entry.get("tool_type") != "answer"]
    if window_steps and all((entry.get("tool_name") or entry.get("action")) in {"window_control", "window.control"}
                            and tool_result_terminal_sufficient(entry) for entry in window_steps):
        payload = synthesize_terminal_answer_payload(terminal_entry)
        payload["text"] = "\n".join(str(entry.get("result", {}).get("summary") or entry.get("summary") or "") for entry in window_steps)
        payload["basis"] = [entry["step_id"] for entry in window_steps if entry.get("step_id")]
        return payload
    latest = {}
    for entry in journal:
        target = entry.get("target_device_id") or entry.get("device_id")
        if not target or entry.get("tool_type") == "answer":
            continue
        result = entry.get("result") or {}
        if tool_result_terminal_sufficient(entry):
            latest[target] = entry
        elif isinstance(result, dict) and (result.get("error") or entry.get("status") in {"failed", "error", "blocked"}):
            latest.pop(target, None)
    if len(latest) < 2:
        return synthesize_terminal_answer_payload(terminal_entry)
    payloads = [(target, synthesize_terminal_answer_payload(entry)) for target, entry in latest.items()]
    result = synthesize_terminal_answer_payload(terminal_entry)
    result["text"] = "\n".join(f"{target}: {payload['text']}" for target, payload in payloads)
    result["basis"] = list(dict.fromkeys(step for _, payload in payloads for step in payload["basis"]))
    if any(payload["answer_type"] != "grounded_report" for _, payload in payloads):
        result["answer_type"] = "partial_report"
        result["self_check"]["claims_completed_action"] = False
    return result
