"""Worker Report v1 is a projection of existing receipts and evidence, not LLM prose."""
import os
from pathlib import PureWindowsPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _report_path(value):
    """Normalized path for object matching; a window title is never a path."""
    text = str(value or "").strip().strip('"')
    return PureWindowsPath(text.replace('/', '\\')).as_posix().casefold() if text else ''


def free_form_reports():
    """Free-form presentation.

    When enabled the model narrates the work in its own words and the report
    keeps only machine facts: status, error_code, artifacts with SHA. No canned
    verdict phrases are substituted for the model's answer.
    """
    return os.getenv('IRU_FREE_FORM_REPORT', '').strip().lower() in {'1', 'true', 'yes', 'on'}


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
    requires_user_action: bool
    error_code: str | None

    @model_validator(mode="after")
    def check_outcome(self):
        if self.goal_completed != (self.status=="success"):raise ValueError("inconsistent_goal_status")
        if self.goal_completed and not self.evidence_refs:raise ValueError("missing_goal_evidence")
        return self


from hashlib import sha256
import json
import re
try:
    from .runtime_state import _short_did
except ImportError:
    from runtime_state import _short_did
try:
    from .tool_completion import normalize_operation_result, confirmed_window_effect
except ImportError:
    from tool_completion import normalize_operation_result, confirmed_window_effect

