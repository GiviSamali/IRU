"""Worker Report v1 is a projection of existing receipts and evidence, not LLM prose."""
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
    from .tool_completion import normalize_operation_result
except ImportError:
    from tool_completion import normalize_operation_result

TERMINAL = {"done", "completed", "completed_with_recovery", "failed", "error", "cancelled", "blocked", "partial", "unknown", "interrupted", "success"}


def operation_facts(command):
    tool = command.get('tool_name') or command.get('action') or ''
    outcome = normalize_operation_result(tool, command.get('result'))
    return 'unknown' if command.get('status')=='unknown' and outcome!='not_executed' else outcome


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


def needs_semantic_assessment(task):
    # A file receipt proves bytes/format, not coverage of requested content.
    # Reuse the existing bound observation; it never replaces native effect proof.
    return bool(task.get('assigned_artifacts')) or (
        task.get('execution_scope')=='analysis' and not task.get('window_effects'))


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
            if checked['status']=='success' and task.get('execution_scope')=='analysis' and any(
                    c.get('tool_name') in {'answer.text','answer_text'} and
                    (c.get('result') or {}).get('_result_contract')==3 for c in task.get('commands') or []):
                # Apply the same coverage rule to snapshots produced by the
                # former v3 shortcut. Reading never invokes another model.
                return build_worker_report(task)
            # Older bound snapshots certified only file identity/format. Reuse
            # the one builder to apply content coverage; never call a model on read.
            if (checked['status']=='success' and task.get('assigned_artifacts')
                    and checked['artifacts'] and all('content_verified' not in a
                        and a.get('verification_scope')!='copy_identity' for a in checked['artifacts'])):
                return build_worker_report(task)
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
    evidence = [c for c in commands if c.get('tool_type') not in {'system','answer'} and operation_facts(c) in {'success','partial'}]
    terminal = next((c for c in reversed(commands) if c.get('tool_name') in {'answer.text','answer_text'} and c.get('status')=='terminal'),{})
    payload = terminal.get('result') if isinstance(terminal.get('result'),dict) else {}
    compact = payload.get('_result_contract')==3
    if compact:
        refs=[c['step_id'] for c in commands if isinstance(c.get('step_id'),str)
            and c.get('tool_type') not in {'system','answer'} and not str(c.get('tool_name','')).startswith('answer')]
    text = task.get('answer') or payload.get('text') or payload.get('answer') or ''
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
                    "verified":True,"source_task_id":entry.get("task_id") or task["task_id"],
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
    if len(pending) == 1 and len(remaining) == 1:
        # One requirement, one verified candidate: the binding is unambiguous.
        matched.append(remaining.pop())
        pending = []
    for label in pending:
        unmatched.append({"label": label,
                          "reason": ("ambiguous_artifact" if len(remaining) > 1 else
                                     "artifact_missing" if not user_files else
                                     "no_matching_artifact")})
    artifact_incomplete = bool(unmatched)
    operations = [c for c in commands if c.get('tool_type') not in {'system','answer'}
                  and not str(c.get('tool_name','')).startswith('answer')]
    useful = bool(artifacts) or any(isinstance(c.get('result'),dict) and bool(c['result'].get('stdout')) for c in operations)
    request = coverage_request(task)
    assessment = payload.get('_coverage') or {}
    bound = assessment.get('binding') == coverage_binding(request, commands[:commands.index(terminal)] if terminal else commands, text)
    complete_analysis = (bound and assessment.get('coverage')=='complete'
                         and set(assessment.get('evidence_refs') or []).issubset(set(refs)))
    complete_coverage = complete_analysis
    # Coverage describes an analysis, not a postcondition of arbitrary code.
    # Opaque commands and mutations need an accepted, mechanically verified
    # effect contract. A model cannot opt out of that requirement.
    try:
        from .tool_registry import canonical_tool_name
    except ImportError:
        from tool_registry import canonical_tool_name
    analysis_tools = {'web_search','web.tabs','web.read','web.elements','web.wait',
        'read_file','list_dir','get_file_content','get_file_link','window.list',
        'window.find','window.verify','app.verify_launch','device.get_passport',
        'device.refresh_state','device.check_runtime','memory.list_facts',
        'memory.get_stats','system.list_tools','system.get_last_run_summary'}
    analysis_only = task.get('execution_scope')=='analysis' and all(operation_facts(c)=='success' for c in operations)
    observations = [c for c in operations if canonical_tool_name(c.get('tool_name') or c.get('action') or '') in analysis_tools
                    or (c.get('tool_name')=='execute_cmd' and isinstance(c.get('result',{}).get('stdout'),str)
                        and c['result']['stdout'].strip())]
    referenced_observation = not operations or any(c.get('step_id') in assessment.get('evidence_refs',[]) for c in observations)
    targets={_short_did(d) for d in task.get('device_ids') or []}
    observed_targets={_short_did(c.get('target_device_id') or c.get('device_id') or '') for c in observations
                      if c.get('step_id') in assessment.get('evidence_refs',[])}
    complete_analysis = (complete_analysis and analysis_only and referenced_observation and targets.issubset(observed_targets)
                         and not expected and not task.get('window_effects'))
    if compact and task.get('execution_scope')=='analysis' and not expected and not task.get('window_effects'):
        current_observations=[c for c in observations if operation_facts(c)=='success']
        current_targets={_short_did(c.get('target_device_id') or c.get('device_id') or '') for c in current_observations}
        complete_analysis=(complete_coverage and bool(current_observations)
            and any(c.get('step_id') in assessment.get('evidence_refs',[]) for c in current_observations)
            and targets.issubset(observed_targets))
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
    unconfirmed_claims = any(not matches_claim(path) for path in claimed_paths)
    file_contract = bool(expected) and not artifact_incomplete and all(
        file_key(a) not in rolled_back and file_key(a) not in invalidated_files for a in matched)
    def content_observed(artifact):
        # Content must be read at the current version. A create/format receipt,
        # an old inspection or a model's own prose is not a content check.
        for entry in operations:
            if entry.get('step_id') not in assessment.get('evidence_refs', []):continue
            if operation_facts(entry)!='success':continue
            result=entry.get('result') or {}
            if _short_did(entry.get('target_device_id') or entry.get('device_id') or '')!=artifact['device_id']:continue
            tool=entry.get('tool_name') or entry.get('action')
            if tool in {'office_docx','office_xlsx','office_pptx'}:
                if (result.get('action')=='inspect' and result.get('items')
                        and result.get('path')==artifact['path'] and result.get('sha256')==artifact['sha256']):return True
            if tool in {'execute_cmd','read_file','get_file_content'}:
                if not (result.get('stdout') or result.get('content') or result.get('text')):continue
                candidates=[item for item in result.get('artifact_receipts') or [] if isinstance(item,dict)
                    and item.get('verified') is True and item.get('source_task_id')==task['task_id']
                    and item.get('device_id')==artifact['device_id']]
                candidates.append({'path':result.get('path'),'device_id':artifact['device_id'],
                    'sha256':result.get('file_sha256') or result.get('sha256')})
                if any(isinstance(item,dict) and item.get('path')==artifact['path']
                       and item.get('sha256')==artifact['sha256'] for item in candidates):return True
        return False
    content_unconfirmed = False
    for artifact in matched:
        if artifact.get('verification_scope')=='copy_identity':continue
        artifact['verification_scope']=('file_identity_and_format' if PureWindowsPath(artifact['path']).suffix.lower()
            in {'.docx','.xlsx','.pptx'} else 'file_identity')
        artifact['content_verified']=bool(complete_coverage and content_observed(artifact))
        content_unconfirmed = content_unconfirmed or not artifact['content_verified']
    if content_unconfirmed:file_contract=False
    window_steps = [c for c in operations if canonical_tool_name(c.get('tool_name') or c.get('action') or '')
                    in {'window.control','app.launch','app.open_url'}]
    def window_effect(entry):
        tool=canonical_tool_name(entry.get('tool_name') or entry.get('action') or '')
        return 'open' if tool in {'app.launch','app.open_url'} else entry.get('result',{}).get('action')
    expected_effects=task.get('window_effects') or []
    final_window_steps=[c for target in targets for c in [s for s in window_steps
        if _short_did(s.get('target_device_id') or s.get('device_id') or '')==target][-len(expected_effects):]] if expected_effects else []
    window_contract = (task.get('execution_scope')=='window' and bool(task.get('window_effects'))
        and bool(targets) and all([window_effect(c) for c in final_window_steps
            if _short_did(c.get('target_device_id') or c.get('device_id') or '')==target] == task.get('window_effects') for target in targets)
        and all(operation_facts(c)=='success' and c.get('result',{}).get('completion_state')=='success' for c in final_window_steps))
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
    substantial_unknown = any(operation_facts(c)=='unknown' and (c.get('tool_name') or c.get('action')) not in
        {'web_search','system_list_tools','system_get_last_run_summary'} for c in operations)
    if task.get('cancel_requested') or raw=='cancelled': status='cancelled'
    elif raw in {'queued','waiting_dependency','running','waiting_input','waiting_confirmation'}: status=raw
    elif raw=='confirm': status='waiting_confirmation'
    elif task.get('_report_integrity_failed'): status='unknown'
    elif substantial_unknown or required_invalid or task.get('_action_outcome_unknown') or raw in {'unknown','interrupted'}: status='unknown'
    elif clarification or raw=='blocked': status='blocked'
    elif typed_contract or (complete_analysis and not artifact_incomplete): status='success'
    elif operations and all(operation_facts(c) in {'failed','not_executed'} for c in operations): status='failed'
    elif useful or any(operation_facts(c) in {'success','partial'} for c in operations): status='partial'
    else: status='unknown'
    if (payload.get('unresolved') or (not compact and payload.get('remaining')) or unconfirmed_claims) and status=='success': status='partial'
    if task.get('_legacy_read') and status=='success' and raw in {'partial','failed','error','blocked','cancelled'}:
        status='failed' if raw=='error' else raw
    required_rollback = any(file_key(a) in rolled_back for a in matched)
    if rolled_back and status=='success' and (required_rollback or not complete_analysis): status='partial'
    clarification_text = ''
    if clarification:
        question_payload = clarification.get('result') or {}
        question = question_payload.get('question') or ''
        options = question_payload.get('options') or []
        clarification_text = question + ('\n\n' + '\n'.join(f'{i}. {value}' for i,value in enumerate(options,1)) if options else '')
        if text and text != question:
            status = 'unknown'

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
        if status=='unknown':error_code='clarification_binding_mismatch'
        text=clarification_text

    if status=='failed' and operations:
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'execution_failed')['answer']
    elif status in {'partial','unknown'} and len(task.get('device_ids') or [])>1:
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'multi_device_incomplete')['answer']
    if artifact_incomplete and status in {'partial','unknown','failed'}:
        missing=[item["label"] for item in unmatched] or list(expected)
        text='Обязательные файловые результаты не подтверждены: '+', '.join(missing)+'.\n\n' + (
            'Сохранённый текст результата — неподтверждённый исходный материал:\n'+text if text else '')

    if status in {'partial','failed','unknown'} and not error_code:
        error_code = 'goal_not_completed' if terminal else 'terminal_answer_missing'
    if required_rollback:
        error_code = 'required_file_change_reverted'
        # Preserve the model's original words in the journal, but never project
        # a superseded success claim as the final result.
        text = 'Запрошенное изменение не сохранилось: последняя подтверждённая версия файла совпадает с исходной.\n' + '\n'.join(
            f"{a['path']}: SHA-256 {a['sha256']}" for a in matched
            if file_key(a) in rolled_back)
    if required_invalid:
        error_code='artifact_postcondition_unconfirmed'
        text='Последнее состояние обязательного файла не подтверждено. Предыдущий receipt не подтверждает состояние после последней попытки.\n'
        text+='\n'.join(a['path'] for a in matched if file_key(a) in invalidated_files)
    if content_unconfirmed and not (required_rollback or required_invalid) and status=='partial':
        error_code='artifact_content_unconfirmed'
        text='Файл записан; подтверждена его целостность. Полнота запрошенного содержания не подтверждена.\n'
        text+='\n'.join(f"{a['path']}: SHA-256 {a['sha256']}" for a in matched if a.get('content_verified') is False)
        if bound and assessment.get('reason'):text+='\nПроверка содержания: '+str(assessment['reason'])
    if task.get('_report_integrity_failed') and status=='unknown':
        error_code='worker_report_binding_mismatch'
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code)['answer']
    if status=='success' and (not terminal or payload.get('text')!=text):
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
    if status in {'partial','unknown'} and re.match(
            r'^\s*(?:[#*>]+\s*)?(?:\*\*)?(?:готово|всё\s+(?:выполнено|готово)|задача\s+выполнена|done|all\s+done)\b',
            text,re.IGNORECASE):
        # Keep the original terminal in execution_details, not as the final
        # verdict. Do not attempt to rewrite arbitrary success claims as facts.
        try:
            from .run_journal import unfinished_execution_result
        except ImportError:
            from run_journal import unfinished_execution_result
        text=unfinished_execution_result(commands,error_code or 'goal_not_completed')['answer']
    if compact and payload.get('unresolved') and status not in {'success','cancelled'}:
        text+='\n\nСущественно не завершено:\n'+'\n'.join('- '+value for value in payload['unresolved'])
    summary = bounded_report_summary(text) if text else summaries[status]
    report = {"schema_version":1, "task_id":task["task_id"], "worker_id":task.get("worker_id") or "worker-1", "status":status,
        "goal_completed":status == "success", "summary":summary,
        "target_device_ids":[_short_did(d) for d in task.get("device_ids") or []], "artifacts":artifacts,
        "evidence_refs":refs, "requires_user_action":status in {"waiting_confirmation","waiting_input","blocked"},
        "error_code":error_code}
    return WorkerReport.model_validate(report).model_dump()
