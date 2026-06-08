from __future__ import annotations

from typing import Any


FAIL_CLOSED_MESSAGE = (
    "Не удалось безопасно проверить корректность финального ответа. Повтори запрос."
)

_FAILED_STATUSES = {"failed", "error", "blocked", "cancelled"}
_SUCCESS_STATUSES = {
    "ok",
    "success",
    "done",
    "completed",
    "found",
    "verified",
    "opened_verified",
    "opened_visible_focus_failed",
    "opened_unverified",
    "opened_browser_visible",
    "partial_success",
    "launched",
    "launched_verified",
}


def answer_auditor_infra_fail_closed(cfg: dict[str, Any] | None) -> bool:
    return bool((cfg or {}).get("answer_auditor_infra_fail_closed", False))


def _basis_step_map(commands_log: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("step_id")): entry
        for entry in (commands_log or [])
        if isinstance(entry, dict) and entry.get("step_id")
    }


def _result_has_success_or_partial_evidence(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or entry.get("tool_status") or "").lower()
    if status in _FAILED_STATUSES:
        return False

    result = entry.get("result")
    if not isinstance(result, dict):
        return bool(entry.get("tool_name") or entry.get("action"))

    if result.get("error") or result.get("launch_error"):
        return False

    result_status = str(result.get("status") or "").lower()
    if result_status in _FAILED_STATUSES:
        return False
    if result_status in _SUCCESS_STATUSES:
        return True
    if result.get("completion_state") in {"success", "partial_success"}:
        return True
    if result.get("terminal_sufficient"):
        return True
    if result.get("returncode") in (0, "0"):
        return True
    if result.get("launched") is True:
        return True
    if result.get("path") or result.get("url") or result.get("facts") or result.get("windows") is not None:
        return True

    return bool(entry.get("tool_name") or entry.get("action"))


def is_answer_payload_grounded_for_auditor_infra_failure(
    answer_payload: dict[str, Any],
    commands_log: list[dict[str, Any]] | None,
) -> bool:
    if not isinstance(answer_payload, dict):
        return False

    if answer_payload.get("answer_type") in {"clarification"}:
        return False

    self_check = answer_payload.get("self_check") if isinstance(answer_payload.get("self_check"), dict) else {}
    if self_check.get("has_sufficient_evidence") is not True:
        return False

    basis = [str(item) for item in answer_payload.get("basis") or [] if str(item).strip()]
    if not basis:
        return False

    steps = _basis_step_map(commands_log)
    if not steps or any(step_id not in steps for step_id in basis):
        return False

    return any(_result_has_success_or_partial_evidence(steps[step_id]) for step_id in basis)
