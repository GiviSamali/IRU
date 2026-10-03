"""Small Browser Bridge v1 intent boundary derived only from the human request.

This is deliberately conservative. Page observations and planner text never grant
capabilities, device scope, or permission to submit a draft.
"""
from __future__ import annotations

import re
from typing import Any

WEB_OPERATIONS = frozenset({"web.tabs", "web.read", "web.elements", "web.fill", "web.activate", "web.wait"})
_BROWSER_NOUNS = re.compile(r"(?:браузер|вкладк|веб[- ]|web\b|browser\b|tab\b|страниц|\bчат(?:е|а|у|ы|ов|ом)?\b|поле сообщения|композер|ссылк)", re.I)
_READ_START = re.compile(r"^(?:прочитай|читай|покажи|перечисли|найди|что|какие|read\b|list\b|show\b)", re.I)
_DRAFT_START = re.compile(r"^(?:напиши|впиши|заполни|вставь|набери|подготовь(?: текст| сообщение)?|write\b|fill\b|draft\b)", re.I)
_SEND_START = re.compile(r"^(?:отправь|отправляй|пошли|спроси(?! меня\b)|скажи (?:ему|ей|им)|нажми|активируй|send\b|submit\b|ask (?:him|her|them)\b|tell (?:him|her|them)\b|activate\b)", re.I)
_WAIT_START = re.compile(r"^(?:дождись|подожди|жди|wait\b)", re.I)
_OPEN_START = re.compile(r"^(?:открой|перейди|open\b|navigate\b|follow\b)", re.I)
_PRIVILEGED_CLAUSE = re.compile(r"(?:execute_cmd|transfer_file|передай.{0,40}файл|сохрани.{0,40}файл|создай.{0,40}файл|удали|скачай|загрузи|установи|python|powershell)", re.I)
_ALLOWED_ARGUMENTS = {
    "web.tabs": {"device_id"},
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
    text = re.sub(r"\s+", " ", str(message or "")).strip().rstrip(".!?")
    return re.sub(r"^(?:(?:пожалуйста|а теперь|теперь|хорошо|отлично)[, ]+)+", "", text, flags=re.I)


def _intent_text(message: str) -> str:
    # Prefix device routing is human input; draft payload is otherwise opaque.
    text = _normalize(message)
    return re.sub(r"^на (?:(?:устройстве|пк|компьютере) )?(?:первом пк|втором пк|[\w-]+)[, ]+", "", text, flags=re.I)


_NON_DEVICE_LOCATIONS = frozenset({
    "странице", "страницу", "страницах", "вкладке", "вкладку", "вкладках", "сайте", "сайтах",
    "экране", "экран", "форме", "кнопке", "этом", "этой", "этих", "текущей", "главной",
    "рабочем", "столе", "завтра", "сегодня", "русском", "английском", "page", "tab", "screen",
})


def _device_mentions(message: str) -> tuple[set[str], bool]:
    """Read human device clauses, never colon-delimited draft/message payload."""
    raw = _normalize(message)
    intent = _intent_text(raw)
    if (_DRAFT_START.match(intent) or _SEND_START.match(intent)) and ":" in raw:
        raw = raw.split(":", 1)[0]
    names: set[str] = set()
    alias = False
    for found in re.finditer(r"\bна (?:(?:устройстве|пк|компьютере) )?([\w-]+)", raw, re.I):
        name = found.group(1)
        folded = name.casefold()
        if folded in {"первом", "втором", "первый", "второй"}:
            alias = True
        elif folded not in _NON_DEVICE_LOCATIONS:
            names.add(name)
    return names, alias


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
            page = result.get("page") if isinstance(result.get("page"), dict) else {}
            observed = {**page, **result}
            tab_id = observed.get("tab_id")
            if not isinstance(tab_id, int) or isinstance(tab_id, bool):
                continue
            row: dict[str, Any] = {"device_id": target, "tab_id": tab_id, "operation": operation}
            for key in ("document_id", "revision"):
                if isinstance(observed.get(key), str):
                    row[key] = observed[key][:128]
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


def browser_request(message: str, history: list[dict[str, Any]] | None = None) -> bool:
    try:
        from .window_policy import ordinary_window_request
    except ImportError:
        from window_policy import ordinary_window_request
    if ordinary_window_request(message):
        return False
    text = _intent_text(message)
    context = _immediate_browser_context(history)
    authority_text = text.split(":", 1)[0] if (_DRAFT_START.match(text) or _SEND_START.match(text)) and ":" in text else text
    if _OPEN_START.match(text):
        # Starting a browser/opening an arbitrary URL remains app.launch/open_url.
        if re.search(r"https?://", text, re.I) or re.match(r"^(?:открой|open) (?:браузер|chrome|edge|comet)\b", text, re.I):
            return False
        page_reference = re.search(r"(?:эту|этот|текущую|выбранную) (?:ссылку|вкладку)|ссылку на (?:этой|текущей) странице", text, re.I)
        return bool(page_reference or context)
    if _BROWSER_NOUNS.search(authority_text):
        return bool(_READ_START.match(text) or _DRAFT_START.match(text) or _SEND_START.match(text)
                    or _WAIT_START.match(text) or _OPEN_START.match(text))
    # Follow-up draft/send/wait has meaning only after an observed Browser Bridge turn.
    return bool(context and (_READ_START.match(text) or _DRAFT_START.match(text)
                             or _SEND_START.match(text) or _WAIT_START.match(text) or _OPEN_START.match(text)))


# Alias retained for callers that parallel the ordinary-window classifier.
ordinary_browser_request = browser_request


class BrowserTaskPolicy:
    """Original-user-intent policy; never construct this from a PLAN step or web result."""

    def __init__(self, message: str, current_device: str | None, history: list[dict[str, Any]] | None = None,
                 *, authorized_device_ids: set[str] | list[str] | None = None):
        self.message = str(message or "")
        self.current_device = current_device
        self.context = [row for row in _immediate_browser_context(history)
                        if str(row.get("device_id") or "").casefold() == str(current_device or "").casefold()]
        text = _intent_text(self.message)
        self.is_browser_task = browser_request(self.message, history)
        # Do not inspect quoted/payload draft text for new verbs or local-tool requests.
        self.draft_action = bool(self.is_browser_task and _DRAFT_START.match(text))
        self.external_action = bool(self.is_browser_task and _SEND_START.match(text))
        self.bare_send = bool(re.fullmatch(r"(?:отправь|отправляй|пошли|send|submit)(?: на [\w-]+)?", text, re.I))
        self.draft_targets = [(row["tab_id"], row["document_id"]) for row in self.context
                              if row.get("draft_ready") and row.get("document_id") and row.get("revision")]
        self.draft_targets = list(dict.fromkeys(self.draft_targets))
        if self.bare_send:
            # A follow-up references exactly one immediately observed draft. It never
            # grants permission to pick another tab or alter the draft before sending.
            self.external_action = self.external_action and len(self.draft_targets) == 1
        intent_prefix = text.split(":", 1)[0]
        compound = re.search(r"\b(?:дождись|подожди|прочитай|перечисли|wait|read)\b", intent_prefix, re.I)
        simple_draft = bool(self.draft_action and ":" in text
                            and re.match(r"^(?:напиши|впиши|вставь|набери|write|draft)\b", intent_prefix, re.I))
        self.single_mutation_completion = bool(
            self.is_browser_task and not compound
            and (self.bare_send and self.external_action or simple_draft
                 or self.external_action and ":" in text))
        # A colon-delimited message supplied by the human is literal payload. DOM
        # instructions may not replace it with a different message before activation.
        literal_draft = self.draft_action and re.match(r"^(?:напиши|впиши|вставь|набери|write|draft)\b", intent_prefix, re.I)
        self.literal_payload = (self.message.split(":", 1)[1].strip()
                                if ":" in self.message and (literal_draft or self.external_action) else None)
        self.navigation_action = bool(self.is_browser_task and _OPEN_START.match(text))
        self.browser_only = self.is_browser_task and (self.draft_action or not _PRIVILEGED_CLAUSE.search(intent_prefix))
        self.page_data_seen = False
        self.authorized_device_ids = set(authorized_device_ids or [])
        self.named_devices, self.unresolved_device_alias = _device_mentions(self.message)
        if self.named_devices:
            # Exact Unicode names are fail-closed; only case spelling is normalized.
            self.authorized_device_ids = set(self.named_devices)
        elif self.unresolved_device_alias:
            # Only an existing authoritative router may supply an alias resolution.
            # Without that scope the worker must clarify instead of acting on current.
            self.authorized_device_ids = set(authorized_device_ids or [])
        elif current_device:
            self.authorized_device_ids.add(current_device)
        self.allowed_operations = set(WEB_OPERATIONS - {"web.fill", "web.activate"}) if self.is_browser_task else set()
        if self.draft_action or self.external_action and not self.bare_send:
            self.allowed_operations.add("web.fill")
        if self.external_action or self.navigation_action:
            self.allowed_operations.add("web.activate")

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
        if not self.is_browser_task:
            return False, "explicit_browser_task_required"
        target = device_id or (params or {}).get("device_id") or self.current_device
        if not target or str(target).casefold() not in {str(identifier).casefold() for identifier in self.authorized_device_ids}:
            return False, "browser_device_not_authorized_by_user"
        try:
            validate_browser_arguments(operation, params or {})
        except ValueError as exc:
            return False, str(exc)
        if operation == "web.fill" and operation not in self.allowed_operations:
            return False, "explicit_draft_intent_required"
        if operation == "web.fill" and self.literal_payload is not None and params.get("text") != self.literal_payload:
            return False, "browser_literal_message_mismatch"
        if operation == "web.activate" and operation not in self.allowed_operations:
            return False, "ambiguous_or_missing_browser_draft" if self.bare_send else "explicit_external_action_intent_required"
        if operation == "web.activate" and self.bare_send:
            if str(target).casefold() != str(self.current_device or "").casefold() or (params.get("tab_id"), params.get("document_id")) not in self.draft_targets:
                return False, "browser_draft_target_mismatch"
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
        if operation in {"web.fill", "web.activate", "web.wait"}:
            if result.get("response_policy") not in {"silent", "silent_on_success"}:
                return False
            last_action_index = index
    if last_action_index < 0:
        return False  # A read/list answer must remain audible.
    # Observations used to select a field/button are silent supporting work. Reading
    # after the action is the user's requested result and must remain audible.
    return all(_canonical(str(command.get("tool_name") or command.get("action") or "")) == "web.wait"
               for command in commands[last_action_index + 1:])
