from __future__ import annotations

from typing import Any


TERMINAL_CORRECTION = (
    "You have sufficient current-run evidence. Call answer_text now. "
    "Do not call more tools unless the user explicitly requested deeper verification. "
    "For app.open_url opened_unverified with launched=true and no launch_error, answer as a user-facing success: "
    "'Готово, открыл сайт в браузере.' Do not claim the site is unavailable just because the exact tab/window was not verified."
)

_OPEN_URL_SCARY_PHRASES = (
    "сайт недоступен",
    "возможно сайт недоступен",
    "не удалось открыть",
    "окно с сайтом не обнаружено",
)


def tool_result_terminal_sufficient(entry: dict[str, Any] | None) -> bool:
    result = (entry or {}).get("result")
    if not isinstance(result, dict):
        return False
    if result.get("terminal_sufficient"):
        return True
    tool_name = (entry or {}).get("tool_name") or (entry or {}).get("action")
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

    if tool_name == "app.open_url":
        url = str(result.get("url") or "").strip()
        launched = bool(result.get("launched"))
        launch_error = str(result.get("launch_error") or result.get("error") or "").strip()
        if not launched or launch_error or status == "failed":
            text = "Не удалось открыть сайт в браузере."
            if url:
                text = f"Не удалось открыть {url} в браузере."
            if launch_error:
                text = f"{text} Причина: {launch_error}"
        elif status == "opened_visible_focus_failed":
            text = "Ссылка открыта, окно найдено, но сфокусировать окно не удалось."
        elif status in {"opened_unverified", "opened_browser_visible"}:
            text = "Готово, открыл сайт в браузере."
            if url:
                text = f"Готово, открыл {url} в браузере."
        else:
            text = "Ссылка открыта."
        if url:
            if status not in {"opened_unverified", "opened_browser_visible"} and "браузере" not in text:
                text = f"{text} URL: {url}"
    else:
        text = str(result.get("summary") or entry.get("summary") or "Действие выполнено.")

    completion_state = result.get("completion_state")
    if tool_name == "app.open_url" and not completion_state:
        if status == "opened_verified":
            completion_state = "success"
        elif result.get("launch_error") or not result.get("launched") or status == "failed":
            completion_state = "failed"
        else:
            completion_state = "partial_success"
    answer_type = "grounded_report" if completion_state == "success" else "partial_report"
    if completion_state == "failed":
        answer_type = "error_report"
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


def sanitize_open_url_answer_payload(answer_payload: dict[str, Any], journal: list[dict[str, Any]]) -> dict[str, Any]:
    basis = [str(item) for item in answer_payload.get("basis") or []]
    if not basis:
        return answer_payload

    basis_steps = {
        str(entry.get("step_id")): entry
        for entry in journal or []
        if entry.get("step_id")
    }
    for step_id in basis:
        entry = basis_steps.get(step_id)
        result = entry.get("result") if isinstance(entry, dict) else None
        if not isinstance(result, dict):
            continue
        if (entry.get("tool_name") or entry.get("action")) not in {"app.open_url", "app_open_url"}:
            continue
        status = str(result.get("status") or "")
        if status not in {"opened_unverified", "opened_browser_visible"}:
            continue
        if not result.get("launched") or result.get("launch_error"):
            continue
        text = str(answer_payload.get("text") or "")
        if any(phrase in text.lower() for phrase in _OPEN_URL_SCARY_PHRASES):
            sanitized = dict(answer_payload)
            url = str(result.get("url") or "").strip()
            sanitized["text"] = f"Готово, открыл {url} в браузере." if url else "Готово, открыл сайт в браузере."
            return sanitized
    return answer_payload
