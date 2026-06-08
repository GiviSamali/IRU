from __future__ import annotations

import re
from typing import Any


ALLOWED_PROPOSAL_STATUSES = {"proposed", "reviewing", "approved", "rejected", "implemented", "deprecated"}
ALLOWED_PRIORITIES = {"low", "normal", "high"}
ALLOWED_RISK_LEVELS = {
    "safe",
    "read_only",
    "write",
    "runtime",
    "process_start",
    "process_control",
    "network",
    "destructive",
    "confirmation_required",
    "fallback",
}
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
SECRET_RE = re.compile(
    r"(api[_-]?key|secret|token|password|authorization|bearer\s+[a-z0-9._-]+|sk-[a-z0-9_-]{12,})",
    re.IGNORECASE,
)
DANGEROUS_DIRECT_EXECUTION_RE = re.compile(
    r"(dynamic\s+import|exec\s*\(|eval\s*\(|subprocess|production\s+registry|auto\s*install|"
    r"самовольно|автоматически\s+установ|добавь\s+в\s+production)",
    re.IGNORECASE,
)


class ToolProposalValidationError(ValueError):
    pass


def _db():
    try:
        from . import database as database_module  # type: ignore
    except ImportError:
        import database as database_module  # type: ignore
    return database_module


def _text_contains_forbidden(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple)):
        return any(_text_contains_forbidden(item) for item in (value.values() if isinstance(value, dict) else value))
    text = str(value)
    return bool(SECRET_RE.search(text) or DANGEROUS_DIRECT_EXECUTION_RE.search(text))


def _as_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolProposalValidationError(f"{field_name} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _as_object(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolProposalValidationError(f"{field_name} must be an object")
    return value


def validate_tool_proposal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ToolProposalValidationError("proposal payload must be an object")
    if _text_contains_forbidden(payload):
        raise ToolProposalValidationError("proposal must not contain secrets or direct production execution instructions")

    name = str(payload.get("name") or "").strip().lower()
    if not TOOL_NAME_RE.match(name):
        raise ToolProposalValidationError("name must look like namespace.action")

    problem = str(payload.get("problem") or "").strip()
    purpose = str(payload.get("purpose") or "").strip()
    if not problem:
        raise ToolProposalValidationError("problem is required")
    if not purpose:
        raise ToolProposalValidationError("purpose is required")

    risk_level = str(payload.get("risk_level") or "safe").strip().lower()
    if risk_level not in ALLOWED_RISK_LEVELS:
        raise ToolProposalValidationError(f"risk_level is not recognized: {risk_level}")
    priority = str(payload.get("priority") or "normal").strip().lower()
    if priority not in ALLOWED_PRIORITIES:
        raise ToolProposalValidationError(f"priority is not recognized: {priority}")

    return {
        "name": name,
        "title": str(payload.get("title") or name).strip(),
        "problem": problem,
        "purpose": purpose,
        "category": str(payload.get("category") or "tooling").strip().lower(),
        "tool_type": "proposal",
        "risk_level": risk_level,
        "permissions": _as_string_list(payload.get("permissions"), "permissions"),
        "input_schema": _as_object(payload.get("input_schema"), "input_schema"),
        "output_schema": _as_object(payload.get("output_schema"), "output_schema"),
        "evidence_contract": _as_object(payload.get("evidence_contract"), "evidence_contract"),
        "side_effects": _as_string_list(payload.get("side_effects"), "side_effects"),
        "idempotency": str(payload.get("idempotency") or "unknown").strip(),
        "cleanup": str(payload.get("cleanup") or "").strip() or None,
        "rollback": str(payload.get("rollback") or "").strip() or None,
        "examples": payload.get("examples") if isinstance(payload.get("examples"), list) else [],
        "test_plan": _as_string_list(payload.get("test_plan"), "test_plan"),
        "priority": priority,
        "notes": str(payload.get("notes") or "").strip() or None,
    }


def create_tool_proposal(
    payload: dict[str, Any],
    *,
    user_id: int | None,
    chat_id: int | None = None,
    source_task_id: str | None = None,
    source_poll_task_id: str | None = None,
) -> dict[str, Any]:
    proposal = validate_tool_proposal_payload(payload)
    proposal_id = _db().add_tool_proposal(
        **proposal,
        user_id=user_id,
        chat_id=chat_id,
        source_task_id=source_task_id,
        source_poll_task_id=source_poll_task_id,
        status="proposed",
    )
    return {
        "status": "created",
        "proposal_id": proposal_id,
        "name": proposal["name"],
        "title": proposal["title"],
        "proposal_status": "proposed",
        "summary": f"Tool proposal {proposal['name']} created for review.",
    }


def list_current_user_tool_proposals(user_id: int | None, *, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    if status and status not in ALLOWED_PROPOSAL_STATUSES:
        raise ToolProposalValidationError(f"unsupported status: {status}")
    proposals = _db().list_tool_proposals(user_id=user_id, status=status, limit=limit)
    return {
        "status": "ok",
        "count": len(proposals),
        "proposals": proposals,
    }


def get_current_user_tool_proposal(proposal_id: int, user_id: int | None) -> dict[str, Any]:
    proposal = _db().get_tool_proposal(proposal_id, user_id=user_id)
    if not proposal:
        return {"status": "not_found", "error": "proposal not found"}
    return {"status": "ok", "proposal": proposal}


def update_current_user_tool_proposal_status(
    proposal_id: int,
    *,
    status: str,
    notes: str | None,
    user_id: int | None,
) -> dict[str, Any]:
    if status not in ALLOWED_PROPOSAL_STATUSES:
        raise ToolProposalValidationError(f"unsupported status: {status}")
    if _text_contains_forbidden(notes):
        raise ToolProposalValidationError("notes must not contain secrets or direct production execution instructions")
    proposal = _db().update_tool_proposal_status(proposal_id, status, notes=notes, user_id=user_id)
    if not proposal:
        return {"status": "not_found", "error": "proposal not found"}
    return {"status": "updated", "proposal": proposal}


def run_tool_proposal_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    user_id: int | None,
    chat_id: int | None = None,
    poll_task_id: str | None = None,
) -> dict[str, Any]:
    try:
        if tool_name == "tool_propose":
            return create_tool_proposal(
                args,
                user_id=user_id,
                chat_id=chat_id,
                source_poll_task_id=poll_task_id,
            )
        if tool_name == "tool_list_proposals":
            return list_current_user_tool_proposals(
                user_id,
                status=args.get("status"),
                limit=int(args.get("limit") or 50),
            )
        if tool_name == "tool_get_proposal":
            return get_current_user_tool_proposal(int(args.get("proposal_id") or 0), user_id)
        if tool_name == "tool_update_proposal_status":
            return update_current_user_tool_proposal_status(
                int(args.get("proposal_id") or 0),
                status=str(args.get("status") or ""),
                notes=args.get("notes"),
                user_id=user_id,
            )
    except ToolProposalValidationError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "error", "error": f"unknown proposal tool: {tool_name}"}
