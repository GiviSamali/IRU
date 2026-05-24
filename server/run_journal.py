from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from .tool_registry import canonical_tool_name, compact_tool_summary, tool_log_fields
except ImportError:
    from tool_registry import canonical_tool_name, compact_tool_summary, tool_log_fields  # type: ignore


ANSWER_TOOL_NAMES = {
    "answer_text",
    "answer_ask_clarification",
    "answer_report_failure",
    "answer_request_confirmation",
    "answer.text",
    "answer.ask_clarification",
    "answer.report_failure",
    "answer.request_confirmation",
}

ANSWER_TEXT_NAMES = {"answer_text", "answer.text"}
ANSWER_CLARIFICATION_NAMES = {"answer_ask_clarification", "answer.ask_clarification"}
ANSWER_FAILURE_NAMES = {"answer_report_failure", "answer.report_failure"}
ANSWER_CONFIRMATION_NAMES = {"answer_request_confirmation", "answer.request_confirmation"}

RAW_CONTENT_CORRECTION = "Raw assistant content is not allowed. Use answer_text for any user-facing response."
ONE_TOOL_CORRECTION = "Call exactly one tool per iteration. Wait for its result, then choose the next tool."
GROUNDED_CORRECTION = (
    "Your answer was not grounded in current-run evidence. Ask yourself what tool is needed, "
    "call exactly one tool, wait for result, then answer through answer_text."
)
INSUFFICIENT_EVIDENCE_CORRECTION = (
    "Your answer_text says evidence is insufficient. Call the needed tool first or use clarification/failure."
)


class ProtocolValidationError(ValueError):
    def __init__(self, message: str, correction: str | None = None):
        super().__init__(message)
        self.message = message
        self.correction = correction or message


def is_terminal_answer_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in ANSWER_TOOL_NAMES or canonical_tool_name(tool_name) in ANSWER_TOOL_NAMES


def is_answer_text_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in ANSWER_TEXT_NAMES or canonical_tool_name(tool_name) in ANSWER_TEXT_NAMES


def is_answer_clarification_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in ANSWER_CLARIFICATION_NAMES or canonical_tool_name(tool_name) in ANSWER_CLARIFICATION_NAMES


def is_answer_failure_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in ANSWER_FAILURE_NAMES or canonical_tool_name(tool_name) in ANSWER_FAILURE_NAMES


def is_answer_confirmation_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name in ANSWER_CONFIRMATION_NAMES or canonical_tool_name(tool_name) in ANSWER_CONFIRMATION_NAMES


def _next_idx(journal: list[dict[str, Any]]) -> int:
    existing = [entry.get("idx") for entry in journal if isinstance(entry.get("idx"), int)]
    return (max(existing) + 1) if existing else 1


def _status_for_result(result: Any, terminal: bool = False) -> str:
    if terminal:
        return "terminal"
    if isinstance(result, dict):
        if result.get("error"):
            return "failed"
        if result.get("status") in {"failed", "error"}:
            return "failed"
        if result.get("status") == "skipped":
            return "skipped"
    return "success"


def make_run_step(
    *,
    journal: list[dict[str, Any]],
    tool_name: str,
    result: Any = None,
    command: str = "",
    target_device_id: str | None = None,
    hostname: str | None = None,
    iteration: int | None = None,
    tool_type: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    step_index: int | None = None,
    step_title: str | None = None,
) -> dict[str, Any]:
    idx = _next_idx(journal)
    canonical = canonical_tool_name(tool_name)
    terminal = is_terminal_answer_tool(tool_name)
    final_status = status or _status_for_result(result, terminal=terminal)
    final_summary = summary or compact_step_summary(tool_name, result, command)
    fields = tool_log_fields(tool_name, result, command, target_device_id)
    entry = {
        "action": tool_name,
        "command": command or f"[tool] {canonical}",
        "device_id": target_device_id,
        "target_device_id": target_device_id,
        "hostname": hostname or target_device_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "step_id": f"step_{idx}",
        "idx": idx,
        "tool_name": canonical,
        "tool_type": tool_type or fields.get("tool_type") or ("answer" if terminal else "typed"),
        "tool_status": "terminal" if terminal else final_status,
        "status": "terminal" if terminal else final_status,
        "summary": final_summary,
    }
    if iteration is not None:
        entry["iteration"] = iteration
    if step_index is not None:
        entry["step_index"] = step_index
    if step_title is not None:
        entry["step_title"] = step_title
    for key, value in fields.items():
        entry.setdefault(key, value)
    return entry


