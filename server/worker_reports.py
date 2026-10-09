"""Worker Report v1 is a projection of existing receipts and evidence, not LLM prose."""
from pathlib import PureWindowsPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class WorkerReport(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    schema_version: Literal[1]
    task_id: str
    worker_id: Literal["worker-1"]
    status: Literal["queued","running","waiting_confirmation","success","partial","blocked","failed","cancelled","unknown"]
    goal_completed: bool
    summary: str = Field(max_length=2000)
    target_device_ids: list[str]
    artifacts: list[dict]
    evidence_refs: list[str]
    requires_user_action: bool
    error_code: str | None

    @model_validator(mode="after")
    def check_outcome(self):
        if self.goal_completed != (self.status=="success"):raise ValueError("inconsistent_goal_status")
        if self.goal_completed and not self.evidence_refs:raise ValueError("missing_goal_evidence")
        return self


try:
    from .controller_trust import has_grounded_terminal_answer
    from .runtime_state import _short_did
    from .tool_completion import execute_cmd_result_is_negative
    from .command_confirmation import confirmed_command_outcome
except ImportError:
    from controller_trust import has_grounded_terminal_answer
    from runtime_state import _short_did
    from tool_completion import execute_cmd_result_is_negative
    from command_confirmation import confirmed_command_outcome

TERMINAL = {"done", "completed", "completed_with_recovery", "failed", "error", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


def positive_evidence(command):
    result=command.get("result")
    if command.get("status")!="success" or not isinstance(result,dict) or result.get("error"):return False
    status=result.get("status")
    if status is not None and (not isinstance(status,str) or status in {"unknown","started","launch_requested","pending","not_found","failed","error","blocked","partial","disconnected"}):return False
    if result.get("returncode") is not None and (type(result["returncode"]) is not int or result["returncode"]!=0):return False
    if command.get("tool_name") == "execute_cmd" or command.get("action") == "execute_cmd":
        return confirmed_command_outcome(result)=="success"
    return not execute_cmd_result_is_negative(result)


def build_worker_report(task):
    commands = [c for c in task.get("commands") or [] if isinstance(c, dict)]
    receipt = task.get("task_receipt") if isinstance(task.get("task_receipt"),dict) else {}
    raw = task.get("status", "unknown")
    negative = {"error":"failed", "failed":"failed", "cancelled":"cancelled", "blocked":"blocked", "partial":"partial", "unknown":"unknown", "interrupted":"unknown"}
    refs = [c["step_id"] for c in commands if isinstance(c.get("step_id"), str)]
    evidence=[c for c in commands if positive_evidence(c)]
    confirmed = bool(evidence) and ((receipt.get("task_status") in {"completed", "completed_with_recovery"}
                and (receipt.get("goal_completed") is True or receipt.get("final_verification_status") == "verified"))
                or has_grounded_terminal_answer(task.get("answer") or "", commands))
    terminal=next((c for c in reversed(commands) if c.get("status")=="terminal"),{})
    payload=terminal.get("result") if isinstance(terminal.get("result"),dict) else {}
    terminal_negative={"partial_report":"partial","error_report":"failed","failure":"failed","clarification":"blocked"}.get(payload.get("answer_type"))
    if task.get("plan_suggestion"):status="blocked"
    elif raw == "queued": status = "queued"
    elif raw == "confirm": status = "waiting_confirmation"
    elif raw not in TERMINAL: status = "running"
    elif raw in negative: status = negative[raw]
    elif receipt.get("task_status") in negative: status = negative[receipt["task_status"]]
    elif terminal_negative:status=terminal_negative
    elif receipt.get("goal_completed") is False: status = "partial"
    elif receipt.get("final_verification_status") == "failed": status = "failed"
    else: status = "success" if confirmed else "unknown"
    if status=="success" and receipt.get("final_verification_status")!="verified" and any(isinstance(t,dict) and t.get("status") in {"failed","blocked","partial"} for t in task.get("tasks") or []):
        status = "partial"
    if task.get("cancel_requested"):status="cancelled"
    artifacts = []
    allowed_devices={_short_did(d) for d in task.get("device_ids") or []} or {"server"}
    for entry in evidence:
        result = entry["result"];tool = entry.get("tool_name") or entry.get("action")
        verified_paths = result.get("files_verified") or result.get("verified_files") or []
        if isinstance(verified_paths, str): verified_paths = [verified_paths]
        paths = list(verified_paths) if isinstance(verified_paths, list) else []
        if tool == "write_content" and isinstance(result.get("bytes_written"), int):
            paths += [result.get("path") or result.get("file_path")]
        if tool == "transfer_file" and result.get("sha256_verified") is True: paths += [result.get("target_path")]
        for path in paths:
            device = result.get("target_device") if tool == "transfer_file" else _short_did(entry.get("target_device_id") or entry.get("device_id") or "")
            if isinstance(path,str) and path and device in allowed_devices and not any(a["path"] == path and a["device_id"] == device for a in artifacts):
                artifacts.append({"type":"file", "name":PureWindowsPath(path).name, "path":path, "device_id":device, "verified":True})
    summaries = {"success":"Задача выполнена по подтверждённым результатам.", "partial":"Задача выполнена частично.",
        "blocked":"Выполнение заблокировано.", "failed":"Задача завершилась с ошибкой.", "cancelled":"Задача отменена.",
        "unknown":"Исход задачи не подтверждён; автоматически её не повторяю.", "queued":"Задача ожидает освобождения Worker.",
        "running":"Задача выполняется.", "waiting_confirmation":"Задача ожидает решения пользователя."}
    report = {"schema_version":1, "task_id":task["task_id"], "worker_id":"worker-1", "status":status,
        "goal_completed":status == "success", "summary":payload["text"][:600] if terminal_negative and isinstance(payload.get("text"),str) else summaries[status],
        "target_device_ids":[_short_did(d) for d in task.get("device_ids") or []], "artifacts":artifacts,
        "evidence_refs":refs, "requires_user_action":status in {"waiting_confirmation","blocked"},
        "error_code":task.get("worker_error_code") or receipt.get("terminal_reason") if status != "success" else None}
    return WorkerReport.model_validate(report).model_dump()
