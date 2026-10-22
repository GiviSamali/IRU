"""Worker Report v1 is a projection of existing receipts and evidence, not LLM prose."""
from pathlib import PureWindowsPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class WorkerReport(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    schema_version: Literal[1]
    task_id: str
    worker_id: str = Field(pattern=r"^worker-[1-4]$")
    status: Literal["queued","waiting_dependency","running","waiting_confirmation","waiting_input","success","partial","blocked","failed","cancelled","unknown"]
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
    from .tool_completion import execute_cmd_outcome_marker, execute_cmd_result_is_negative, execute_cmd_result_is_complete, execute_cmd_returncode_is_zero, write_content_result_is_ok, tool_result_terminal_sufficient
    from .command_confirmation import confirmed_command_outcome
except ImportError:
    from controller_trust import has_grounded_terminal_answer
    from runtime_state import _short_did
    from tool_completion import execute_cmd_outcome_marker, execute_cmd_result_is_negative, execute_cmd_result_is_complete, execute_cmd_returncode_is_zero, write_content_result_is_ok, tool_result_terminal_sufficient
    from command_confirmation import confirmed_command_outcome

TERMINAL = {"done", "completed", "completed_with_recovery", "failed", "error", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


def positive_evidence(command, *, observation=False):
    result=command.get("result")
    if command.get("status")!="success" or not isinstance(result,dict) or result.get("error"):return False
    status=result.get("status")
    if status is not None and (not isinstance(status,str) or status in {"unknown","started","launch_requested","pending","not_found","failed","error","blocked","partial","disconnected"}):return False
    if result.get("returncode") is not None and not execute_cmd_returncode_is_zero(result["returncode"]):return False
    if (command.get("tool_name") or command.get("action")) == "write_content":
        return write_content_result_is_ok(result)
    if command.get("tool_name") == "execute_cmd" or command.get("action") == "execute_cmd":
        if any(isinstance(r,dict) and r.get("verified") is True and r.get("created_or_changed") is True
               for r in result.get("artifact_receipts") or []):
            return True
        if confirmed_command_outcome(result)=="success" and execute_cmd_outcome_marker(result)=="OK":return True
        # A successful observation is usable by an already validated informational
        # terminal answer. It does not establish that an action achieved its goal.
        return observation and execute_cmd_result_is_complete(result) and not execute_cmd_result_is_negative(result)
    return not execute_cmd_result_is_negative(result)


def build_worker_report(task):
    commands = [c for c in task.get("commands") or [] if isinstance(c, dict)]
    receipt = task.get("task_receipt") if isinstance(task.get("task_receipt"),dict) else {}
    raw = task.get("status", "unknown")
    negative = {"error":"failed", "failed":"failed", "cancelled":"cancelled", "blocked":"blocked", "partial":"partial", "unknown":"unknown", "interrupted":"unknown"}
    refs = [c["step_id"] for c in commands if isinstance(c.get("step_id"), str)]
    evidence=[c for c in commands if positive_evidence(c)]
    grounded = has_grounded_terminal_answer(task.get("answer") or "", commands)
    terminal_answer = next((c for c in reversed(commands) if c.get("tool_name")=="answer.text" and c.get("status")=="terminal"), {})
    answer_payload = terminal_answer.get("result") if isinstance(terminal_answer.get("result"), dict) else {}
    check = answer_payload.get("self_check") if isinstance(answer_payload.get("self_check"), dict) else {}
    basis = answer_payload.get("basis") if isinstance(answer_payload.get("basis"), list) else []
    observations = [c for c in commands if c.get("step_id") in basis]
    informational = (grounded and check.get("claims_completed_action") is False and bool(observations)
                     and receipt.get("answer_source")=="audited_terminal"
                     and receipt.get("goal_completed") is True and receipt.get("final_verification_status")=="verified"
                     and receipt.get("task_status")=="completed"
                     and all(positive_evidence(c, observation=True) for c in observations))
    # Require both operation evidence and the existing original-goal answer audit.
    try:
        from .run_journal import audited_action_evidence
    except ImportError:
        from run_journal import audited_action_evidence
    action_evidence=receipt.get('action_evidence')
    matched_action_evidence=(isinstance(action_evidence,dict) and action_evidence == audited_action_evidence(
        answer_payload,commands,(task.get('proposed_objective') or task.get('message') or '') if task.get('goal_id')
        else task.get('original_request') or task.get('message') or '',action_evidence))
    confirmed = informational or (grounded and bool(observations)
                and receipt.get("answer_source") == "audited_terminal"
                and receipt.get("task_status") in {"completed", "completed_with_recovery"}
                and receipt.get("goal_completed") is True
                and receipt.get("final_verification_status") == "verified"
                and all(positive_evidence(c, observation=True) for c in observations)
                and (any(positive_evidence(c) for c in observations) or matched_action_evidence))
    native_basis = observations if grounded else evidence
    native_evidence = bool(native_basis) and all(
        positive_evidence(c) and (c.get("tool_name") or c.get("action")) not in {"execute_cmd", "write_content"}
        and tool_result_terminal_sufficient(c) for c in native_basis)
    confirmed = confirmed or (native_evidence and (
        (receipt.get("answer_source") != "audited_terminal"
         and receipt.get("task_status") in {"completed", "completed_with_recovery"}
         and (receipt.get("goal_completed") is True or receipt.get("final_verification_status") == "verified"))
        or (grounded and bool(observations) and all(positive_evidence(c) for c in observations))))
    terminal=next((c for c in reversed(commands) if c.get("status")=="terminal"),{})
    payload=terminal.get("result") if isinstance(terminal.get("result"),dict) else {}
    clarification=(terminal.get('tool_name')=='answer.ask_clarification'
                   and isinstance(payload.get('question'),str) and payload['question'].strip()
                   and payload['question']==task.get('answer'))
    terminal_negative={"partial_report":"partial","error_report":"failed","failure":"failed","clarification":"blocked"}.get(payload.get("answer_type"))
    if clarification:terminal_negative='blocked'
    if task.get("plan_suggestion"):status="blocked"
    elif raw == "queued": status = "queued"
    elif raw == "waiting_dependency": status = "waiting_dependency"
    elif raw == "confirm": status = "waiting_confirmation"
    elif raw == "waiting_input": status = "waiting_input"
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
    final_receipts = []
    def same_artifact(left, right):
        def key(item):
            path = item["path"]
            return (item["device_id"], PureWindowsPath(path).as_posix().casefold()
                    if PureWindowsPath(path).drive else path)
        return key(left) == key(right)
    def record_artifact(artifact, native=False):
        # The latest operation owns the path; an older receipt must not survive
        # a subsequent change, even when that change lacks native verification.
        artifacts[:] = [a for a in artifacts if not same_artifact(a, artifact)]
        final_receipts[:] = [a for a in final_receipts if not same_artifact(a, artifact)]
        artifacts.append(artifact)
        if native:
            final_receipts.append(artifact)
    allowed_devices={_short_did(d) for d in task.get("device_ids") or []} or {"server"}
    for entry in evidence:
        result = entry["result"];tool = entry.get("tool_name") or entry.get("action")
        device = _short_did(entry.get("target_device_id") or entry.get("device_id") or "")
        for item in result.get("artifact_receipts") or []:
            if (not isinstance(item,dict) or item.get("source_task_id") != task["task_id"]
                    or item.get("device_id") != device or device not in allowed_devices
                    or item.get("verified") is not True or item.get("created_or_changed") is not True
                    or type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0
                    or not isinstance(item.get("path"),str) or not isinstance(item.get("sha256"),str)
                    or len(item["sha256"]) != 64):
                continue
            artifact = {**item,"name":PureWindowsPath(item["path"]).name,"worker_id":task.get("worker_id") or "worker-1"}
            record_artifact(artifact, native=True)
        verified_paths = result.get("files_verified") or result.get("verified_files") or []
        if isinstance(verified_paths, str): verified_paths = [verified_paths]
        paths = list(verified_paths) if isinstance(verified_paths, list) else []
        if tool == "write_content" and isinstance(result.get("bytes_written"), int):
            paths += [result.get("path") or result.get("file_path")]
        if tool == "transfer_file" and result.get("sha256_verified") is True: paths += [result.get("target_path")]
        for path in paths:
            device = result.get("target_device") if tool == "transfer_file" else _short_did(entry.get("target_device_id") or entry.get("device_id") or "")
            if isinstance(path,str) and path and device in allowed_devices:
                artifact = {"type":"file", "name":PureWindowsPath(path).name, "path":path, "device_id":device,
                    "verified":True,"source_task_id":task["task_id"],"worker_id":task.get("worker_id") or "worker-1"}
                if tool == "write_content" and result.get("verified") is True and result.get("committed") is True:
                    artifact.update(size_bytes=result.get("total_size"),sha256=result.get("file_sha256"))
                transferred = tool == "transfer_file" and result.get("sha256_verified") is True
                if transferred:
                    artifact.update(size_bytes=result.get("bytes_transferred"),sha256=result.get("sha256"))
                record_artifact(artifact, native=transferred or (tool == "write_content"
                    and result.get("verified") is True and result.get("committed") is True))
    expected = task.get("assigned_artifacts") or []
    user_files = [a for a in final_receipts if "/scripts/helpers/" not in a["path"].replace("\\","/").casefold()
                  or any(str(e).replace("\\","/").split("/")[-1].casefold()==a["name"].casefold() for e in expected)]
    # Expected labels may be descriptive or an earlier proposed filename. Keep
    # the existing goal audit authoritative for semantics; require real files.
    artifact_incomplete = status == "success" and bool(expected) and len(user_files) < len(expected)
    if artifact_incomplete:
        status = "partial" if user_files else "unknown"
    summaries = {"success":"Задача выполнена по подтверждённым результатам.", "partial":"Задача выполнена частично.",
        "blocked":"Выполнение заблокировано.", "failed":"Задача завершилась с ошибкой.", "cancelled":"Задача отменена.",
        "unknown":"Исход задачи не подтверждён; автоматически её не повторяю.", "queued":"Задача ожидает освобождения Worker.",
        "waiting_dependency":"Задача ожидает подтверждённого результата другой задачи.",
        "waiting_input":"Задача ожидает выбора окна.", "running":"Задача выполняется.", "waiting_confirmation":"Задача ожидает решения пользователя."}
    error_code=(task.get("worker_error_code") or receipt.get("terminal_reason")) if status!="success" else None
    if artifact_incomplete:
        error_code = "artifact_receipt_missing"
    if not isinstance(error_code, str):error_code=None
    if clarification and status=='blocked':error_code='clarification_required'
    if status=='unknown' and not error_code:
        if grounded and receipt.get('answer_source')=='audited_terminal':error_code='action_evidence_unconfirmed'
        elif terminal.get('tool_name')=='answer.text':error_code='goal_not_completed'
        elif any(c.get('tool_name')=='tool_only_protocol' for c in commands):error_code='terminal_answer_missing'
    report = {"schema_version":1, "task_id":task["task_id"], "worker_id":task.get("worker_id") or "worker-1", "status":status,
        "goal_completed":status == "success", "summary":payload['question'][:2000] if clarification and status=='blocked' else payload["text"][:2000] if not artifact_incomplete and (terminal_negative or confirmed) and isinstance(payload.get("text"),str) and payload["text"] == task.get("answer") else summaries[status],
        "target_device_ids":[_short_did(d) for d in task.get("device_ids") or []], "artifacts":artifacts,
        "evidence_refs":refs, "requires_user_action":status in {"waiting_confirmation","waiting_input","blocked"},
        "error_code":error_code}
    return WorkerReport.model_validate(report).model_dump()
