"""Worker Report projects the executor verdict and receipt provenance; it does not reassess the Assignment."""
from pathlib import PureWindowsPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def bounded_report_summary(text, limit=2000):
    """Keep complete Markdown blocks, including fences and list/table groups."""
    if len(text) <= limit:
        return text
    import re
    marker = '\n[Сокращено. Полный текст сохранён в отчёте задачи.]'
    budget = limit - len(marker)
    boundaries = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and stripped == token:
                fence = None
        offset += len(line)
        if offset > budget:
            break
        if not stripped and fence is None:
            boundaries.append(offset)
    return (text[:max(boundaries)].rstrip() if boundaries else 'Подробный результат сохранён в отчёте задачи.') + marker

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
    unresolved: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    requires_user_action: bool
    error_code: str | None

    @model_validator(mode="after")
    def check_outcome(self):
        if self.goal_completed != (self.status=="success"):raise ValueError("inconsistent_goal_status")
        return self


from hashlib import sha256
import json
import re
try:
    from .runtime_state import _short_did
except ImportError:
    from runtime_state import _short_did
try:
    from .tool_completion import normalize_operation_result
except ImportError:
    from tool_completion import normalize_operation_result

TERMINAL = {"done", "completed", "completed_with_recovery", "failed", "error", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


def operation_facts(command):
    tool = command.get('tool_name') or command.get('action') or ''
    saved = command.get('tool_status')
    if saved in {'success', 'failed', 'partial', 'unknown', 'not_executed'}:
        return saved
    # Historical entries without a stored outcome remain readable; no replay.
    outcome = normalize_operation_result(tool, command.get('result'))
    return outcome


def result_binding(task, report=None):
    fields = ('task_id','message','original_request','proposed_objective','proposed_context_summary','goal_id','assigned_artifacts',
              'device_ids','window_effects','commands','answer','cancel_requested','_action_outcome_unknown')
    data = {k:task.get(k) for k in fields}
    if task.get('assignment_scope') or task.get('assignment_constraints'):
        data.update(assignment_scope=task.get('assignment_scope'),assignment_constraints=task.get('assignment_constraints'))
    raw=task.get('status')
    data['lifecycle'] = ('unknown' if raw in {'unknown','interrupted'} else raw
        if raw in {'queued','running','confirm','waiting_confirmation','waiting_input','waiting_dependency','cancelled','blocked'} else 'terminal')
    if report is not None: data['report'] = report
    return sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()






def receipt_from_report(report, previous=None):
    receipt = dict(previous or {})
    receipt.update(task_status='completed' if report['status']=='success' else report['status'],
                   goal_completed=report['goal_completed'],
                   final_verification_status='verified' if report['goal_completed'] else 'unverified',
                   answer_source='worker_report')
    return receipt


def saved_worker_report(task, report=None):
    candidate = report or task.get('worker_report')
    try:
        checked = WorkerReport.model_validate(candidate).model_dump()
        if checked['task_id'] == task['task_id'] and task.get('worker_report_binding') in {result_binding(task,checked), result_binding(task,candidate)}:
            return checked
    except (ValueError,TypeError):
        pass
    # One deterministic legacy adapter; no model/device/replay on reads.
    return build_worker_report({**task, '_report_integrity_failed':bool(task.get('worker_report_binding')),
                               '_legacy_read':not task.get('worker_report_binding')})


def build_worker_report(task):
    task_id = task["task_id"]
    # Only this Task's own effects are evidence. A journal entry produced by
    # another Worker must never be re-attributed here; legacy entries without
    # provenance stay readable.
    commands = [c for c in task.get('commands') or []
                if isinstance(c,dict) and c.get('task_id') in (None, task_id)]
    receipt = task.get('task_receipt') if isinstance(task.get('task_receipt'),dict) else {}
    raw = task.get('status','unknown')
    refs = [c['step_id'] for c in commands if isinstance(c.get('step_id'),str)]
    operations=[c for c in commands if (c.get('tool_type') not in {'system','answer'}
        or c.get('tool_name') in {'system.list_tools','system.get_last_run_summary','system_list_tools','system_get_last_run_summary'})
        and not str(c.get('tool_name','')).startswith('answer')]
    evidence=[c for c in operations if operation_facts(c) in {'success','partial'}]
    # A deliberate failure report is a terminal answer too: it must not be
    # reported as "terminal answer missing".
    terminal = next((c for c in reversed(commands)
                     if c.get('tool_name') in {'answer.text','answer_text',
                                               'answer.report_failure','answer_report_failure'}
                     and c.get('status')=='terminal'),{})
    payload = terminal.get('result') if isinstance(terminal.get('result'),dict) else {}
    evidence_binding_error=False
    if payload.get('_result_contract')==4:
        try:
            from .run_journal import terminal_result_binding
        except ImportError:
            from run_journal import terminal_result_binding
        evidence_binding_error=payload.get('_evidence_binding')!=terminal_result_binding(payload,commands)
    compact = payload.get('_result_contract')==3
    if compact:
        refs=[c['step_id'] for c in operations if isinstance(c.get('step_id'),str)]
        if not operations and isinstance(terminal.get('step_id'),str):
            refs=[terminal['step_id']]  # The analytical text itself is the output, not an external action receipt.
    text = (payload.get('text') if payload.get('_result_contract')==4 else task.get('answer')) or payload.get('text') or payload.get('answer') or payload.get('message') or ''
    clarification = next((c for c in reversed(commands) if c.get('tool_name')=='answer.ask_clarification'),None)
    artifacts = []
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
        if native and (not isinstance(artifact.get('sha256'),str) or not re.fullmatch('[a-fA-F0-9]{64}',artifact['sha256'])):
            native=False
            artifact['verified']=False
        artifact.setdefault('content_observed',False)
        artifact.setdefault('content_verified',False)
        artifact.setdefault('verification_scope','file_identity_and_format'
            if artifact.get('structure')=='office_zip_xml' else 'file_identity')
        artifacts.append(artifact)
    allowed_devices={_short_did(d) for d in task.get("device_ids") or []} or {"server"}
    for entry in evidence:
        result = entry.get("result");tool = entry.get("tool_name") or entry.get("action")
        if not isinstance(result,dict):continue
        device = _short_did(entry.get("target_device_id") or entry.get("device_id") or "")
        native_files = tool in {'execute_cmd','write_content','office_docx','office_xlsx','office_pptx','transfer_file'}
        for item in (result.get("artifact_receipts") or []) if native_files else []:
            if (not isinstance(item,dict) or item.get("source_task_id") != task["task_id"]
                    or item.get("device_id") != device or device not in allowed_devices
                    or item.get("verified") is not True or item.get("created_or_changed") is not True
                    or type(item.get("size_bytes")) is not int or item["size_bytes"] < 0
                    or not isinstance(item.get("path"),str) or not isinstance(item.get("sha256"),str)
                    or not re.fullmatch("[a-fA-F0-9]{64}",item["sha256"])):
                continue
            artifact = {**item,"name":PureWindowsPath(item["path"]).name,"worker_id":task.get("worker_id") or "worker-1"}
            record_artifact(artifact, native=True)
        paths = []
        if tool == 'write_content' and operation_facts(entry)=='success':paths=[result['path']]
        if tool == 'transfer_file' and result.get('sha256_verified') is True and re.fullmatch('[a-fA-F0-9]{64}',str(result.get('sha256',''))):
            paths=[result.get('target_path')]
        for path in paths:
            device = result.get("target_device") if tool == "transfer_file" else _short_did(entry.get("target_device_id") or entry.get("device_id") or "")
            if isinstance(path,str) and path and device in allowed_devices:
                artifact = {"type":"file", "name":PureWindowsPath(path).name, "path":path, "device_id":device,
                    "verified":bool(result.get("sha256_verified") if tool=="transfer_file" else result.get("verified") and result.get("committed")),"source_task_id":entry.get("task_id") or task["task_id"],
                    "worker_id":task.get("worker_id") or "worker-1"}
                if tool in {"office_docx","office_xlsx","office_pptx"}:
                    native = next((r for r in result.get("artifact_receipts") or [] if isinstance(r,dict)
                        and r.get("path") == path and r.get("source_task_id") == task["task_id"]
                        and r.get("device_id") == device and r.get("verified") is True), None)
                    if native is None: continue
                    artifact.update(native)
                    record_artifact(artifact, native=True)
                    continue
                if tool == "write_content" and result.get("verified") is True and result.get("committed") is True:
                    artifact.update(size_bytes=result.get("total_size"),sha256=result.get("file_sha256"))
                    complete_input=result.get('mode') not in {'replace','replace_exact'}
                    artifact.update(content_observed=complete_input,content_verified=complete_input,
                                    verification_scope='file_bytes_only')
                transferred = tool == "transfer_file" and result.get("sha256_verified") is True
                if transferred:
                    artifact.update(size_bytes=result.get("bytes_transferred"),sha256=result.get("sha256"))
                    artifact['verification_scope']='copy_identity'
                record_artifact(artifact, native=transferred or (tool == "write_content"
                    and result.get("verified") is True and result.get("committed") is True))
    # Artifact provenance/integrity is mechanical; it never evaluates whether
    # the human's requested work was substantively completed.
    def file_key(item):
        path = item['path']
        return (_short_did(item.get('device_id') or ''),
                PureWindowsPath(path).as_posix().casefold() if PureWindowsPath(path).drive else path)
    invalidated_files = set()
    for entry in operations:
        result = entry.get('result') or {}
        if not isinstance(result,dict):continue
        tool=entry.get('tool_name') or entry.get('action')
        if (tool in {'read_file','get_file_content'} or tool in {'office_docx','office_xlsx','office_pptx'} and result.get('action')=='inspect'):
            digest=result.get('file_sha256') or result.get('sha256')
            device=_short_did(entry.get('target_device_id') or entry.get('device_id') or '')
            if operation_facts(entry)=='success' and isinstance(digest,str) and re.fullmatch('[a-fA-F0-9]{64}',digest):
                key=file_key({'device_id':device,'path':result.get('path') or ''})
                if any(file_key(a)==key and a.get('sha256')!=digest for a in artifacts):invalidated_files.add(key)
        if (entry.get('tool_name') or entry.get('action')) not in {'execute_cmd','write_content','office_docx','office_xlsx','office_pptx','transfer_file'}:continue
        device = _short_did(entry.get('target_device_id') or entry.get('device_id') or '')
        for error in result.get('artifact_errors') or []:
            if isinstance(error,dict) and isinstance(error.get('path'),str):
                invalidated_files.add(file_key({'device_id':device,'path':error['path']}))
        for receipt_item in result.get('artifact_receipts') or []:
            if (isinstance(receipt_item,dict) and receipt_item.get('source_task_id')==task['task_id']
                    and receipt_item.get('device_id')==device and receipt_item.get('verified') is True
                    and receipt_item.get('created_or_changed') is True
                    and isinstance(receipt_item.get('path'),str) and isinstance(receipt_item.get('sha256'),str)
                    and re.fullmatch('[a-fA-F0-9]{64}',receipt_item['sha256'])):
                invalidated_files.discard(file_key(receipt_item))
        if (entry.get('tool_name')=='write_content' and result.get('committed') is True
                and isinstance(result.get('path'),str)):
            key=file_key({'device_id':device,'path':result['path']})
            if operation_facts(entry)=='success':invalidated_files.discard(key)
            else:invalidated_files.add(key)
    for artifact in artifacts:
        if file_key(artifact) in invalidated_files:
            artifact.update(verified=False,content_verified=False,content_observed=False)
    outcome = payload.get('status') if payload.get('_result_contract') == 4 else None
    if task.get('cancel_requested') or raw == 'cancelled':
        status = 'cancelled'
    elif raw in {'queued','waiting_dependency','running','waiting_input','waiting_confirmation'}:
        status = raw
    elif raw == 'confirm':
        status = 'waiting_confirmation'
    elif raw in {'unknown','interrupted'} or evidence_binding_error or task.get('_report_integrity_failed') or task.get('_action_outcome_unknown'):
        status = 'unknown'
    elif raw=='blocked' or (receipt.get('answer_source')=='confirmation_result' and receipt.get('task_status')=='blocked'):
        status = 'blocked'
    elif outcome in {'success','partial','failed','unknown'}:
        status = outcome
    elif clarification:
        status = 'blocked'
    elif terminal.get('tool_name') in {'answer.report_failure','answer_report_failure'}:
        status = 'failed'
    elif terminal:
        # Historical terminals did not record an explicit executor verdict.
        # Preserve their durable lifecycle, never reassess their old Assignment.
        status = {'error':'failed','interrupted':'unknown'}.get(raw,raw)
        if raw in {'done', 'completed'} and receipt.get('answer_source') == 'audited_terminal':
            # This legacy receipt already records the executor's final verdict.
            recorded_status = receipt.get('task_status')
            if recorded_status in {'completed', 'partial', 'failed', 'unknown'}:
                status = 'success' if recorded_status == 'completed' else recorded_status
            if status == 'success' and any(operation_facts(entry) == 'unknown' for entry in operations):
                status = 'unknown'
        if status not in {'success','partial','failed','unknown','blocked'}:
            status = 'unknown'
        if payload.get('unresolved') and status == 'success':
            status = 'partial'
    else:
        fallback = receipt.get('task_status') if receipt.get('answer_source')=='server_fallback' else None
        status = fallback if fallback in {'partial','failed','unknown'} else 'failed' if raw=='error' else raw if raw in {'failed','partial','unknown','blocked'} else 'unknown'
    if payload.get('_result_contract') == 4:
        refs = list(payload.get('basis') or []) or ([terminal['step_id']] if not operations and terminal.get('step_id') else [])
    error_code = None if status == 'success' else task.get('worker_error_code') or receipt.get('terminal_reason')
    if evidence_binding_error:error_code='worker_evidence_binding_mismatch'
    if not error_code and status in {'failed','unknown'}:
        error_code = ('worker_report_binding_mismatch' if task.get('_report_integrity_failed') else
            'terminal_answer_missing' if not terminal else
            'legacy_terminal_status_missing' if not outcome and status=='unknown' else 'worker_' + status)
    if not isinstance(error_code,str):error_code=None
    if clarification:
        text = (clarification.get('result') or {}).get('question') or text
        options=(clarification.get('result') or {}).get('options') or []
        if options:text+='\n\n'+'\n'.join(f'{index}. {value}' for index,value in enumerate(options,1))
        error_code = 'clarification_required'
    elif not outcome and status=='unknown':
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'legacy_terminal_status_missing')['answer']
    report = {'schema_version':1,'task_id':task_id,'worker_id':task.get('worker_id') or 'worker-1',
        'status':status,'goal_completed':status=='success','summary':bounded_report_summary(text or ''),
        'target_device_ids':[_short_did(d) for d in task.get('device_ids') or []],
        'artifacts':artifacts,'evidence_refs':refs,'unresolved':list(payload.get('unresolved') or []),
        'caveats':list(payload.get('caveats') or []),
        'requires_user_action':status in {'blocked','waiting_input','waiting_confirmation'},'error_code':error_code}
    return WorkerReport.model_validate(report).model_dump()
