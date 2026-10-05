"""Deterministic Browser Bridge device, argument and untrusted-data guards.

The primary LLM chooses web tools. No browser intent classifier is used.
"""
from __future__ import annotations

import re
from typing import Any

WEB_OPERATIONS = frozenset({"web.tabs", "web.read", "web.elements", "web.fill", "web.activate", "web.wait", "web.focus"})
_ALLOWED_ARGUMENTS = {
    "web.tabs": {"device_id"},
    "web.focus": {"device_id", "tab_id"},
    "web.read": {"device_id", "tab_id", "scope", "max_chars", "position"},
    "web.elements": {"device_id", "tab_id", "max_elements", "position"},
    "web.fill": {"device_id", "tab_id", "document_id", "revision", "element_id", "text"},
    "web.activate": {"device_id", "tab_id", "document_id", "revision", "element_id"},
    "web.wait": {"device_id", "tab_id", "document_id", "revision", "timeout_ms"},
}


def _canonical(operation: str) -> str:
    candidate = "web." + operation[4:] if operation.startswith("web_") else operation
    return candidate if candidate in WEB_OPERATIONS else operation


def _normalize(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "")).strip()


def recent_browser_context(history: list[dict[str, Any]] | None, *, device_id: str | None = None) -> list[dict[str, Any]]:
    """Compact identity/draft observations only; page content never becomes authority."""
    rows: list[dict[str, Any]] = []
    for message in reversed(history or []):
        if message.get("role") != "assistant":
            continue
        for command in reversed(message.get("commands") or []):
            operation = _canonical(str(command.get("tool_name") or command.get("action") or ""))
            target = command.get("target_device_id") or command.get("device_id")
            if operation not in WEB_OPERATIONS or (device_id is not None and target != device_id):
                continue
            result = command.get("result")
            if not isinstance(result, dict) or result.get("error") or result.get("status") not in {"success", "ok", "changed", "timeout", "filled", "activated"}:
                continue
            if operation == "web.tabs":
                for tab in result.get("tabs") or []:
                    if not isinstance(tab, dict) or type(tab.get("tab_id")) is not int:
                        continue
                    existing = next((item for item in rows if item["device_id"] == target and item["tab_id"] == tab["tab_id"]), None)
                    if existing is None:
                        rows.append({"device_id":target,"tab_id":tab["tab_id"],"operation":operation})
                    if len(rows) >= 6:
                        return rows
                continue
            page = result.get("page") if isinstance(result.get("page"), dict) else {}
            observed = {**page, **result}
            tab_id = observed.get("tab_id")
            if not isinstance(tab_id, int) or isinstance(tab_id, bool):
                continue
            row: dict[str, Any] = {"device_id": target, "tab_id": tab_id, "operation": operation}
            for key in ("document_id","revision"):
                if isinstance(observed.get(key), str):
                    row[key] = observed[key][:128]
            if operation == "web.focus" and observed.get("focused") is True:
                row["selected"] = True
            # A successful fill supplies the exact draft ID, but never supplies submit intent.
            element = observed.get("element") if isinstance(observed.get("element"), dict) else {}
            draft_id = observed.get("element_id") or element.get("element_id")
            if operation == "web.fill" and isinstance(draft_id, str):
                row["draft_element_id"] = draft_id[:128]
                row["draft_ready"] = True
            if not any(item["device_id"] == target and item["tab_id"] == tab_id for item in rows):
                rows.append(row)
            if len(rows) >= 6:
                return rows
    return rows


