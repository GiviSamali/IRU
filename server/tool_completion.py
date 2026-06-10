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


_TOOL_NAME_ALIASES = {
    "app_open_url": "app.open_url",
    "app_open_file": "app.open_file",
    "fs_resolve_path": "fs.resolve_path",
    "fs_open_folder": "fs.open_folder",
    "fs_list_dir": "fs.list_dir",
    "fs_stat": "fs.stat",
    "fs_read_file": "fs.read_file",
    "fs_write_file": "fs.write_file",
    "fs_patch_file": "fs.patch_file",
    "fs_rename": "fs.rename",
    "fs_copy": "fs.copy",
    "fs_move": "fs.move",
    "fs_delete": "fs.delete",
    "window_find": "window.find",
    "window_verify": "window.verify",
}

_TERMINAL_FS_STATUS_BY_TOOL = {
    "fs.write_file": {"written", "appended"},
    "fs.patch_file": {"patched"},
    "fs.rename": {"renamed"},
    "fs.copy": {"copied"},
    "fs.move": {"moved"},
    "fs.delete": {"deleted"},
}


def _canonical_tool_name(name: Any) -> str:
    value = str(name or "")
    return _TOOL_NAME_ALIASES.get(value, value)


def _display_path(result: dict[str, Any]) -> str:
    return str(
        result.get("resolved_path")
        or result.get("path")
        or result.get("new_path")
        or result.get("destination")
        or ""
    ).strip()


def tool_result_terminal_sufficient(entry: dict[str, Any] | None) -> bool:
    result = (entry or {}).get("result")
    if not isinstance(result, dict):
        return False
    if result.get("terminal_sufficient"):
        return True
    tool_name = _canonical_tool_name((entry or {}).get("tool_name") or (entry or {}).get("action"))
    if tool_name in {"app.open_url", "app_open_url"}:
        return bool(result.get("launched")) and str(result.get("status") or "") in {
            "opened_verified",
            "opened_visible_focus_failed",
            "opened_unverified",
            "opened_browser_visible",
        }
    if tool_name in {"fs.open_folder", "app.open_file"}:
        return str(result.get("status") or "") in {"opened", "opened_unverified", "found_existing"} and bool(
            result.get("window_found")
            or result.get("process_started")
            or result.get("terminal_sufficient")
        )
    if tool_name in _TERMINAL_FS_STATUS_BY_TOOL:
        return str(result.get("status") or "") in _TERMINAL_FS_STATUS_BY_TOOL[tool_name]
    if tool_name in {"window.find", "window.verify"}:
        status = str(result.get("status") or "").lower()
        window = result.get("window") or result.get("match") or {}
        visible = result.get("window_visible")
        if visible is None and isinstance(window, dict):
            visible = window.get("visible")
        return status in {"found", "verified", "success", "ok"} and visible is not False
    return False


def synthesize_terminal_answer_payload(entry: dict[str, Any]) -> dict[str, Any]:
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    tool_name = _canonical_tool_name(entry.get("tool_name") or entry.get("action") or "tool")
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
    elif tool_name == "fs.open_folder":
        path = _display_path(result)
        if result.get("window_found"):
            text = f"Готово, открыл папку: {path}." if path else "Готово, открыл папку."
        elif status == "failed" or result.get("launch_error"):
            text = "Не удалось открыть папку."
            if path:
                text = f"Не удалось открыть папку: {path}."
            if result.get("launch_error"):
                text = f"{text} Причина: {result.get('launch_error')}"
        else:
            text = f"Готово, отправил команду открыть папку: {path}." if path else "Готово, отправил команду открыть папку."
    elif tool_name == "app.open_file":
        path = _display_path(result)
        if result.get("window_found"):
            text = f"Готово, открыл файл: {path}." if path else "Готово, открыл файл."
        elif status == "failed" or result.get("launch_error"):
            text = "Не удалось открыть файл."
            if path:
                text = f"Не удалось открыть файл: {path}."
            if result.get("launch_error"):
                text = f"{text} Причина: {result.get('launch_error')}"
        else:
            text = f"Готово, отправил команду открыть файл: {path}." if path else "Готово, отправил команду открыть файл."
    elif tool_name in {"fs.write_file", "fs.patch_file"}:
        path = _display_path(result)
        if status == "appended":
            verb = "добавил текст в"
        elif tool_name == "fs.patch_file" or status == "patched":
            verb = "обновил"
        else:
            verb = "создал"
        text = f"Готово, {verb} файл: {path}." if path else "Готово, файл обновлен."
    elif tool_name in {"fs.rename", "fs.copy", "fs.move"}:
        source = str(result.get("old_path") or result.get("source") or "").strip()
        destination = str(result.get("new_path") or result.get("destination") or "").strip()
        action = {"fs.rename": "переименовал", "fs.copy": "скопировал", "fs.move": "переместил"}[tool_name]
        if source and destination:
            text = f"Готово, {action}: {source} -> {destination}."
        else:
            text = f"Готово, {action} файл или папку."
    elif tool_name == "fs.delete":
        path = _display_path(result)
        if status == "needs_confirmation":
            text = f"Нужно подтверждение перед удалением: {path}." if path else "Нужно подтверждение перед удалением."
        else:
            text = f"Готово, удалил: {path}." if path else "Готово, удалил файл или папку."
    elif tool_name in {"fs.resolve_path", "fs.stat", "fs.list_dir", "fs.read_file"}:
        text = str(result.get("summary") or entry.get("summary") or "Данные по файлам получены.")
    elif tool_name in {"window.find", "window.verify"}:
        window = result.get("window") or result.get("match") or {}
        title = str(window.get("title") or result.get("window_title") or "").strip()
        if tool_result_terminal_sufficient(entry):
            text = f"Готово, окно найдено: {title}." if title else "Готово, окно найдено."
        else:
            text = "Окно не найдено или не подтверждено."
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
    if not completion_state:
        if tool_result_terminal_sufficient(entry):
            completion_state = "success"
        elif status in {"failed", "error"} or result.get("error"):
            completion_state = "failed"
        elif status == "needs_confirmation":
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
        if _canonical_tool_name(entry.get("tool_name") or entry.get("action")) != "app.open_url":
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