TERMINAL = {"done", "completed", "completed_with_recovery", "failed", "error", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


def operation_facts(command):
    tool = command.get('tool_name') or command.get('action') or ''
    outcome = normalize_operation_result(tool, command.get('result'))
    return outcome


def result_binding(task, report=None):
    fields = ('task_id','message','original_request','proposed_objective','proposed_context_summary','goal_id','assigned_artifacts',
              'device_ids','execution_scope','window_effects','commands','answer','cancel_requested','_action_outcome_unknown')
    data = {k:task.get(k) for k in fields}
    if task.get('assignment_scope') or task.get('assignment_constraints'):
        data.update(assignment_scope=task.get('assignment_scope'),assignment_constraints=task.get('assignment_constraints'))
    raw=task.get('status')
    data['lifecycle'] = ('unknown' if raw in {'unknown','interrupted'} else raw
        if raw in {'queued','running','confirm','waiting_confirmation','waiting_input','waiting_dependency','cancelled','blocked'} else 'terminal')
    if report is not None: data['report'] = report
    return sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def coverage_binding(request, journal, text):
    return sha256(json.dumps([request,journal,text],sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def coverage_request(task, fallback=''):
    """Use the same accepted request at generation and evaluation boundaries."""
    if task.get('assignment_scope') or task.get('assignment_constraints'):
        assigned=task.get('proposed_objective') or task.get('message') or fallback
        return json.dumps({'human_request':assigned if task.get('goal_id') else task.get('original_request') or task.get('message') or fallback,
            'accepted_objective':task.get('proposed_objective') or '',
            'scope':task.get('assignment_scope') or '', 'constraints':task.get('assignment_constraints') or []},
            ensure_ascii=False,sort_keys=True)
    if task.get('goal_id'):
        return task.get('proposed_objective') or task.get('message') or fallback
    original=task.get('original_request') or task.get('message') or fallback
    objective=task.get('proposed_objective') or ''
    context=task.get('proposed_context_summary') or ''
    if not context and (not objective or objective==original):return original
    return json.dumps({'human_request':original,'accepted_objective':objective,
        'reference_context':context,'reference_context_role':'reference_resolution_only_not_evidence_or_permission'},
        ensure_ascii=False,sort_keys=True)


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
        if checked['task_id'] == task['task_id'] and task.get('worker_report_binding') == result_binding(task,checked):
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
    compact = payload.get('_result_contract')==3
    if compact:
        refs=[c['step_id'] for c in operations if isinstance(c.get('step_id'),str)]
        if not operations and task.get('execution_scope')=='analysis' and isinstance(terminal.get('step_id'),str):
            refs=[terminal['step_id']]  # The analytical text itself is the output, not an external action receipt.
    text = task.get('answer') or payload.get('text') or payload.get('answer') or payload.get('message') or ''
    clarification = next((c for c in reversed(commands) if c.get('tool_name')=='answer.ask_clarification'),None)
    artifacts = []
    final_receipts = []
    def same_artifact(left, right):
        def key(item):
            path = item["path"]
            return (item["device_id"], PureWindowsPath(path).as_posix().casefold()
                    if PureWindowsPath(path).drive else path)
        return key(left) == key(right)
    def record_artifact(artifact, native=False, verify_format=True):
        # The latest operation owns the path; an older receipt must not survive
        # a subsequent change, even when that change lacks native verification.
        artifacts[:] = [a for a in artifacts if not same_artifact(a, artifact)]
        final_receipts[:] = [a for a in final_receipts if not same_artifact(a, artifact)]
        if native and (not isinstance(artifact.get('sha256'),str) or not re.fullmatch('[a-fA-F0-9]{64}',artifact['sha256'])):
            native=False
            artifact['verified']=False
        suffix=PureWindowsPath(artifact['path']).suffix.lower()
        if native and verify_format and suffix in {'.docx','.xlsx','.pptx'}:
            format_proved=(artifact.get('structure')=='office_zip_xml' or
                (artifact.get('format')==suffix[1:] and artifact.get('artifact_role')=='final'))
            if not format_proved or type(artifact.get('size_bytes')) is not int or artifact['size_bytes']<=0:
                native=False
                artifact['verified']=False
        artifacts.append(artifact)
        if native:
            final_receipts.append(artifact)
    allowed_devices={_short_did(d) for d in task.get("device_ids") or []} or {"server"}
    for entry in evidence:
        result = entry["result"];tool = entry.get("tool_name") or entry.get("action")
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
                transferred = tool == "transfer_file" and result.get("sha256_verified") is True
                if transferred:
                    artifact.update(size_bytes=result.get("bytes_transferred"),sha256=result.get("sha256"))
                    artifact['verification_scope']='copy_identity'
                record_artifact(artifact, native=transferred or (tool == "write_content"
                    and result.get("verified") is True and result.get("committed") is True),verify_format=not transferred)
    expected = [item.strip() for item in task.get("assigned_artifacts") or []
                if isinstance(item, str) and item.strip()]
    user_files = [a for a in final_receipts if "/scripts/helpers/" not in a["path"].replace("\\","/").casefold()
                  or any(e.replace("\\","/").split("/")[-1].casefold()==a["name"].casefold() for e in expected)]
    # A receipt satisfies exactly one assignment. Binding is deterministic:
    # exact name/path equality, then a single remaining candidate for a single
    # remaining requirement. Anything else stays unmatched with a reason; a
    # descriptive label is never guessed into a file by substring search.
    def _key(value):
        return PureWindowsPath(str(value or "").replace("\\", "/")).as_posix().casefold()

    remaining = list(user_files)
    matched, unmatched, pending = [], [], []
    for label in expected:
        target = _key(label)
        hit = next((a for a in remaining
                    if _key(a.get("path")) == target or _key(a.get("name")) == target), None)
        if hit is None:
            pending.append(label)
            continue
        matched.append(hit)
        remaining.remove(hit)
    if (len(pending)==1 and len(remaining)==1 and not PureWindowsPath(pending[0]).drive
            and not PureWindowsPath(pending[0]).suffix and '/' not in pending[0] and '\\' not in pending[0]):
        # One requirement, one verified candidate: the binding is unambiguous.
        matched.append(remaining.pop())
        pending = []
    for label in pending:
        unmatched.append({"label": label,
                          "reason": ("ambiguous_artifact" if len(remaining) > 1 else
                                     "artifact_missing" if not user_files else
                                     "no_matching_artifact")})
    artifact_incomplete = bool(unmatched)
    useful = bool(artifacts) or any(isinstance(c.get('result'),dict) and bool(c['result'].get('stdout')) for c in operations)
    # Meaning and completeness of an untyped result belong to Worker. Its
    # terminal response is considered alongside the declared required artifacts,
    # effects, devices and actual tool outcomes, never as a substitute for them.
    worker_finished=bool(terminal and terminal.get('tool_name') in {'answer.text','answer_text'}
        and isinstance(payload.get('text'),str) and payload['text'].strip()
        and not payload.get('unresolved') and (compact or not payload.get('remaining'))
        and payload.get('answer_type') not in {'partial_report','error_report','failure','clarification'})
    try:
        from .tool_registry import canonical_tool_name
        from .api_support import is_recovery_observation
    except ImportError:
        from tool_registry import canonical_tool_name
        from api_support import is_recovery_observation
    targets={_short_did(d) for d in task.get('device_ids') or []}
    # The last confirmed version owns a file. An undo invalidates the old edit.
    try:
        from .run_journal import file_state_history
    except ImportError:
        from run_journal import file_state_history
    def file_key(item):
        path = item['path']
        return (_short_did(item.get('device_id') or ''),
                PureWindowsPath(path).as_posix().casefold() if PureWindowsPath(path).drive else path)
    rolled_back = set()
    for history in file_state_history(commands).values():
        if history[0].get('previous_sha256') and history[-1]['sha256']==history[0]['previous_sha256']:
            rolled_back.add(file_key(history[-1]))
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
                if any(file_key(a)==key and a.get('sha256')!=digest for a in matched):invalidated_files.add(key)
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
    required_invalid = any(file_key(a) in invalidated_files for a in matched)
    for artifact in artifacts:
        if file_key(artifact) in invalidated_files:
            artifact['verified']=False
    claimed_paths = [p for kind in ('changed','created') for p in (payload.get('effects') or {}).get(kind,[])]
    def matches_claim(path):
        normalized=path.replace('\\','/').casefold()
        candidates=[a for a in final_receipts if a['path'].replace('\\','/').casefold()==normalized
                    or '/' not in normalized and a['name'].casefold()==normalized]
        return len(candidates)==1 and file_key(candidates[0]) not in rolled_back
    unconfirmed_claims = bool(expected) and any(not matches_claim(path) for path in claimed_paths
        if any(_report_path(path)==_report_path(a.get('path')) or PureWindowsPath(path).name==label for a in matched for label in expected))
    file_contract = bool(expected) and not artifact_incomplete and all(
        file_key(a) not in rolled_back and file_key(a) not in invalidated_files for a in matched)
    def content_observed(artifact):
        # Content must be read at the current version. A create/format receipt,
        # an old inspection or a model's own prose is not a content check.
        # This is observation provenance, not independent semantic approval.
        pages=[]
        for entry in operations:
            if operation_facts(entry)!='success':continue
            result=entry.get('result') or {}
            if _short_did(entry.get('target_device_id') or entry.get('device_id') or '')!=artifact['device_id']:continue
            tool=entry.get('tool_name') or entry.get('action')
            if (result.get('verified') is True and result.get('committed') is True
                    and result.get('path')==artifact['path'] and result.get('file_sha256')==artifact['sha256']):
                return True
            for observation in result.get('artifact_observations') or []:
                if (isinstance(observation,dict) and observation.get('status')=='success'
                        and observation.get('path')==artifact['path'] and observation.get('sha256')==artifact['sha256']
                        and observation.get('items')):
                    pages.append(observation)
            if tool in {'office_docx','office_xlsx','office_pptx'}:
                if (result.get('operation_verified') is True and result.get('action')=='create'
                        and isinstance((result.get('applied_input') or {}).get('data'),dict)
                        and result.get('path')==artifact['path'] and result.get('sha256')==artifact['sha256']):
                    return True  # Native roundtrip checked this complete bounded input, not just the first inspect page.
                if ((result.get('action')=='inspect' or result.get('operation_verified') is True) and result.get('items')
                        and result.get('path')==artifact['path'] and result.get('sha256')==artifact['sha256']):
                    if 'total_items' not in result:
                        # Compatibility with older inspectors: no pagination claim.
                        return True
                    pages.append(result)
            if tool in {'execute_cmd','read_file','get_file_content'}:
                if (tool=='execute_cmd' and PureWindowsPath(artifact['path']).suffix.lower()
                        in {'.docx','.xlsx','.pptx'}):
                    # A verified format/hash plus arbitrary stdout is not a
                    # content read. Only the Agent's actual observations above
                    # can contribute Office coverage, with full pagination.
                    continue
                if not (result.get('stdout') or result.get('content') or result.get('text')):continue
                candidates=[item for item in result.get('artifact_receipts') or [] if isinstance(item,dict)
                    and item.get('verified') is True and item.get('source_task_id')==task['task_id']
                    and item.get('device_id')==artifact['device_id']]
                candidates.append({'path':result.get('path'),'device_id':artifact['device_id'],
                    'sha256':result.get('file_sha256') or result.get('sha256')})
                if any(isinstance(item,dict) and item.get('path')==artifact['path']
                       and item.get('sha256')==artifact['sha256'] for item in candidates):return True
        if pages:
            total=pages[0].get('total_items')
            if type(total) is not int or total<1:return False
            cursor=0
            # Re-reading the same page does not add coverage or invalidate it.
            if any(type(page.get('offset')) is not int or page['offset']<0 for page in pages):return False
            offsets={page['offset']:page for page in pages}
            for page in sorted(offsets.values(),key=lambda p:p['offset']):
                end=page['offset']+len(page['items'])
                if (page.get('total_items')!=total or page['offset']!=cursor or end>total
                        or page.get('next_offset')!=(end if end<total else None)):return False
                cursor=end
            return cursor==total
        return False
    for artifact in matched:
        # Byte integrity is executor-owned. Content observation describes what
        # the tool actually returned; it is NOT independent semantic coverage.
        artifact['content_observed']=content_observed(artifact)
        artifact['content_verified']=artifact['content_observed']
        artifact.setdefault('verification_scope','file_identity_and_format'
            if PureWindowsPath(artifact['path']).suffix.lower() in {'.docx','.xlsx','.pptx'} else 'file_identity')
    expected_effects=task.get('window_effects') or []
    def requested_window_step(entry):
        tool=canonical_tool_name(entry.get('tool_name') or entry.get('action') or '')
        action=(entry.get('result') or {}).get('action') or (entry.get('window_request') or {}).get('action')
        return (confirmed_window_effect(entry) is not None or tool in {'app.launch','app.open_url'} or tool=='window.control'
            and (action not in {'list','find','active','monitors'} or action in expected_effects))
    window_steps = [c for c in operations if requested_window_step(c)]
    def window_effect(entry):
        tool=canonical_tool_name(entry.get('tool_name') or entry.get('action') or '')
        return confirmed_window_effect(entry) or ('open' if tool in {'app.launch','app.open_url'} else entry.get('result',{}).get('action'))
    final_window_steps=[c for target in targets for c in [s for s in window_steps
        if _short_did(s.get('target_device_id') or s.get('device_id') or '')==target][-len(expected_effects):]] if expected_effects else []
    window_contract = (bool(task.get('window_effects'))
        and bool(targets) and all([window_effect(c) for c in final_window_steps
            if _short_did(c.get('target_device_id') or c.get('device_id') or '')==target] == task.get('window_effects') for target in targets)
        and all(operation_facts(c)=='success' and confirmed_window_effect(c)==window_effect(c) for c in final_window_steps))
    if window_contract and expected_effects.count('open'):
        # An effect name says nothing about which object was opened. The
        # requirement comes from the accepted Assignment; the proof comes from
        # the agent's own observation of that object's real path.
        required_objects=[item for item in (task.get('open_objects') or [])
                          if isinstance(item,dict) and item.get('path')]
        if required_objects and {str(item.get('type')) for item in required_objects} & {'file','folder'}:
            proven=[]
            for step in final_window_steps:
                if confirmed_window_effect(step)!='open':
                    continue
                result=step.get('result') or {}
                observed=result.get('observed_object') or {}
                device=_short_did(step.get('target_device_id') or step.get('device_id') or '')
                if observed:
                    proven.append((_report_path(observed.get('observed_path')),device))
                else:
                    # Legacy launch receipts attest the actual command/argv, not
                    # object-observation fields that this Agent never advertised.
                    candidates=[result.get('command'),*(result.get('args') or [])]
                    proven.extend((_report_path(path),device) for path in candidates if isinstance(path,str))
            remaining=list(proven)
            for required in required_objects:
                wanted=_report_path(required.get('path'))
                device=required.get('device_id')
                index=next((position for position,(path,step_device) in enumerate(remaining)
                            if path==wanted and (not device or device==step_device)), None)
                if index is None:
                    window_contract=False
                    break
                # One observation satisfies one requirement: the same window
                # cannot stand for two demanded objects.
                remaining.pop(index)
    # An untyped CLI operation can implement several effects. Only Worker can
    # interpret that mapping; never turn rc=0 alone into a window postcondition.
    # A later failed native action cannot be hidden by an older CLI result.
    if expected_effects and worker_finished:
        last_native=max((i for i,c in enumerate(operations) if requested_window_step(c)),default=-1)
        cli=[c for i,c in enumerate(operations) if i>last_native
             and canonical_tool_name(c.get('tool_name') or c.get('action') or '')=='execute_cmd'
             and operation_facts(c)=='success']
        cli_targets={_short_did(c.get('target_device_id') or c.get('device_id') or '') for c in cli}
        if targets and targets.issubset(cli_targets):window_contract=True
    covered_targets={a['device_id'] for a in matched}
    for c in operations:
        result=c.get('result') or {}
        if (c.get('tool_name')=='transfer_file' and operation_facts(c)=='success'
                and result.get('sha256_verified') is True and isinstance(result.get('sha256'),str)
                and re.fullmatch('[a-fA-F0-9]{64}',result['sha256'])):
            covered_targets.update(_short_did(result.get(k) or '') for k in ('source_device','target_device'))
    typed_contract = (bool(expected or task.get('window_effects'))
        and (not expected or file_contract) and (not task.get('window_effects') or window_contract)
        and (len(targets)<2 or window_contract or targets.issubset(covered_targets)))
    observed_targets={_short_did(c.get('target_device_id') or c.get('device_id') or '')
                      for c in operations if operation_facts(c)=='success'}
    def observation(entry):
        result=entry.get('result') if isinstance(entry.get('result'),dict) else {}
        params={'command':entry.get('command'),'action':result.get('action') or (entry.get('window_request') or {}).get('action')}
        return is_recovery_observation(entry.get('tool_name') or entry.get('action') or '',params)
    substantial_unknown=any(operation_facts(c)=='unknown' and not observation(c) for c in operations)
    required_complete=(not expected or file_contract) and (not expected_effects or window_contract)
    effects=[c for c in operations if not observation(c)]
    last_effect_ok=not effects or operation_facts(effects[-1])=='success'
    worker_complete=(worker_finished and required_complete and not required_invalid and last_effect_ok
        and (any(operation_facts(c)=='success' for c in operations)
             or (not operations and not targets and task.get('execution_scope')=='analysis' and bool(refs)))
        and (not targets or targets.issubset(observed_targets | covered_targets)))
    if task.get('cancel_requested') or raw=='cancelled': status='cancelled'
    elif raw in {'queued','waiting_dependency','running','waiting_input','waiting_confirmation'}: status=raw
    elif raw=='confirm': status='waiting_confirmation'
    elif task.get('_report_integrity_failed'): status='unknown' if any(not observation(c) for c in operations) else 'failed'
    elif substantial_unknown or task.get('_action_outcome_unknown') or (raw in {'unknown','interrupted'} and not typed_contract): status='unknown'
    elif required_invalid: status='partial' if evidence else 'failed'
    elif clarification or raw=='blocked': status='blocked'
    elif operations and all((c.get('result') or {}).get('status')=='blocked' for c in operations): status='blocked'
    elif terminal.get('tool_name') in {'answer.report_failure','answer_report_failure'}: status='partial' if evidence else 'failed'
    elif (typed_contract and task.get('execution_scope')!='analysis') or worker_complete: status='success'
    elif operations and all(operation_facts(c) in {'failed','not_executed'} for c in operations): status='failed'
    elif useful or any(operation_facts(c) in {'success','partial'} for c in operations): status='partial'
    else: status='failed'
    unresolved_items=[str(v) for v in (payload.get('unresolved') or [])]
    # v3 unresolved is substantive unfinished Assignment work; optional advice
    # belongs in remaining. Do not silently discard it by filename heuristics.
    if (unresolved_items or (not compact and payload.get('remaining')) or unconfirmed_claims) and status=='success': status='partial'
    if task.get('_legacy_read') and status=='success' and raw in {'partial','failed','error','blocked','cancelled'}:
        status='failed' if raw=='error' else raw
    required_rollback = any(file_key(a) in rolled_back for a in matched)
    if rolled_back and status=='success' and required_rollback: status='partial'
    clarification_text = ''
    clarification_mismatch=False
    if clarification:
        question_payload = clarification.get('result') or {}
        question = question_payload.get('question') or ''
        options = question_payload.get('options') or []
        clarification_text = question + ('\n\n' + '\n'.join(f'{i}. {value}' for i,value in enumerate(options,1)) if options else '')
        if text and text != question:
            clarification_mismatch=True
            status = 'blocked'

    summaries = {"success":"Задача выполнена по подтверждённым результатам.", "partial":"Задача выполнена частично.",
        "blocked":"Выполнение заблокировано.", "failed":"Задача завершилась с ошибкой.", "cancelled":"Задача отменена.",
        "unknown":"Исход задачи не подтверждён; автоматически её не повторяю.", "queued":"Задача ожидает освобождения Worker.",
        "waiting_dependency":"Задача ожидает подтверждённого результата другой задачи.",
        "waiting_input":"Задача ожидает выбора окна.", "running":"Задача выполняется.", "waiting_confirmation":"Задача ожидает решения пользователя."}
    error_code=(task.get("worker_error_code") or receipt.get("terminal_reason")) if status!="success" else None
    if artifact_incomplete:
        error_code = ("artifact_match_ambiguous"
                      if any(item.get("reason") == "ambiguous_artifact" for item in unmatched)
                      else "artifact_receipt_missing")
    if not isinstance(error_code, str):error_code=None
    if status=='partial' and not error_code and useful and any(operation_facts(c)=='failed' for c in operations):
        error_code='mixed_diagnostic_evidence'
    if clarification and status=='blocked':error_code='clarification_required'
    if clarification_text:
        if clarification_mismatch:error_code='clarification_binding_mismatch'
        text=clarification_text

    free = free_form_reports()
    if not free and status=='failed' and operations:
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'execution_failed')['answer']
    elif not free and status in {'partial','unknown'} and len(task.get('device_ids') or [])>1:
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'multi_device_incomplete')['answer']
    if not free and artifact_incomplete and status in {'partial','unknown','failed'}:
        missing=[item["label"] for item in unmatched] or list(expected)
        text='Обязательные файловые результаты не подтверждены: '+', '.join(missing)+'.\n\n' + (
            'Сохранённый текст результата — неподтверждённый исходный материал:\n'+text if text else '')

    if status in {'partial','failed','unknown'} and not error_code:
        error_code = 'goal_not_completed' if terminal else 'terminal_answer_missing'
    if required_rollback:
        error_code = 'required_file_change_reverted'
        if not free:
            text = 'Запрошенное изменение не сохранилось: последняя подтверждённая версия файла совпадает с исходной.\n' + '\n'.join(
                f"{a['path']}: SHA-256 {a['sha256']}" for a in matched
                if file_key(a) in rolled_back)
    if required_invalid:
        error_code='artifact_postcondition_unconfirmed'
        if not free:
            text='Последнее состояние обязательного файла не подтверждено. Предыдущий receipt не подтверждает состояние после последней попытки.\n'
            text+='\n'.join(a['path'] for a in matched if file_key(a) in invalidated_files)
    if task.get('_report_integrity_failed') and status in {'unknown','failed'}:
        error_code='worker_report_binding_mismatch'
        if not free:
            try:
                from .run_journal import unfinished_execution_result
            except ImportError:
                from run_journal import unfinished_execution_result
            text=unfinished_execution_result(commands,error_code)['answer']
    if not free and status=='success' and (not terminal or payload.get('text')!=text):
        # Completion belongs to verified effects, not to whether another model
        # managed to phrase a final answer. Never display an unbound candidate
        # as the authoritative explanation of an otherwise verified result.
        lines=['Обязательные результаты подтверждены по журналу исполнения.']
        lines.extend(f"{a['path']}: SHA-256 {a['sha256']}" for a in matched)
        if window_contract:lines.append('Подтверждённые действия с окнами: '+', '.join(task['window_effects'])+'.')
        failed=[c.get('step_id','без step_id') for c in operations if operation_facts(c) in {'failed','not_executed'}]
        if failed:lines.append('Ошибки дополнительных шагов сохранены в журнале: '+', '.join(failed)+'.')
        lines.append('Финальный текст модели не подтверждён; приведены факты исполнения.')
        text='\n'.join(lines)
    if not free and status in {'partial','unknown'} and re.match(
            r'^\s*(?:[#*>]+\s*)?(?:\*\*)?(?:готово|всё\s+(?:выполнено|готово)|задача\s+выполнена|done|all\s+done)\b',
            text,re.IGNORECASE):
        # Keep the original terminal in execution_details, not as the final
        # verdict. Do not attempt to rewrite arbitrary success claims as facts.
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'goal_not_completed')['answer']
    if not free and compact and payload.get('unresolved') and status not in {'success','cancelled'}:
        text+='\n\nСущественно не завершено:\n'+'\n'.join('- '+value for value in payload['unresolved'])
    if free:
        # The model narrates the work. The facts below stay authoritative in the
        # report fields; nothing here rewrites or prefaces the model's words.
        facts=[f"{a['path']} · SHA-256 {a['sha256']}" for a in matched if isinstance(a.get('sha256'),str)]
        if not text.strip():
            text='\n'.join(facts) if facts else summaries[status]
        elif facts and all(fact not in text for fact in facts):
            text=text.rstrip()+'\n\n'+'\n'.join(facts)
    summary = bounded_report_summary(text) if text else summaries[status]
    report = {"schema_version":1, "task_id":task["task_id"], "worker_id":task.get("worker_id") or "worker-1", "status":status,
        "goal_completed":status == "success", "summary":summary,
        "target_device_ids":[_short_did(d) for d in task.get("device_ids") or []], "artifacts":artifacts,
        "evidence_refs":refs, "requires_user_action":status in {"waiting_confirmation","waiting_input","blocked"},
        "error_code":error_code}
    return WorkerReport.model_validate(report).model_dump()