def _immediate_browser_context(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    latest = next((item for item in reversed(history or []) if item.get("role") == "assistant"), None)
    return recent_browser_context([latest] if latest else [])


def _immediate_verified_drafts(history: list[dict[str, Any]] | None, device: str | None) -> list[tuple[int,str]]:
    """Preserve already verified fill facts through same-revision observations.

    This does not grant Send intent. Only the latest assistant turn is considered;
    changed/failed observations, native actions and any possible Send invalidate it.
    """
    latest = next((m for m in reversed(history or []) if m.get("role") == "assistant"), {})
    drafts: dict[tuple[str,int],tuple[str,str]] = {}
    for command in latest.get("commands") or []:
        op = _canonical(str(command.get("tool_name") or command.get("action") or ""))
        if op.startswith(("answer.","answer_")):
            continue
        if op not in WEB_OPERATIONS:
            drafts.clear(); continue
        result = command.get("result")
        if not isinstance(result, dict):
            drafts.clear()
            continue
        target = str(command.get("target_device_id") or command.get("device_id") or "")
        tab = result.get("tab_id")
        if op == "web.activate" and result.get("status") in {"success","unknown"}:
            drafts.clear(); continue
        if op in {"web.fill","web.read","web.elements"} and (result.get("error") or result.get("status") != "success"):
            drafts.clear(); continue
        if type(tab) is not int:
            continue
        key = (target.casefold(),tab)
        document,revision = result.get("document_id"), result.get("revision")
        if op == "web.fill":
            element = result.get("element") if isinstance(result.get("element"), dict) else {}
            element_id = result.get("element_id") or element.get("element_id")
            if all(isinstance(value, str) and 0 < len(value) <= 128
                   for value in (document, revision, element_id)):
                drafts[key] = (document, revision)
            else:
                drafts.pop(key, None)
        elif op in {"web.read","web.elements","web.wait"} and key in drafts and drafts[key] != (document,revision):
            drafts.pop(key,None)
    return [(tab,document) for (owner,tab),(document,revision) in drafts.items() if owner == str(device or "").casefold()]


def _active_browser_context(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Carry verified metadata across browser-only refusal/clarification turns.

    Neither answer prose nor page text grants capabilities. An intervening native
    action, unrelated answer or new device scope ends this continuation.
    """
    assistants = [item for item in history or [] if item.get("role") == "assistant"]
    for message in reversed(assistants[-3:]):
        commands = message.get("commands") or []
        operations = [_canonical(str(c.get("tool_name") or c.get("action") or "")) for c in commands]
        if any(op not in WEB_OPERATIONS and not op.startswith(("answer.","answer_")) for op in operations):
            break
        rows = recent_browser_context([message])
        if rows:
            return rows
        clarification = any(op in {"answer.ask_clarification", "answer_ask_clarification"} for op in operations)
        browser_attempt = any(op in WEB_OPERATIONS for op in operations)
        if not commands or not (clarification or browser_attempt):
            break
    return []


def browser_request(message: str, history: list[dict[str, Any]] | None = None) -> bool:
    """Compatibility route hint: language never restricts access to web tools.

    The LLM chooses a capability from the request and conversation, not a keyword router.
    A new turn starts with the ordinary tool inventory; prior page text cannot force a route.
    """
    return False


ordinary_browser_request = browser_request


class BrowserTaskPolicy:
    """Deterministic device/data boundary, independent of the phrasing of the request.

    Runtime and the static extension validate effects, identities and confirmations.
    Page or planner content never overrides server ownership and argument guards.
    """
    def __init__(self, message: str, current_device: str | None, history=None,
                 *, authorized_device_ids: set[str] | list[str] | None = None):
        self.message = str(message or "")
        self.current_device = current_device
        self.context = recent_browser_context(history, device_id=current_device)
        self.is_browser_task = False
        self.contextual_task = False
        self.browser_only = False
        self.page_data_seen = False
        self.external_action = False
        self.draft_action = False
        self.bare_send = False
        self.single_mutation_completion = False
        self.tabs_only = False
        self.focus_only = False
        self.focus_action = False
        self.navigation_action = False
        self.literal_payload = None
        self.draft_targets = _immediate_verified_drafts(history, current_device)
        self.authorized_device_ids = set(authorized_device_ids) if authorized_device_ids is not None else None
        self.allowed_operations = set(WEB_OPERATIONS)

    def mark_page_data(self) -> None:
        self.page_data_seen = True

    def allows(self, operation: str, device_id: str | None = None, params: dict[str, Any] | None = None) -> tuple[bool, str]:
        operation = _canonical(str(operation or ""))
        if operation.startswith("answer.") or operation.startswith("answer_"):
            return True, ""
        if operation not in WEB_OPERATIONS:
            if self.browser_only or self.page_data_seen:
                return False, "untrusted_page_data_cannot_authorize_privileged_tool"
            return True, ""
        # Tool selection is semantic; it establishes the current capability scope.
        self.is_browser_task = self.contextual_task = self.browser_only = True
        target = device_id or (params or {}).get("device_id") or self.current_device
        if not target or (self.authorized_device_ids is not None and str(target).casefold() not in {str(identifier).casefold() for identifier in self.authorized_device_ids}):
            return False, "browser_device_not_authorized_by_user"
        if "device_id" in (params or {}) and params["device_id"] != target:
            return False, "browser_device_scope_mismatch"
        try:
            validate_browser_arguments(operation, params or {})
        except ValueError as exc:
            return False, str(exc)
        return True, ""

    def allow_tool(self, tool_name: str, target_device_id: str | None = None, params: dict[str, Any] | None = None,
                   *, page_data_seen: bool = False) -> bool:
        if page_data_seen:
            self.mark_page_data()
        return self.allows(tool_name, target_device_id, params)[0]


def validate_browser_arguments(operation: str, args: dict[str, Any]) -> dict[str, Any]:
    """Strict runtime boundary, including callers that skip JSON-schema validation."""
    operation = _canonical(str(operation or ""))
    if operation not in WEB_OPERATIONS:
        raise ValueError("unsupported_browser_operation")
    if not isinstance(args, dict):
        raise ValueError("invalid_browser_arguments")
    if set(args) - _ALLOWED_ARGUMENTS[operation]:
        raise ValueError("unknown_browser_arguments")
    clean = dict(args)
    if "device_id" in clean and (not isinstance(clean["device_id"], str) or not 1 <= len(clean["device_id"]) <= 128):
        raise ValueError("invalid_browser_device_id")
    if operation != "web.tabs":
        value = clean.get("tab_id")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("invalid_browser_tab_id")
    if operation in {"web.fill", "web.activate", "web.wait"}:
        for key in ("document_id", "revision"):
            if not isinstance(clean.get(key), str) or not 1 <= len(clean[key]) <= 128:
                raise ValueError("invalid_browser_" + key)
    if operation in {"web.fill", "web.activate"}:
        if not isinstance(clean.get("element_id"), str) or not 1 <= len(clean["element_id"]) <= 128:
            raise ValueError("invalid_browser_element_id")
    if operation == "web.fill" and (not isinstance(clean.get("text"), str) or len(clean["text"]) > 20000):
        raise ValueError("invalid_browser_text")
    if operation == "web.read" and clean.get("scope", "main") not in {"main", "page"}:
        raise ValueError("invalid_browser_scope")
    if operation in {"web.read", "web.elements"} and clean.get("position", "tail") not in {"head", "tail"}:
        raise ValueError("invalid_browser_position")
    for key, maximum in (("max_chars", 24000), ("max_elements", 200), ("timeout_ms", 15000)):
        if key in clean and (not isinstance(clean[key], int) or isinstance(clean[key], bool) or not 1 <= clean[key] <= maximum):
            raise ValueError("invalid_browser_" + key)
    return clean


def silent_browser_success(task: dict[str, Any]) -> bool:
    if task.get("status") not in {"done", "completed", "completed_with_recovery"} or task.get("plan_suggestion"):
        return False
    commands = [command for command in task.get("commands", [])
                if command.get("tool_type") != "answer"
                and not str(command.get("tool_name") or command.get("action") or "").startswith(("answer.", "answer_"))]
    if not commands:
        return False
    last_action_index = -1
    for index, command in enumerate(commands):
        operation = _canonical(str(command.get("tool_name") or command.get("action") or ""))
        result = command.get("result") or {}
        if operation not in WEB_OPERATIONS or result.get("error") or result.get("status") not in {"success", "ok", "changed", "filled", "activated"}:
            return False
        if operation in {"web.fill", "web.activate", "web.wait", "web.focus"}:
            if result.get("response_policy") not in {"silent", "silent_on_success"}:
                return False
            last_action_index = index
    if last_action_index < 0:
        return False  # A read/list answer must remain audible.
    # Observations used to select a field/button are silent supporting work. Reading
    # after the action is the user's requested result and must remain audible.
    return all(_canonical(str(command.get("tool_name") or command.get("action") or "")) == "web.wait"
               for command in commands[last_action_index + 1:])



def browser_answer_ready(message: str, journal: list[dict[str, Any]]) -> bool:
    # Only the model can decide whether a multi-part human request is complete.
    # Equivalent reads are bounded by the existing repeat/answer-phase guards.
    return False


def browser_tabs_report(result: dict[str, Any]) -> str:
    tabs = result.get("tabs") or []
    label = "Показаны первые" if result.get("truncated") else "Открыто вкладок:"
    lines = [f"{label} {len(tabs)}."]
    for index, tab in enumerate(tabs, 1):
        title = re.sub(r"\s+", " ", str(tab.get("title") or "Без названия"))[:160]
        origin = str(tab.get("origin") or "")[:250]
        lines.append(f"{index}. {title}" + (f" — {origin}" if origin else "")
                     + (" (активная)" if tab.get("active") else ""))
    return "\n".join(lines)



def browser_partial_read(journal: list[dict[str, Any]]) -> dict[str, Any]:
    observed = next(entry for entry in reversed(journal) if _canonical(str(entry.get("tool_name") or entry.get("action") or "")) == "web.read" and entry.get("result", {}).get("status") == "success")
    result = observed["result"]
    content = result.get("text", result.get("content", ""))
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    text = "Страница прочитана, но выделить запрошенный ответ не удалось. Полученный фрагмент:\n" + str(content)[-1600:]
    return {"answer_type":"partial_report","text":text,"basis":[observed["step_id"]],
            "self_check":{"depends_on_current_external_state":True,"claims_completed_action":False,
                          "has_sufficient_evidence":True,"missing_evidence_question":"Не удалось выделить запрошенное сообщение"}}



def browser_failure_text(result: dict) -> str:
    """Keep protocol detail in the operation journal, not in spoken UI text."""
    reason = result.get("error")
    if result.get("status") == "unknown" or reason in {"needs_verification","browser_action_unknown","prior_browser_action_needs_verification"}:
        return "Выполнение отправки пока не подтверждено. Повторно отправлять сообщение не буду, чтобы не создать дубль."
    if reason == "browser_confirmation_expired":
        return "Время подтверждения истекло. Действие в браузере не выполнено."
    if reason == "browser_confirmation_declined":
        return "Действие в браузере не выполнено: подтверждение не получено."
    if reason == "browser_offline":
        return "На выбранном устройстве нет активного соединения Browser Bridge с сервером ИРУ. Проверьте браузер в Настройках."
    if reason in {"invalid_browser_credential", "pairing_required"}:
        return "Привязку браузера нужно обновить в расширении Browser Bridge."
    if reason in {"tab_disconnected","browser_disconnected","browser_not_connected","browser_timeout"}:
        return "Не удалось прочитать вкладку: связь с браузером временно недоступна."
    if reason in {"stale_element","browser_tab_mismatch"}:
        return "Страница изменилась. Нужно заново проверить выбранную вкладку."
    return "Не удалось выполнить действие в браузере. Подробности есть в ходе выполнения."