def append_tool_step(journal: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("step_id") and entry.get("idx") is not None:
        journal.append(entry)
        return entry
    idx = _next_idx(journal)
    action = entry.get("action") or entry.get("tool_name") or ""
    canonical = canonical_tool_name(action)
    result = entry.get("result")
    command = entry.get("command") or f"[tool] {canonical}"
    status = entry.get("status") or _status_for_result(result)
    fields = tool_log_fields(action, result, command, entry.get("target_device_id") or entry.get("device_id"))
    entry["step_id"] = f"step_{idx}"
    entry["idx"] = idx
    entry["created_at"] = entry.get("created_at") or datetime.now(timezone.utc).isoformat()
    entry["collected_at"] = entry.get("collected_at") or entry["created_at"]
    entry["tool_name"] = fields.get("tool_name") or canonical
    entry["tool_type"] = fields.get("tool_type") or entry.get("tool_type") or "typed"
    entry["tool_status"] = fields.get("tool_status") or entry.get("tool_status") or status
    entry["status"] = status
    entry["summary"] = entry.get("summary") or fields.get("summary") or compact_step_summary(action, result, command)
    if "target_device_id" not in entry:
        entry["target_device_id"] = entry.get("device_id")
    journal.append(entry)
    return entry


def append_answer_step(
    journal: list[dict[str, Any]],
    tool_name: str,
    payload: dict[str, Any],
    *,
    target_device_id: str | None = None,
    hostname: str | None = None,
    iteration: int | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    answer_type = payload.get("answer_type") or payload.get("kind") or "terminal"
    entry = make_run_step(
        journal=journal,
        tool_name=tool_name,
        result=payload,
        command=command or f"[tool] {canonical_tool_name(tool_name)}",
        target_device_id=target_device_id,
        hostname=hostname,
        iteration=iteration,
        tool_type="answer",
        status="terminal",
        summary=f"answer_type={answer_type}",
    )
    journal.append(entry)
    return entry


def compact_step_summary(action: str, result: Any = None, command: str = "") -> str:
    canonical = canonical_tool_name(action)
    if canonical == "answer.text" and isinstance(result, dict):
        return f"answer_type={result.get('answer_type', 'unknown')}"
    if canonical == "answer.ask_clarification":
        return "clarification requested"
    if canonical == "answer.report_failure":
        return "failure reported"
    if canonical == "answer.request_confirmation":
        return "confirmation requested"
    return compact_tool_summary(action, result, command)


def wrap_tool_result_for_llm(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": entry.get("step_id"),
        "tool_name": entry.get("tool_name") or canonical_tool_name(entry.get("action", "")),
        "status": entry.get("status") or entry.get("tool_status"),
        "summary": entry.get("summary") or "",
        "result": entry.get("result"),
    }


def validate_tool_call_batch(tool_calls: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not tool_calls:
        raise ProtocolValidationError("missing tool call", RAW_CONTENT_CORRECTION)
    if len(tool_calls) != 1:
        raise ProtocolValidationError("exactly one tool call is required", ONE_TOOL_CORRECTION)
    tool_call = tool_calls[0]
    fn_name = ((tool_call.get("function") or {}).get("name") or "").strip()
    if not fn_name:
        raise ProtocolValidationError("tool call is missing function name", ONE_TOOL_CORRECTION)
    return tool_call


def _current_non_answer_step_ids(journal: list[dict[str, Any]]) -> set[str]:
    return {
        str(entry.get("step_id"))
        for entry in journal
        if entry.get("step_id") and entry.get("tool_type") != "answer" and not is_terminal_answer_tool(entry.get("tool_name"))
    }


def _has_failed_non_answer_step(journal: list[dict[str, Any]]) -> bool:
    return any(
        entry.get("step_id")
        and entry.get("tool_type") != "answer"
        and not is_terminal_answer_tool(entry.get("tool_name"))
        and entry.get("status") in {"failed", "error", "blocked"}
        for entry in journal
    )


def validate_basis_references(
    basis: Any,
    journal: list[dict[str, Any]],
    *,
    require_non_empty: bool,
) -> list[str]:
    if not isinstance(basis, list) or not all(isinstance(item, str) and item.strip() for item in basis):
        raise ProtocolValidationError("basis must be an array of step_id strings", GROUNDED_CORRECTION)
    if require_non_empty and not basis:
        raise ProtocolValidationError("basis is required for grounded answer", GROUNDED_CORRECTION)
    allowed = _current_non_answer_step_ids(journal)
    invalid = [item for item in basis if item not in allowed]
    if invalid:
        raise ProtocolValidationError(f"basis references are not current non-answer steps: {invalid}", GROUNDED_CORRECTION)
    return basis


def validate_answer_text_payload(payload: Any, journal: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolValidationError("answer_text arguments must be an object", GROUNDED_CORRECTION)
    required = {"answer_type", "text", "basis", "self_check"}
    missing = sorted(required - set(payload))
    if missing:
        raise ProtocolValidationError(f"answer_text missing required fields: {missing}", GROUNDED_CORRECTION)
    if payload.get("answer_type") not in {"pure_text", "grounded_report", "clarification", "failure"}:
        raise ProtocolValidationError("answer_text answer_type is invalid", GROUNDED_CORRECTION)
    if not isinstance(payload.get("text"), str) or not payload.get("text").strip():
        raise ProtocolValidationError("answer_text text must be a non-empty string", GROUNDED_CORRECTION)

    self_check = payload.get("self_check")
    if not isinstance(self_check, dict):
        raise ProtocolValidationError("answer_text self_check must be an object", GROUNDED_CORRECTION)
    self_required = {
        "depends_on_current_external_state",
        "claims_completed_action",
        "has_sufficient_evidence",
        "missing_evidence_question",
    }
    self_missing = sorted(self_required - set(self_check))
    if self_missing:
        raise ProtocolValidationError(f"answer_text self_check missing fields: {self_missing}", GROUNDED_CORRECTION)
    for key in ("depends_on_current_external_state", "claims_completed_action", "has_sufficient_evidence"):
        if not isinstance(self_check.get(key), bool):
            raise ProtocolValidationError(f"answer_text self_check.{key} must be boolean", GROUNDED_CORRECTION)
    if not isinstance(self_check.get("missing_evidence_question"), str):
        raise ProtocolValidationError("answer_text self_check.missing_evidence_question must be string", GROUNDED_CORRECTION)

    answer_type = payload["answer_type"]
    if self_check.get("has_sufficient_evidence") is False and answer_type not in {"clarification", "failure"}:
        raise ProtocolValidationError("answer_text declares insufficient evidence", INSUFFICIENT_EVIDENCE_CORRECTION)
    requires_basis = (
        answer_type == "grounded_report"
        or bool(self_check.get("depends_on_current_external_state"))
        or bool(self_check.get("claims_completed_action"))
    )
    validate_basis_references(payload.get("basis"), journal, require_non_empty=requires_basis)
    return payload


def validate_answer_report_failure_payload(payload: Any, journal: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolValidationError("answer_report_failure arguments must be an object", GROUNDED_CORRECTION)
    required = {"message", "reason", "recoverable", "suggested_next_action", "basis"}
    missing = sorted(required - set(payload))
    if missing:
        raise ProtocolValidationError(f"answer_report_failure missing required fields: {missing}", GROUNDED_CORRECTION)
    if not isinstance(payload.get("recoverable"), bool):
        raise ProtocolValidationError("answer_report_failure recoverable must be boolean", GROUNDED_CORRECTION)
    basis = validate_basis_references(payload.get("basis"), journal, require_non_empty=False)
    if not basis and _has_failed_non_answer_step(journal):
        raise ProtocolValidationError("answer_report_failure must reference the failed current-run step", GROUNDED_CORRECTION)
    return payload


def validate_answer_confirmation_payload(payload: Any, journal: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolValidationError("answer_request_confirmation arguments must be an object", GROUNDED_CORRECTION)
    required = {"message", "action", "risk", "command_preview", "basis"}
    missing = sorted(required - set(payload))
    if missing:
        raise ProtocolValidationError(f"answer_request_confirmation missing required fields: {missing}", GROUNDED_CORRECTION)
    if payload.get("risk") not in {"low", "medium", "high"}:
        raise ProtocolValidationError("answer_request_confirmation risk is invalid", GROUNDED_CORRECTION)
    validate_basis_references(payload.get("basis"), journal, require_non_empty=False)
    return payload
